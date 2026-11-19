"""Synchronous, single-owner transport for a recorded physics world."""
from collections import deque
from copy import deepcopy
import json
import math
import os
import secrets
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from .recorded_takeover import action_batch
from .simulation_bridge import feature_sha256, json_value


class RecordedService:
    """Called exclusively on the HTTP/physics thread; one trusted coordinator."""

    def __init__(self, session, journal, *, history_frames=30, action_timeout=30., clock=time.monotonic):
        if type(history_frames) is not int or not 1 <= history_frames <= 1000:
            raise ValueError('history_frames must be between 1 and 1000')
        if not math.isfinite(action_timeout) or action_timeout <= 0:
            raise ValueError('action_timeout must be positive and finite')
        self.session = session
        self.journal = journal
        self.clock = clock
        self.timeout = action_timeout
        self.session_id = uuid.uuid4().hex
        self.map = json_value(session.map_input())
        self.map_version = feature_sha256(self.map)
        self.leases = {}
        self.history = deque(maxlen=history_frames)
        self.deadline = None
        self.revision = 0
        self.fault = None
        self._capture()
        self._record('open', self.snapshot())

    def _record(self, event, data):
        try:
            self.journal.write(json.dumps(dict(event=event, data=data), allow_nan=False) + '\n')
            self.journal.flush()
            os.fsync(self.journal.fileno())
        except Exception:
            self.fault = 'journal_write_failed'
            raise

    def _capture(self):
        self.revision += 1
        observation = self.session.observe()
        observation.update(session_id=self.session_id, scene_id=str(self.session.scenario['id']),
                           map_version=self.map_version,
                           state_version=f'{self.session_id}:{self.revision}')
        if self.history and self.history[-1]['step'] == observation['step']:
            self.history.pop()
        self.history.append(deepcopy(observation))

    def _expire(self):
        if self.deadline is not None and self.clock() >= self.deadline:
            self.fault = self.fault or 'action_timeout'

    def snapshot(self):
        self._expire()
        result = deepcopy(self.history[-1])
        result['service_stopped'] = self.fault
        result['action_timeout_s'] = self.timeout
        result['action_remaining_s'] = None if self.deadline is None else max(0., self.deadline-self.clock())
        return result

    def _check(self, body):
        self._expire()
        if self.fault or self.session.stopped:
            raise ValueError(self.fault or self.session.stopped)
        if body.get('session_id') != self.session_id or body.get('map_version') != self.map_version:
            raise ValueError('Session or map version mismatch')
        if body.get('state_version') != self.history[-1]['state_version']:
            raise ValueError('State version mismatch')
        if type(body.get('observation_step')) is not int or body['observation_step'] != self.session.step:
            raise ValueError('Observation step must match the current frame')

    def takeover(self, body):
        self._check(body)
        ids = body.get('actor_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids):
            raise ValueError('actor_ids must be a nonempty list of source track IDs')
        if len(set(ids)) != len(ids) or set(ids) & self.leases.keys():
            raise ValueError('Duplicate actor or control ownership conflict')
        self._record('takeover_intent', dict(actor_ids=ids, step=self.session.step))
        try:
            self.session.takeover(ids)
        except ValueError:
            raise
        except Exception:
            self.fault = 'takeover_failed'
            raise
        for key in ids:
            self.leases[key] = secrets.token_urlsafe(32)
        # Adding another vehicle must not extend an existing computation deadline.
        if self.deadline is None:
            self.deadline = self.clock() + self.timeout
        self._capture()
        self._record('takeover', self.snapshot())
        return dict(observation=self.snapshot(), control_tokens={key:self.leases[key] for key in ids})

    def advance(self, body):
        self._check(body)
        actions = action_batch(self.session.step, body['observation_step'],
                               self.session.controlled, body.get('actions'))
        tokens = body.get('control_tokens')
        if not isinstance(tokens, dict) or set(tokens) != set(self.leases):
            raise PermissionError('Every controlled actor requires its control token')
        for key, expected in self.leases.items():
            supplied = tokens[key]
            if not isinstance(supplied, str) or not secrets.compare_digest(supplied.encode(), expected.encode()):
                raise PermissionError('Invalid control token')
        self._record('advance_intent', dict(session_id=self.session_id, step=self.session.step, actions=actions))
        previous_step = self.session.step
        try:
            self.session.advance(actions, observation_step=body['observation_step'])
        except Exception:
            # A native failure may have advanced part of the physics world.
            self.fault = 'advance_failed'
            raise
        if self.session.step != previous_step + 1:
            self.fault = 'invalid_physics_step'
            raise RuntimeError(self.fault)
        self.deadline = self.clock() + self.timeout if self.leases else None
        self._capture()
        self._record('advance', self.snapshot())
        return self.snapshot()

    def read(self, path):
        if path == '/observation':
            return self.snapshot()
        if path == '/map':
            current = self.snapshot()
            return dict(session_id=self.session_id, scene_id=current['scene_id'],
                        map_version=self.map_version, coordinate_frame=current['coordinate_frame'],
                        features=deepcopy(self.map))
        if path == '/history':
            current = self.snapshot()
            return dict(session_id=self.session_id, scene_id=current['scene_id'],
                        map_version=self.map_version, through_step=current['step'],
                        observations=deepcopy(list(self.history)), service_stopped=self.fault)
        raise KeyError(path)


class RecordedHTTPServer(HTTPServer):
    """Serial requests keep Panda3D and all physics mutations on one thread."""

    def __init__(self, address, service, access_key):
        if not isinstance(access_key, str) or not access_key:
            raise ValueError('An access key is required')
        self.service, self.access_key = service, access_key
        super().__init__(address, RecordedHandler)


class RecordedHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass  # Credentials and request payloads must not enter HTTP access logs.

    def _reply(self, status, data):
        encoded = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self, mutation):
        try:
            supplied = self.headers.get('Authorization', '')
            expected = 'Bearer ' + self.server.access_key
            if not secrets.compare_digest(supplied.encode(), expected.encode()):
                raise PermissionError('Access key required')
            if not mutation:
                result = self.server.service.read(self.path)
            else:
                if self.path not in ('/takeover', '/advance'):
                    raise KeyError(self.path)
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('Transfer-Encoding is unsupported')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('Expected a JSON body of at most 1 MiB')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                method = getattr(self.server.service, self.path[1:])
                result = method(body)
            self._reply(200, result)
        except PermissionError as error:
            self._reply(403, dict(error=str(error)))
        except KeyError:
            self._reply(404, dict(error='Unknown endpoint'))
        except (ValueError, TypeError) as error:
            self._reply(409, dict(error=str(error)))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            self.server.service.fault = 'internal_error'
            self._reply(500, dict(error='Service stopped after an internal error; inspect the experiment journal'))

    def do_GET(self):
        self._handle(False)

    def do_POST(self):
        self._handle(True)
