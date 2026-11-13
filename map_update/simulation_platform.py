"""Local scene review, real MetaDrive playback, and versioned planning handoff.

HTTP requests never supply filesystem paths or Python callbacks. A single
dedicated child owns Panda3D on its main thread, including on macOS. The web
review renderer and the actual simulator are explicitly separate view modes.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import mimetypes
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid


class BusyError(ValueError):
    pass


class EngineBroker:
    """Serialize engine mutations; a seek must finish before another begins."""
    FRAME_CACHE_LIMIT = 8
    FRAME_CACHE_BYTES = 24 * 1024 * 1024

    def __init__(self, repo_root, python=None, snapshot_provider=None):
        self.repo = Path(repo_root).resolve()
        self.python = str(python or sys.executable)
        self.directory = tempfile.TemporaryDirectory(prefix='map-review-engine-')
        # macOS may return /var/folders/... while resolve() uses
        # /private/var/folders/.... Compare canonical paths on both sides.
        self.root = Path(self.directory.name).resolve()
        self.lock = threading.Lock()
        self.completed = threading.Condition(self.lock)
        self.snapshot_provider = snapshot_provider
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='metadrive-session')
        self.process = None
        self.responses = queue.Queue()
        self.log = None
        self.closed = False
        self.frames = OrderedDict()
        self.frame_bytes = 0
        self.state = dict(busy=False, job_id=None, last_result=None, error=None,
                          scene_id=None, version=None, ready=False)

    def status(self, *, wait_ms=0, job_id=None):
        wait_ms = _wait_ms(wait_ms)
        with self.completed:
            deadline = time.monotonic() + wait_ms / 1000
            while (wait_ms and self.state['busy'] and not self.closed
                   and (job_id is None or self.state['job_id'] == job_id)):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.completed.wait(remaining)
            return json.loads(json.dumps(self.state))

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            self._stop_process()
        # A restarted worker numbers its frames from one. Old high-numbered
        # files would make its eight-file cleanup delete the new first frame.
        for path in self.root.glob('frame-*.png'):
            path.unlink(missing_ok=True)
        with self.lock:
            self.frames.clear()
            self.frame_bytes = 0
        self.responses = queue.Queue()
        self.log = (self.root / 'worker.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen(
            [self.python, '-u', str(self.repo / 'scripts/simulation_session_worker.py'),
             '--output-dir', str(self.root)], cwd=self.repo, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        process, responses = self.process, self.responses

        def read():
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except (ValueError, TypeError):
                    # Some native libraries print to fd 1; those are logs, not
                    # acknowledgements that a simulation command succeeded.
                    continue
                if isinstance(value, dict) and 'id' in value:
                    responses.put(value)
            responses.put({'worker_exited': True})
        threading.Thread(target=read, name='metadrive-protocol', daemon=True).start()

    def submit(self, command, *, scene_id=None, version=None, expected_scene_id=None, expected_version=None):
        with self.lock:
            if self.closed:
                raise ValueError('Simulation service is closed')
            if self.state['busy']:
                raise BusyError('上一项仿真操作仍在执行，请等待完成。')
            if expected_scene_id is not None and (self.state['scene_id'] != expected_scene_id
                                                  or self.state['version'] != expected_version):
                raise BusyError('仿真已被另一页面切换，请重新启动当前场景。')
            identifier = uuid.uuid4().hex
            self.state.update(busy=True, job_id=identifier, error=None, progress=None)
            if scene_id is not None:
                self.state.update(scene_id=scene_id, version=version, last_result=None, ready=False)
                self.frames.clear()
                self.frame_bytes = 0
        self.executor.submit(self._execute, identifier, dict(command))
        return {'job_id': identifier}

    def _execute(self, identifier, command):
        try:
            self._start()
            self.process.stdin.write(json.dumps({'id': identifier, **command}) + '\n')
            self.process.stdin.flush()
            deadline = time.monotonic() + 300
            while True:
                response = self.responses.get(timeout=max(.1, deadline-time.monotonic()))
                if response.get('worker_exited'):
                    raise RuntimeError('MetaDrive 进程已退出；请检查已激活的仿真环境和模型资源。')
                if response.get('id') != identifier:
                    continue
                if response.get('event') == 'progress':
                    with self.lock:
                        self.state['progress'] = response.get('result', response.get('progress'))
                    continue
                if not response.get('ok'):
                    error = response.get('error', 'MetaDrive operation failed')
                    raise RuntimeError(error if isinstance(error, str) else json.dumps(error, ensure_ascii=False))
                result = response.get('result', {})
                if command.get('command') == 'close':
                    with self.lock:
                        self.state.update(last_result=result, ready=False)
                        self.frames.clear()
                        self.frame_bytes = 0
                    self._stop_process()
                else:
                    self._publish_result(result)
                break
        except queue.Empty:
            self._stop_process()
            with self.lock:
                self.state.update(error='仿真操作超时，已关闭工作进程；可重新启动场景。', ready=False)
        except Exception as error:
            with self.lock:
                self.state.update(error=str(error), ready=False)
        finally:
            with self.completed:
                self.state['busy'] = False
                self.completed.notify_all()

    def _publish_result(self, result):
        """Bind immutable PNG bytes before advertising a completed frame.

        Browsers may fetch frame N after the worker has advanced to N+1. A
        bounded byte cache preserves that exact image independently of worker
        file cleanup. Opaque IDs cannot be reused after a worker restart.
        """
        result = dict(result)
        # Physical updates have their own identity even when no viewer needs
        # a PNG. Never identify a newer pose snapshot with an older image.
        result['state_id'] = uuid.uuid4().hex
        filename = result.get('frame') or result.get('frame_file') or result.get('frame_path')
        data = None
        result.pop('frame_id', None)
        if filename is not None:
            if not isinstance(filename, str):
                raise ValueError('Invalid simulation frame path')
            path = (self.root / filename).resolve()
            if not path.is_relative_to(self.root) or path.suffix.lower() != '.png':
                raise ValueError('Invalid simulation frame path')
            data = path.read_bytes()
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or not data.endswith(b'IEND\xaeB`\x82'):
                raise ValueError('MetaDrive 未生成完整 PNG 图像，请重启引擎查看渲染错误。')
            if len(data) > self.FRAME_CACHE_BYTES:
                raise ValueError('Rendered image exceeds the frame cache size limit')
            result['frame_id'] = uuid.uuid4().hex
        if self.snapshot_provider is not None and result.get('live_engine') is True:
            # Bundle the matching snapshot in the completion response. The
            # browser can decode the PNG without a second serial JSON request.
            with self.lock:
                scene_id, version = self.state['scene_id'], self.state['version']
            result['snapshot'] = self.snapshot_provider(result, scene_id, version)
        with self.lock:
            if data is not None:
                self.frames[result['frame_id']] = {
                    'scene_id': self.state['scene_id'], 'version': self.state['version'],
                    'sequence': result.get('sequence'), 'data': data,
                }
                self.frame_bytes += len(data)
                while (len(self.frames) > self.FRAME_CACHE_LIMIT
                       or self.frame_bytes > self.FRAME_CACHE_BYTES):
                    _, old = self.frames.popitem(last=False)
                    self.frame_bytes -= len(old['data'])
            self.state.update(last_result=result, ready=True)

    def frame(self, *, scene_id=None, version=None, sequence=None, frame_id=None):
        with self.lock:
            if scene_id is not None and (scene_id != self.state['scene_id'] or version != self.state['version']):
                raise ValueError('Requested frame belongs to a different scene or map version')
            if frame_id is not None:
                record = self.frames.get(frame_id)
            elif sequence is not None:
                raise ValueError('Frames are no longer addressable by sequence alone; frame_id is required')
            else:
                record = self.frames.get((self.state.get('last_result') or {}).get('frame_id'))
            if record is None:
                raise ValueError('Requested frame is no longer available; refresh the current engine frame')
            if record['scene_id'] != self.state['scene_id'] or record['version'] != self.state['version']:
                raise ValueError('Requested frame belongs to a different scene or map version')
            if sequence is not None and str(record['sequence']) != str(sequence):
                raise ValueError('Requested frame ID and sequence do not match')
            return record['data']

    def _stop_process(self):
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for handle in (process.stdin, process.stdout):
                if handle is not None:
                    handle.close()
        if self.log is not None:
            self.log.close()
        self.process = self.log = None

    def close(self):
        with self.completed:
            self.closed = True
            self.completed.notify_all()
        self._stop_process()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.directory.cleanup()


def _one(query, key, default):
    values = query.get(key, [default])
    if len(values) != 1:
        raise ValueError('Repeated query parameter: ' + key)
    return values[0]


def _step(query):
    value = _one(query, 'step', '0')
    if not value.isascii() or not value.isdecimal():
        raise ValueError('step must be a nonnegative integer')
    return int(value)


def _version(value):
    if value not in {'original', 'updated'}:
        raise ValueError('version must be original or updated')
    return value


def _wait_ms(value):
    if type(value) is not int or not 0 <= value <= 1000:
        raise ValueError('wait_ms must be an integer between 0 and 1000')
    return value


class PlatformServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, repo_root, *, engine_python=None, control_key=None, allowed_origins=()):
        self.control_key = control_key or None
        if address[0] not in {'127.0.0.1', 'localhost', '::1'} and (not self.control_key or len(self.control_key) < 16):
            raise ValueError('Public binding requires a control key of at least 16 characters')
        self.allowed_origins = set()
        for value in allowed_origins:
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
                raise ValueError('Allowed origins must be exact HTTP(S) origins')
            self.allowed_origins.add(parsed.scheme + '://' + parsed.netloc)
        super().__init__(address, PlatformHandler)
        self.repo = Path(repo_root).resolve()
        self.payloads = {}
        self.validations = {}
        self.snapshots = OrderedDict()
        self.cache_lock = threading.Lock()
        self.engine = EngineBroker(self.repo, engine_python, snapshot_provider=self.engine_snapshot)
        from .simulation_interactive_server import InteractiveCoordinator
        self.interactive = InteractiveCoordinator(self)

    def scene(self, key):
        from .simulation_platform_data import load_scene
        with self.cache_lock:
            if key not in self.payloads:
                self.payloads[key] = load_scene(self.repo, key)
            return self.payloads[key]

    def validation(self, key, version):
        from .simulation_platform_data import scene_database
        from .simulation_validation import validate_database
        with self.cache_lock:
            cache_key = (key, version)
            if cache_key not in self.validations:
                database = scene_database(self.repo, key, version)
                metrics = self.repo / 'demo/simulation/validation/detection_metrics.json'
                self.validations[cache_key] = validate_database(database, detection_metrics=metrics if metrics.exists() else None)
            return self.validations[cache_key]

    def engine_snapshot(self, result, key, version):
        from .simulation_platform_data import build_snapshot
        payload = self.scene(key)
        if (result.get('scene_id') != payload['scene_id'] or result.get('dataset') != payload['dataset']
                or result.get('map_features_sha256') != payload['versions'][version]['map_sha256']):
            raise ValueError('Engine frame and scene snapshot identities do not match')
        if result.get('interactive'):
            interactive = result['interactive']
            actors = [{'id': row.get('vehicle_id', row.get('native_object_id')), 'type': row['type'],
                       'is_ego': False, 'persistent': True, 'position': list(row['position_scenario_m']),
                       'heading': row['heading_rad'], 'length': row['length_m'],
                       'width': row['width_m'], 'height': row.get('height_m', 1.)}
                      for row in interactive.get('static_actors', [])]
            for identifier, row in interactive['vehicles'].items():
                actors.append({'id': identifier, 'type': 'VEHICLE', 'category': 'vehicle',
                    'is_ego': identifier == 'ego', 'persistent': False,
                    'position': list(row['position_scenario_m']), 'heading': row['heading_rad'],
                    'length': row['length_m'], 'width': row['width_m'], 'height': row.get('height_m', 1.5),
                    'velocity': row.get('velocity_m_s', [0., 0.]), 'interactive_vehicle': identifier})
            # This is a new physical experiment on the source map, not a
            # timestamp-indexed replay of the source actors. Review overlays
            # already hold their explicitly offline static prior in the scene.
            return {'schema': 'simulation-platform-snapshot-1.0', 'key': key,
                    'scene_id': payload['scene_id'], 'dataset': payload['dataset'],
                    'version': version, 'map_sha256': result['map_features_sha256'],
                    'step': result['step'], 'time_s': interactive.get('sim_time_s', result['step'] * .1),
                    'source_timestamp_ns': None, 'clock': 'interactive_simulation',
                    'actors': actors, 'ego': next(row for row in actors if row.get('is_ego')),
                    'states': {'events': [], 'objects': [], 'scope': 'interactive_current_physics'},
                    'structure': [], 'offline_static_prior': True,
                    'recorded_future_tracks_included': False}
        cache_key = (key, version, result.get('step'))
        with self.cache_lock:
            snapshot = self.snapshots.get(cache_key)
            if snapshot is None:
                snapshot = build_snapshot(payload, result.get('step'), version)
                self.snapshots[cache_key] = snapshot
                while len(self.snapshots) > 32:
                    self.snapshots.popitem(last=False)
            else:
                self.snapshots.move_to_end(cache_key)
        stamp = result.get('source_timestamp_ns')
        if stamp is not None and stamp != snapshot['source_timestamp_ns']:
            raise ValueError('Engine frame and scene snapshot timestamps do not match')
        return snapshot

    def server_close(self):
        self.interactive.close()
        self.engine.close()
        super().server_close()


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = 'MapReview/1.0'

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('4', '5')):
            super().log_message(fmt, *args)

    def _origin_allowed(self, origin):
        parsed = urlsplit(origin)
        return (parsed.scheme in {'http', 'https'} and parsed.netloc == self.headers.get('Host')
                or origin in self.server.allowed_origins)

    def _admin(self):
        if self.server.control_key is None:
            return
        authorization = self.headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or not secrets.compare_digest(authorization[7:].encode(), self.server.control_key.encode()):
            raise PermissionError('创建或重启引擎需要服务器控制密钥。')

    def do_OPTIONS(self):
        origin = self.headers.get('Origin')
        if not origin or not self._origin_allowed(origin):
            self._send({'error': 'Origin is not allowed'}, status=403)
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Vary', 'Origin')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send(self, body, content_type='application/json; charset=utf-8', *, status=200, filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        encoded = len(body) > 4096 and 'gzip' in self.headers.get('Accept-Encoding', '') and not content_type.startswith('image/')
        if encoded:
            body = gzip.compress(body, compresslevel=3)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Vary', 'Accept-Encoding, Origin')
        origin = self.headers.get('Origin')
        if origin and self._origin_allowed(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
        if encoded:
            self.send_header('Content-Encoding', 'gzip')
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            self._get()
        except PermissionError as error:
            self._send({'error': str(error)}, status=403)
        except (KeyError, FileNotFoundError) as error:
            self._send({'error': str(error)}, status=404)
        except (ValueError, TypeError) as error:
            self._send({'error': str(error)}, status=400)
        except Exception as error:
            self._send({'error': str(error)}, status=500)

    def _get(self):
        from .simulation_platform_data import catalog, build_snapshot, planning_bundle
        url = urlsplit(self.path)
        path, query = unquote(url.path), parse_qs(url.query, keep_blank_values=True)
        if path == '/api/scenes':
            self._send({'scenes': catalog(self.server.repo)})
        elif path == '/api/config':
            self._send({'engine_available': importlib.util.find_spec('metadrive') is not None,
                        'python_version': sys.version.split()[0],
                        'engine_label': 'MetaDrive / Panda3D', 'review_label': '场景地图审阅',
                        'source_geometry_only': True, 'planning_schema': 'simulation-planning-handoff-1.0',
                        'interactive_available': True, 'public_static': False,
                        'control_key_required': self.server.control_key is not None})
        elif path == '/api/interactive/state':
            render = _one(query, 'render', '0')
            if render not in {'0', '1'}:
                raise ValueError('render must be 0 or 1')
            state = self.server.interactive.state(render=render == '1')
            requested = _one(query, 'session_id', None)
            if requested is not None and (state.get('room') or {}).get('session_id') != requested:
                raise ValueError('该房间已关闭或重建，请使用新的邀请链接。')
            self._send(state)
        elif path == '/api/interactive/observation':
            self._send(self.server.interactive.observation(_one(query, 'session_id', None),
                                                           _one(query, 'vehicle_id', 'ego')))
        elif path == '/api/interactive/map':
            self._send(self.server.interactive.map(_one(query, 'session_id', None)))
        elif path == '/api/engine/status':
            wait_text = _one(query, 'wait_ms', '0')
            if not wait_text.isascii() or not wait_text.isdecimal():
                raise ValueError('wait_ms must be an integer between 0 and 1000')
            self._send(self.server.engine.status(wait_ms=_wait_ms(int(wait_text)),
                                                job_id=_one(query, 'job_id', None)))
        elif path == '/api/engine/frame.png':
            frame_id = _one(query, 'frame_id', None)
            if not frame_id:
                raise ValueError('画面请求缺少唯一标识，请刷新平台页面后重新启动 MetaDrive。')
            self._send(self.server.engine.frame(scene_id=_one(query, 'scene_id', None),
                       version=_one(query, 'version', None), sequence=_one(query, 'sequence', None),
                       frame_id=frame_id), 'image/png')
        elif path.startswith('/api/scenes/'):
            pieces = path.split('/')
            if len(pieces) not in (4, 5) or not pieces[3]:
                raise KeyError('Unknown scene endpoint')
            key = pieces[3]
            action = pieces[4] if len(pieces) == 5 else ''
            version = _version(_one(query, 'version', 'updated'))
            if action == '':
                self._send(self.server.scene(key))
            elif action == 'snapshot':
                self._send(build_snapshot(self.server.scene(key), _step(query), version))
            elif action == 'planning':
                step = _step(query)
                self._send(planning_bundle(self.server.repo, key, step, version),
                           filename=f'planning-{key}-{version}-frame{step}.json')
            elif action == 'validation':
                self._send(self.server.validation(key, version))
            elif action == 'interactive':
                from .simulation_interactive_scenarios import interactive_profile
                self._send(interactive_profile(self.server.scene(key)))
            else:
                raise KeyError('Unknown scene action')
        else:
            names = {'/': 'simulation_platform.html', '/simulation_platform.html': 'simulation_platform.html',
                     '/simulation_platform.css': 'simulation_platform.css',
                     '/simulation_platform.js': 'simulation_platform.js',
                     '/simulation_platform_transport.js': 'simulation_platform_transport.js',
                     '/vendor/three.min.js': 'vendor/three.min.js', '/vendor/three.LICENSE': 'vendor/three.LICENSE'}
            if path == '/favicon.ico':
                self._send(b'', 'image/x-icon', status=204)
                return
            filename = names.get(path)
            if filename is None:
                raise KeyError('Unknown resource')
            target = self.server.repo / 'map_update' / filename
            content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if content_type in ('text/html', 'text/css', 'text/javascript', 'application/javascript'):
                content_type += '; charset=utf-8'
            self._send(target.read_bytes(), content_type)

    def do_POST(self):
        try:
            self._post()
        except PermissionError as error:
            self._send({'error': str(error)}, status=403)
        except BusyError as error:
            self._send({'error': str(error)}, status=409)
        except (ValueError, KeyError, FileNotFoundError, TypeError) as error:
            self._send({'error': str(error), 'code': getattr(error, 'code', None)}, status=getattr(error, 'status', 400))
        except Exception as error:
            self._send({'error': str(error)}, status=500)

    def _post(self):
        from .simulation_platform_data import scene_database
        origin = self.headers.get('Origin')
        if (origin and not self._origin_allowed(origin)) or (not origin and self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            raise ValueError('Only same-origin simulation controls are accepted')
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Simulation commands require application/json')
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 32768:
            raise ValueError('Invalid simulation command size')
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError('Simulation command must be an object')
        wait_ms = _wait_ms(body.pop('wait_ms', 0))
        path = urlsplit(self.path).path
        if path.startswith('/api/interactive/'):
            action = path.rsplit('/', 1)[-1]
            if action == 'open':
                self._admin()
                key, version = body.get('scene_id'), _version(body.get('version', 'updated'))
                if not isinstance(key, str):
                    raise ValueError('scene_id is required')
                self._send(self.server.interactive.open(key, version), status=202)
            elif action in {'claim', 'input', 'camera', 'control'}:
                self._send(getattr(self.server.interactive, action)(body))
            elif action in {'heartbeat', 'release'}:
                self._send(self.server.interactive.lease_command(action, body))
            else:
                raise KeyError('Unknown interactive endpoint')
            return
        self._admin()
        if self.server.interactive.active:
            raise BusyError('交互房间由服务器推进；请使用房间控制，或先由房主关闭房间。')
        if path == '/api/engine/open':
            key, version = body.get('scene_id'), _version(body.get('version', 'updated'))
            if not isinstance(key, str):
                raise ValueError('scene_id is required')
            database = scene_database(self.server.repo, key, version)
            step = body.get('step', 0)
            if type(step) is not int or step < 0:
                raise ValueError('Replay step must be a nonnegative integer')
            command = {'command': 'open', 'database_path': str(database), 'step': step}
            self._send_job(self.server.engine.submit(command, scene_id=key, version=version), wait_ms)
        elif path == '/api/engine/command':
            expected_scene = body.pop('expected_scene_id', None)
            expected_version = body.pop('expected_version', None)
            if expected_scene is not None:
                if not isinstance(expected_scene, str):
                    raise ValueError('expected_scene_id must be a scene key')
                _version(expected_version)
            allowed = {'seek': {'step'}, 'advance': {'steps'}, 'camera': {'yaw', 'pitch', 'distance', 'pan', 'target', 'preset'},
                       'select': {'feature_id', 'event_id', 'focus'}, 'overlays': {'structure', 'state'},
                       'frame': set(), 'status': set(), 'close': set()}
            command = body.get('command')
            if command not in allowed or set(body) - (allowed[command] | {'command'}):
                raise ValueError('Unsupported simulation command or fields')
            if not self.server.engine.status()['ready'] and command != 'close':
                raise ValueError('请先启动选中场景的 MetaDrive。')
            self._send_job(self.server.engine.submit(body, expected_scene_id=expected_scene,
                                                    expected_version=expected_version), wait_ms)
        else:
            raise KeyError('Unknown simulation endpoint')

    def _send_job(self, job, wait_ms):
        if wait_ms:
            status = self.server.engine.status(wait_ms=wait_ms, job_id=job['job_id'])
            if status['job_id'] != job['job_id']:
                raise BusyError('仿真任务已被另一页面替换，请重新启动当前场景。')
            if not status['busy']:
                self._send({**job, 'status': status})
                return
        self._send(job, status=202)
