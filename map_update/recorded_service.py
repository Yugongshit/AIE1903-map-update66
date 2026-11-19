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
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .recorded_takeover import action_batch
from .map_versions import MapVersions, provenance
from .map_coverage import MapCoverage
from .planning_contract import validate_plan, validate_scope, scoped_observation, road_task
from .evidence_intake import prepare_evidence, road_state_layer, require_identity
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
        self.physical_map_version = self.map_version
        self.maps = MapVersions(self.map)
        self.coverage = MapCoverage(self.map)
        self.scopes = {}
        self.coverage_policies = {}
        self.plans = {}
        self.plan_receipts = {}
        self.tasks = {}
        self.pending = {}
        self.leases = {}
        self.history = deque(maxlen=history_frames)
        self.deadline = None
        self.revision = 0
        self.evidence = {}
        self.structure_records = {}
        self.receipts = {}
        self.fault = None
        self._capture()
        self._record('open', self.snapshot())
        self._record('world_source', dict(database=getattr(session, 'database', None),
                                         index=getattr(session, 'index', None),
                                         source_sha256=getattr(session, 'source_sha256', None),
                                         physical_map_version=self.physical_map_version))

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
        self.pending.clear()
        observation = self.session.observe()
        for actor in observation['objects'].values():
            actor['source_lane_coverage'] = self.coverage.actor(actor)
        layer = road_state_layer(self.evidence,
                                 cutoff_ns=round(observation['simulation_time_s'] * 1_000_000_000),
                                 coordinate_frame=observation['coordinate_frame'])
        for row in layer['features']:
            row['properties']['unresolved_map_aliases'] = [
                alias['map_element_id'] for alias in row['properties']['map_aliases']
                if alias['map_element_id'] not in self.map]
        observation.update(physical_map_version=self.physical_map_version,
                           remaining_recording_s=max(0., (self.session.scenario.get('length', 100000) - 1 - self.session.step) * .1))
        observation.update(road_state_version=feature_sha256(layer), road_states=layer)
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
        result['native_stop_reason'] = self.session.stopped
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

    def evidence_update(self, body):
        allowed = {'submission_id', 'session_id', 'scene_id', 'map_version',
                   'state_version', 'observation_step', 'time_basis', 'events'}
        if set(body) - allowed:
            raise ValueError('Unknown evidence envelope fields')
        submission_id = body.get('submission_id')
        require_identity(submission_id, 'submission_id')
        fingerprint = feature_sha256(body)
        if submission_id in self.receipts:
            receipt = self.receipts[submission_id]
            if receipt['fingerprint'] != fingerprint:
                raise ValueError('submission_id already identifies different content')
            return deepcopy(receipt['result'])
        self._check(body)
        if body.get('time_basis') != 'simulation_time_ns':
            raise ValueError('Explicit simulation_time_ns time basis is required')
        current = self.history[-1]
        if body.get('scene_id') != current['scene_id']:
            raise ValueError('Evidence scene_id mismatch')
        candidate = prepare_evidence(
            self.evidence, body.get('events'),
            cutoff_ns=round(current['simulation_time_s'] * 1_000_000_000),
            coordinate_frame=current['coordinate_frame'])
        for event in candidate.values():
            for alias in event.get('map_aliases', []):
                if alias['map_element_id'] not in self.map:
                    raise ValueError('Unknown map_element_id')
        self._record('evidence_intent', body)
        self.evidence = candidate
        try:
            self._capture()
            result = dict(submission_id=submission_id, observation=self.snapshot())
            self._record('evidence_accepted', result)
        except Exception:
            self.fault = self.fault or 'evidence_publish_failed'
            raise
        self.receipts[submission_id] = dict(fingerprint=fingerprint, result=deepcopy(result))
        return result

    def _owner(self, actor_id, token):
        if not isinstance(actor_id, str):
            raise ValueError('actor_id must be a source ID string')
        expected = self.leases.get(actor_id)
        if not isinstance(token, str) or expected is None or not secrets.compare_digest(token.encode(), expected.encode()):
            raise PermissionError('Actor control token required')

    def _receipt(self, body, kind):
        fields = {'request_id', 'session_id', 'map_version', 'state_version', 'observation_step'}
        fields.update({'map': {'operations', 'provenance'},
                       'rollback': {'target_version', 'provenance'},
                       'structure': {'records'}}[kind])
        if set(body) - fields:
            raise ValueError('Unknown transaction fields')
        request_id = body.get('request_id')
        require_identity(request_id, 'request_id')
        key = (kind, request_id)
        fingerprint = feature_sha256(body)
        previous = self.receipts.get(key)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('Request ID already has different content')
            return key, fingerprint, deepcopy(previous['result'])
        return key, fingerprint, None

    def structure_evidence(self, body):
        from .planning_contract import number
        from .evidence_intake import _timestamp
        key, fingerprint, previous = self._receipt(body, 'structure')
        if previous is not None:
            return previous
        self._check(body)
        records = body.get('records')
        if not isinstance(records, list) or not 1 <= len(records) <= 100:
            raise ValueError('Require 1 to 100 structural evidence records')
        candidate = deepcopy(self.structure_records)
        seen = set()
        fields = {'evidence_id', 'source_object_id', 'change_type', 'observed_time_ns',
                  'source_ids', 'score', 'score_semantics', 'unknown_fields', 'decision',
                  'coordinate_frame', 'operations'}
        current = self.snapshot()
        for row in records:
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError('Structural evidence fields do not match the contract')
            for field in ('evidence_id', 'source_object_id', 'change_type', 'score_semantics'):
                require_identity(row[field], field)
            identity = row['evidence_id']
            if identity in seen:
                raise ValueError('Repeated evidence identity in one batch')
            seen.add(identity)
            if row['coordinate_frame'] != current['coordinate_frame']:
                raise ValueError('Structural evidence coordinate frame mismatch')
            if _timestamp(row['observed_time_ns']) > round(current['simulation_time_s']*1_000_000_000):
                raise ValueError('Structural evidence is in the future')
            number(row['score'], 'score')
            if row['decision'] not in ('supported', 'unsupported', 'unknown'):
                raise ValueError('Explicit upstream decision is required')
            for field in ('source_ids', 'unknown_fields'):
                if not isinstance(row[field], list) or any(not isinstance(value,str) or not value for value in row[field]):
                    raise ValueError(field + ' must be an explicit string list')
            if not row['source_ids']:
                raise ValueError('Evidence sources are required')
            self.maps.prepare(row['operations'])
            if any(op['source_event_id'] != identity for op in row['operations']):
                raise ValueError('Operation evidence identity mismatch')
            accepted = dict(record=deepcopy(row), map_version=self.map_version)
            if identity in candidate and candidate[identity] != accepted:
                raise ValueError('Structural evidence identities are immutable')
            candidate[identity] = accepted
        self._record('structure_evidence_accepted', body)
        self.structure_records = candidate
        result = dict(accepted=sorted(seen), map_version=self.map_version,
                      state_version=current['state_version'], score_is_probability=False)
        self.receipts[key] = dict(fingerprint=fingerprint, result=deepcopy(result))
        return result

    def map_patch(self, body):
        key, fingerprint, previous = self._receipt(body, 'map')
        if previous is not None:
            return previous
        self._check(body)
        source = provenance(body.get('provenance'))
        candidate, changes = self.maps.prepare(body.get('operations'))
        if not source['synthetic']:
            for operation in body['operations']:
                evidence = self.structure_records.get(operation['source_event_id'])
                if (evidence is None or evidence['map_version'] != self.map_version
                        or evidence['record']['decision'] != 'supported'
                        or operation not in evidence['record']['operations']):
                    raise ValueError('Real map publication requires matching supported upstream structural evidence')
        return self._publish_map(body, candidate, changes, source, key, fingerprint)

    def map_rollback(self, body):
        key, fingerprint, previous = self._receipt(body, 'rollback')
        if previous is not None:
            return previous
        self._check(body)
        target = body.get('target_version')
        if not isinstance(target, str) or target not in self.maps.snapshots:
            raise ValueError('Unknown target map revision')
        source = provenance(body.get('provenance'))
        return self._publish_map(body, self.maps.snapshots[target], [], source, key, fingerprint, target)

    def _publish_map(self, body, candidate, changes, source, key, fingerprint, target=None):
        self._record('map_intent', dict(command='map_rollback' if target else 'map_patch', body=body))
        try:
            revision = self.maps.publish(candidate, changes, source, restored_from=target)
            self.map, self.map_version = deepcopy(self.maps.current), self.maps.version
            self._capture()
            result = dict(revision=revision, observation=self.snapshot())
            self._record('map_published', result)
        except Exception:
            self.fault = self.fault or 'map_publish_failed'
            raise
        self.receipts[key] = dict(fingerprint=fingerprint, result=deepcopy(result))
        return result

    def plan_submit(self, body):
        self._check(body)
        plan = validate_plan(body.get('plan'), self.snapshot(), self.session.controlled,
                             self.snapshot()['remaining_recording_s'])
        actor_id = plan['actor_id']
        self._owner(actor_id, body.get('control_token'))
        bound = dict(plan=plan, state_version=body['state_version'], step=self.session.step,
                     map_version=self.map_version, session_id=self.session_id)
        receipt_key = (actor_id, plan['plan_id'])
        old = self.plan_receipts.get(receipt_key)
        if old is not None:
            if old != bound:
                raise ValueError('Plan ID already identifies different content')
            return deepcopy(old)
        self._record('plan_accepted', bound)
        self.plans[actor_id] = bound
        self.plan_receipts[receipt_key] = deepcopy(bound)
        return deepcopy(bound)

    def task_set(self, body):
        self._check(body)
        actor_id = body.get('actor_id')
        self._owner(actor_id, body.get('control_token'))
        current = self.snapshot()['objects'].get(actor_id)
        if current is None:
            raise ValueError('Actor is currently unavailable')
        task = road_task(self.map, current['position_m'], body.get('goal_m'))
        self._record('task_set', dict(actor_id=actor_id, task=task, map_version=self.map_version))
        self.tasks[actor_id] = dict(task=task, map_version=self.map_version)
        self._capture()
        return dict(task=deepcopy(self.tasks[actor_id]), observation=self.snapshot())

    def actor_observation(self, actor_id, token):
        self._owner(actor_id, token)
        result = scoped_observation(self.snapshot(), actor_id, self.scopes[actor_id])
        result['coverage_policy'] = self.coverage_policies[actor_id]
        result['task'] = deepcopy(self.tasks.get(actor_id))
        if result['task']:
            result['task']['valid_for_current_map'] = result['task']['map_version'] == self.map_version
        return result

    def actor_action(self, body):
        self._check(body)
        actor_id = body.get('actor_id')
        self._owner(actor_id, body.get('control_token'))
        checked = action_batch(self.session.step, body['observation_step'],
                               {actor_id}, {actor_id:body.get('action')})
        pending = dict(action=checked[actor_id], plan_id=body.get('plan_id'))
        if actor_id in self.pending and self.pending[actor_id] != pending:
            raise ValueError('Actor already submitted a different action for this frame')
        if pending['plan_id'] is not None:
            self._check_plan(actor_id, pending['plan_id'], body['state_version'])
        self.pending[actor_id] = pending
        if set(self.pending) == self.session.controlled:
            plans = {key:value['plan_id'] for key,value in self.pending.items() if value['plan_id'] is not None}
            advanced = self.advance(dict(
                session_id=self.session_id, map_version=self.map_version,
                state_version=body['state_version'], observation_step=self.session.step,
                actions={key:value['action'] for key,value in self.pending.items()},
                control_tokens=dict(self.leases), plan_ids=plans))
            return dict(advanced=True, observation=scoped_observation(
                advanced, actor_id, self.scopes[actor_id]))
        return dict(advanced=False, observation=self.actor_observation(actor_id, body['control_token']))

    def _check_plan(self, actor_id, plan_id, version):
        plan = self.plans.get(actor_id)
        if plan is None or plan['plan']['plan_id'] != plan_id or plan['state_version'] != version:
            raise ValueError('Action plan must match the current accepted trajectory')

    def resume(self, body):
        if self.fault != 'action_timeout' or self.session.stopped:
            raise ValueError('Only an action-timeout freeze can resume')
        current = self.snapshot()
        for key in ('session_id', 'map_version', 'state_version'):
            if body.get(key) != current[key]:
                raise ValueError('Resume binding mismatch')
        self._record('resume_intent', dict(step=self.session.step))
        self.fault = None
        self.deadline = self.clock() + self.timeout
        self._capture()
        self._record('resumed', self.snapshot())
        return self.snapshot()

    def takeover(self, body):
        self._check(body)
        ids = body.get('actor_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids):
            raise ValueError('actor_ids must be a nonempty list of source track IDs')
        if len(set(ids)) != len(ids) or set(ids) & self.leases.keys():
            raise ValueError('Duplicate actor or control ownership conflict')
        scope = validate_scope(body.get('observation_scope', {'kind': 'global'}))
        coverage_policy = body.get('coverage_policy', 'map_envelope')
        if coverage_policy not in ('map_envelope', 'source_lane_polygons'):
            raise ValueError('Unknown map coverage policy')
        if coverage_policy == 'source_lane_polygons':
            current = self.snapshot()['objects']
            if any(key not in current or current[key]['source_lane_coverage'].get('footprint_covered') is not True for key in ids):
                raise ValueError('Strict coverage requires the complete vehicle footprint inside explicit source polygons')
        self._record('takeover_intent', dict(actor_ids=ids, step=self.session.step, observation_scope=scope, coverage_policy=coverage_policy))
        try:
            self.session.takeover(ids)
        except ValueError:
            raise
        except Exception:
            self.fault = 'takeover_failed'
            raise
        for key in ids:
            self.leases[key] = secrets.token_urlsafe(32)
            self.scopes[key] = deepcopy(scope)
            self.coverage_policies[key] = coverage_policy
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
        plan_ids = body.get('plan_ids', {})
        if not isinstance(plan_ids, dict) or set(plan_ids) - set(actions):
            raise ValueError('plan_ids must refer to controlled actors')
        for actor_id, plan_id in plan_ids.items():
            self._check_plan(actor_id, plan_id, body['state_version'])
        self._record('advance_intent', dict(session_id=self.session_id, step=self.session.step,
                                            actions=actions, plan_ids=plan_ids))
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
        actual_objects = self.session.observe()['objects']
        for actor_id, policy in self.coverage_policies.items():
            if policy == 'source_lane_polygons':
                actor = actual_objects.get(actor_id)
                if actor is None or self.coverage.actor(actor).get('footprint_covered') is not True:
                    self.fault = 'outside_explicit_source_lane_polygons'
        self._capture()
        self._record('advance', self.snapshot())
        if hasattr(self.session, 'contacts'):
            self._record('evaluation', dict(step=self.session.step, contacts=self.session.contacts(),
                                           replay_error_m=self.session.replay_error()))
        return self.snapshot()

    def read(self, path):
        if path == '/structure-evidence':
            return deepcopy(self.structure_records)
        if path == '/map-versions':
            return dict(session_id=self.session_id, current=self.map_version,
                        physical_map_version=self.physical_map_version, revisions=deepcopy(self.maps.revisions))
        if path == '/controls':
            return dict(session_id=self.session_id, control_tokens=dict(self.leases), scopes=deepcopy(self.scopes))
        if path == '/plans':
            return deepcopy(self.plans)
        if path == '/tasks':
            return deepcopy(self.tasks)
        if path == '/observation':
            return self.snapshot()
        if path == '/road-states':
            current = self.snapshot()
            return {key:current[key] for key in
                    ('session_id', 'scene_id', 'step', 'simulation_time_s', 'map_version',
                     'state_version', 'road_state_version', 'road_states', 'service_stopped')}
        if path == '/map':
            current = self.snapshot()
            return dict(session_id=self.session_id, scene_id=current['scene_id'],
                        map_version=self.map_version, coordinate_frame=current['coordinate_frame'],
                        features=deepcopy(self.map), content_sha256=feature_sha256(self.map),
                        physical_map_version=self.physical_map_version)
        if path == '/history':
            current = self.snapshot()
            return dict(session_id=self.session_id, scene_id=current['scene_id'],
                        map_version=self.map_version, through_step=current['step'],
                        observations=deepcopy(list(self.history)), service_stopped=self.fault)
        raise KeyError(path)


class RecordedHTTPServer(HTTPServer):
    """Serial requests keep Panda3D and all physics mutations on one thread."""

    def __init__(self, address, service, access_key, *, session_factory=None, catalog=None):
        if not isinstance(access_key, str) or not access_key:
            raise ValueError('An access key is required')
        self.service, self.access_key = service, access_key
        self.session_factory = session_factory
        self.catalog = catalog or []
        super().__init__(address, RecordedHandler)

    def open_scene(self, body):
        previous = self.service
        if body.get('session_id') != previous.session_id:
            raise ValueError('Open request belongs to an old session')
        index, step = body.get('index'), body.get('step', 0)
        if self.session_factory is None or type(index) is not int or not 0 <= index < len(self.catalog):
            raise ValueError('Unknown catalog scene index')
        if type(step) is not int or step < 0:
            raise ValueError('Replay start step must be nonnegative')
        frames = self.catalog[index].get('frames')
        if frames is not None and step >= frames:
            raise ValueError('Replay start exceeds the recording')
        previous._record('open_intent', dict(index=index, step=step))
        previous.session.close()
        session = None
        try:
            session = self.session_factory(index)
            if step >= session.scenario['length']:
                raise ValueError('Replay start exceeds the recording')
            for _ in range(step):
                session.advance({}, observation_step=session.step)
            self.service = RecordedService(session, previous.journal,
                                           history_frames=previous.history.maxlen, action_timeout=previous.timeout)
            return self.service.snapshot()
        except Exception:
            if session is not None:
                session.close()
            previous.fault = 'scene_open_failed'
            raise


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
            parsed = urlsplit(self.path)
            path = parsed.path
            static = {'/': ('recorded_workbench.html', 'text/html; charset=utf-8'),
                      '/recorded_workbench.js': ('recorded_workbench.js', 'text/javascript'),
                      '/recorded_workbench.css': ('recorded_workbench.css', 'text/css'),
                      '/three.min.js': ('vendor/three.min.js', 'text/javascript')}
            if not mutation and path in static:
                filename, content_type = static[path]
                data = (Path(__file__).parent / filename).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            authorization = self.headers.get('Authorization', '')
            admin = secrets.compare_digest(authorization.encode(), ('Bearer ' + self.server.access_key).encode())
            token = authorization.removeprefix('Bearer ')
            service = self.server.service
            if not mutation:
                if path in ('/actor-observation', '/actor-history', '/actor-map'):
                    actor_id = parse_qs(parsed.query).get('actor_id', [''])[0]
                    service._owner(actor_id, token)
                    if path == '/actor-observation':
                        result = service.actor_observation(actor_id, token)
                    elif path == '/actor-history':
                        result = dict(session_id=service.session_id, observations=[
                            scoped_observation(row, actor_id, service.scopes[actor_id])
                            for row in service.history if actor_id in row['objects']])
                    else:
                        result = service.read('/map')
                elif not admin:
                    raise PermissionError('Access key required')
                elif path == '/catalog':
                    result = dict(scenes=self.server.catalog, session_id=service.session_id)
                elif path == '/report':
                    from .experiment_records import summarize_journal
                    result = summarize_journal(Path(service.journal.name))
                else:
                    result = service.read(path)
            else:
                commands = {'/takeover': 'takeover', '/advance': 'advance', '/evidence': 'evidence_update',
                            '/map-patch': 'map_patch', '/map-rollback': 'map_rollback',
                            '/structure-evidence': 'structure_evidence',
                            '/plans': 'plan_submit', '/tasks': 'task_set',
                            '/actor-action': 'actor_action', '/resume': 'resume'}
                if path not in commands and path != '/open':
                    raise KeyError(path)
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('Transfer-Encoding is unsupported')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('Expected a JSON body of at most 1 MiB')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                if not admin:
                    if path not in ('/plans', '/tasks', '/actor-action'):
                        raise PermissionError('Access key required')
                    if path == '/plans' and not isinstance(body.get('plan'), dict):
                        raise ValueError('Plan must be an object')
                    actor_id = body['plan'].get('actor_id') if path == '/plans' else body.get('actor_id')
                    service._owner(actor_id, token)
                    if body.get('control_token') != token:
                        raise PermissionError('Header and body control token mismatch')
                result = self.server.open_scene(body) if path == '/open' else getattr(service, commands[path])(body)
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
