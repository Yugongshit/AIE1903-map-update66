"""Local scene review, real MetaDrive playback, and versioned planning handoff.

HTTP requests never supply filesystem paths or Python callbacks. A single
dedicated child owns Panda3D on its main thread, including on macOS. The web
review renderer and the actual simulator are explicitly separate view modes.
"""
from concurrent.futures import ThreadPoolExecutor
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import mimetypes
from pathlib import Path
import queue
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
    def __init__(self, repo_root, python=None):
        self.repo = Path(repo_root).resolve()
        self.python = str(python or sys.executable)
        self.directory = tempfile.TemporaryDirectory(prefix='map-review-engine-')
        self.root = Path(self.directory.name)
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='metadrive-session')
        self.process = None
        self.responses = queue.Queue()
        self.log = None
        self.closed = False
        self.state = dict(busy=False, job_id=None, last_result=None, error=None,
                          scene_id=None, version=None, ready=False)

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            self._stop_process()
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
                with self.lock:
                    self.state.update(last_result=result, ready=command.get('command') != 'close')
                if command.get('command') == 'close':
                    self._stop_process()
                break
        except queue.Empty:
            self._stop_process()
            with self.lock:
                self.state.update(error='仿真操作超时，已关闭工作进程；可重新启动场景。', ready=False)
        except Exception as error:
            with self.lock:
                self.state.update(error=str(error), ready=False)
        finally:
            with self.lock:
                self.state['busy'] = False

    def frame(self, *, scene_id=None, version=None, sequence=None):
        status = self.status()
        if scene_id is not None and (scene_id != status['scene_id'] or version != status['version']):
            raise ValueError('Requested frame belongs to a different scene or map version')
        result = status.get('last_result') or {}
        if sequence is not None and str(sequence) != str(result.get('sequence')):
            raise ValueError('Requested frame is no longer the current verified engine result')
        filename = result.get('frame') or result.get('frame_file') or result.get('frame_path')
        if not isinstance(filename, str):
            raise FileNotFoundError('仿真画面尚未生成。')
        path = (self.root / filename).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != '.png':
            raise ValueError('Invalid simulation frame path')
        return path.read_bytes()

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
        with self.lock:
            self.closed = True
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


class PlatformServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, repo_root, *, engine_python=None):
        super().__init__(address, PlatformHandler)
        self.repo = Path(repo_root).resolve()
        self.engine = EngineBroker(self.repo, engine_python)
        self.payloads = {}
        self.validations = {}
        self.cache_lock = threading.Lock()

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

    def server_close(self):
        self.engine.close()
        super().server_close()


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = 'MapReview/1.0'

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('4', '5')):
            super().log_message(fmt, *args)

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
        self.send_header('Vary', 'Accept-Encoding')
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
                        'source_geometry_only': True, 'planning_schema': 'simulation-planning-handoff-1.0'})
        elif path == '/api/engine/status':
            self._send(self.server.engine.status())
        elif path == '/api/engine/frame.png':
            self._send(self.server.engine.frame(scene_id=_one(query, 'scene_id', None),
                       version=_one(query, 'version', None), sequence=_one(query, 'sequence', None)), 'image/png')
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
            else:
                raise KeyError('Unknown scene action')
        else:
            names = {'/': 'simulation_platform.html', '/simulation_platform.html': 'simulation_platform.html',
                     '/simulation_platform.css': 'simulation_platform.css',
                     '/simulation_platform.js': 'simulation_platform.js',
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
        except BusyError as error:
            self._send({'error': str(error)}, status=409)
        except (ValueError, KeyError, FileNotFoundError, TypeError) as error:
            self._send({'error': str(error)}, status=400)
        except Exception as error:
            self._send({'error': str(error)}, status=500)

    def _post(self):
        from .simulation_platform_data import scene_database
        origin = self.headers.get('Origin')
        if (origin and urlsplit(origin).netloc != self.headers.get('Host')) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise ValueError('Only same-origin simulation controls are accepted')
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Simulation commands require application/json')
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 32768:
            raise ValueError('Invalid simulation command size')
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError('Simulation command must be an object')
        path = urlsplit(self.path).path
        if path == '/api/engine/open':
            key, version = body.get('scene_id'), _version(body.get('version', 'updated'))
            if not isinstance(key, str):
                raise ValueError('scene_id is required')
            database = scene_database(self.server.repo, key, version)
            step = body.get('step', 0)
            if type(step) is not int or step < 0:
                raise ValueError('Replay step must be a nonnegative integer')
            command = {'command': 'open', 'database_path': str(database), 'step': step}
            self._send(self.server.engine.submit(command, scene_id=key, version=version), status=202)
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
            self._send(self.server.engine.submit(body, expected_scene_id=expected_scene,
                                                expected_version=expected_version), status=202)
        else:
            raise KeyError('Unknown simulation endpoint')
