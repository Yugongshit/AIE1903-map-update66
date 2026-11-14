"""One server-owned physics clock shared by players, planners and spectators.

HTTP requests update authenticated mailboxes. Only this coordinator submits
native commands; loading a frame or observing a vehicle never advances time.
"""
from copy import deepcopy
import threading
import time

from .simulation_room import InteractiveRoom


class InteractiveCoordinator:
    def __init__(self, server):
        self.server = server
        self.room = InteractiveRoom()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = False
        self.active = False
        self.playing = False
        self.phase = 'idle'
        self.error = None
        self.identity = None
        self.scene_key = self.version = None
        self.traffic_options = None
        self.observed_maps = {}
        self.planning_receipts = []
        self.job = None
        self.last_engine = None
        self.last_result = None
        self.compact_result = None
        self.pending_camera = None
        self.pending_close = False
        self.camera_since_tick = False
        self.next_tick = 0.
        self.render_until = 0.
        self.thread = threading.Thread(target=self._run, name='interactive-physics-clock', daemon=True)
        self.thread.start()

    def _check(self, body):
        if not self.active or self.identity is None:
            raise ValueError('请先创建交互交通房间。')
        if body.get('session_id') != self.identity['session_id'] or body.get('epoch') != self.identity['epoch']:
            raise ValueError('房间已重建；请使用新的邀请链接和控制令牌。')

    def _authorize(self, body, *, host=False):
        import secrets
        self._check(body)
        keys = ('host_token',) if host else ('host_token', 'join_token')
        if not any(isinstance(body.get(key), str) and
                   secrets.compare_digest(body[key].encode(), self.identity[key].encode()) for key in keys):
            raise PermissionError('需要房主凭证。' if host else '需要房间邀请凭证。')

    def _submit(self, command):
        self.job = self.server.engine.submit(command, expected_scene_id=self.scene_key,
                                             expected_version=self.version)
        self.job['identity'] = {k: self.identity[k] for k in ('session_id', 'epoch')}
        return self.job

    def open(self, key, version, traffic=None):
        from .simulation_platform_data import scene_database
        from .simulation_interactive_scenarios import choose_interactive_missions
        payload = self.server.scene(key)
        # The verified missions use the updated source lane graph. Keeping
        # one physical version prevents a lobby from silently changing roads.
        if version != 'updated':
            raise ValueError('交互交通使用更新后的道路；原始道路可在地图审阅中比较。')
        if traffic is not None:
            from .simulation_traffic import cyclic_traffic
            interactive_config = cyclic_traffic(payload, traffic)
        else:
            interactive_config = {'missions': choose_interactive_missions(payload)}
        missions = interactive_config['missions']
        database = scene_database(self.server.repo, key, version)
        with self.lock:
            if self.active:
                raise ValueError('已有交互房间，请由房主关闭后再创建。')
            if self.server.engine.status()['busy']:
                raise ValueError('引擎仍在执行上一项操作。')
            identity = self.room.create(key, version, missions, vehicle_ids=('ego', *[m['vehicle_id'] for m in missions]))
            self.identity = {k: identity[k] for k in ('session_id', 'epoch', 'host_token', 'join_token')}
            self.scene_key, self.version = key, version
            self.traffic_options = deepcopy(traffic)
            from .simulation_live_map import ObservedStateMap
            from .simulation_bridge import feature_sha256
            roads = payload['maps'][version]
            self.observed_maps = {identifier: ObservedStateMap(roads, feature_sha256(roads))
                                  for identifier in ['ego', *[m['vehicle_id'] for m in missions]]}
            self.planning_receipts = []
            self.active, self.playing, self.phase, self.error = True, False, 'starting', None
            self.last_engine = self.last_result = self.compact_result = self.pending_camera = None
            self.pending_close = False
            self.camera_since_tick = False
            self.render_until = 0.
            try:
                self.job = self.server.engine.submit(
                    {'command': 'open', 'database_path': str(database), 'step': 0,
                     'interactive': interactive_config}, scene_id=key, version=version)
                self.job['identity'] = {k: self.identity[k] for k in ('session_id', 'epoch')}
            except BaseException:
                self.active, self.phase = False, 'idle'
                raise
            self.wake.set()
            return {'job_id': self.job['job_id'], 'room': deepcopy(self.identity), 'phase': self.phase}

    def _finish(self, state):
        if not self.job or state.get('busy') or state.get('job_id') != self.job['job_id']:
            return
        identity = self.job['identity']
        self.job = None
        if state.get('error'):
            self.error, self.phase, self.playing = state['error'], 'error', False
            self.last_engine = state
            return
        result = state.get('last_result') or {}
        if not result.get('interactive'):
            if self.phase == 'closing':
                self.room.close(session_id=self.identity['session_id'], epoch=self.identity['epoch'],
                                host_token=self.identity['host_token'])
                self.active, self.phase = False, 'idle'
            return
        try:
            self.room.publish(result, result.get('frame_id'), return_snapshot=False, **identity)
        except Exception as error:
            # Polling and the clock use the same completion path. A malformed
            # physical result must stop both from presenting stale observations
            # as the current state of a still-running world.
            self.error, self.phase, self.playing = str(error), 'error', False
            self.last_engine = {**state, 'error': self.error}
            return
        # Source observation histories are already loaded once with the scene.
        # Repeating them on every viewer poll wastes bandwidth and obscures
        # that these six vehicles are actual current physical actors.
        result = {k: v for k, v in result.items()
                  if k not in {'state_events', 'state_objects', 'persistent_obstacles_runtime'}}
        from .simulation_traffic import vehicle_perception
        for identifier, observed_map in self.observed_maps.items():
            observed_map.update(result['step'], result['interactive'].get('sim_time_s', result['step']*.1),
                                vehicle_perception(result['interactive'], identifier, clearance_queries=observed_map.clearance_queries()))
        self.last_result = result
        # The browser already has static map/state overlays. Its snapshot
        # contains each actual actor once, including stationary colliders.
        # Keep the complete result for Planning and backwards-compatible reads.
        self.compact_result = {**result, 'interactive': {
            key: value for key, value in result['interactive'].items() if key != 'static_actors'}}
        self.last_engine = {**state, 'last_result': result}
        self.phase = 'running' if self.playing else 'paused'

    def _collect_completion(self):
        if self.job:
            state = self.server.engine.status(include_result=False)
            if not state.get('busy') and state.get('job_id') == self.job['job_id']:
                # Take one isolated copy per completion, never on every busy
                # poll or every spectator request.
                self._finish(self.server.engine.status())

    def _run(self):
        while not self.stopping:
            # Readiness checks only copy small job metadata. A short wait
            # avoids adding a 25 ms scheduling stall after every decision.
            self.wake.wait(.01)
            self.wake.clear()
            with self.lock:
                if not self.active:
                    continue
                try:
                    if self.job:
                        self._collect_completion()
                    if self.job:
                        continue
                    if self.pending_close:
                        self.pending_close, self.phase = False, 'closing'
                        self._submit({'command': 'close'})
                        continue
                    if self.phase in {'starting', 'error', 'closing'}:
                        continue
                    now = time.monotonic()
                    if self.pending_camera is not None and (not self.playing or not self.camera_since_tick):
                        command, self.pending_camera = self.pending_camera, None
                        self._submit({'command': 'camera', **command})
                        self.camera_since_tick = True
                    elif self.playing and now >= self.next_tick:
                        step = (self.last_result or {}).get('step', 0)
                        controls = self.room.controls_for_tick(step)['vehicles']
                        controls = {key: {field: row[field] for field in ('mode', 'action', 'expires_at_monotonic') if row.get(field) is not None}
                                    for key, row in controls.items()}
                        command = {'command': 'interactive_tick', 'steps': 1, 'controls': controls,
                                   'capture': now < self.render_until}
                        self._submit(command)
                        self.camera_since_tick = False
                        self.next_tick = now + .1
                    elif (not self.playing and now < self.render_until and
                          not (self.last_result or {}).get('image_available', True)):
                        # A newly opened native-image tab may need a picture
                        # while paused. Rendering alone never advances physics.
                        self._submit({'command': 'frame'})
                except Exception as error:
                    self.error, self.phase, self.playing = str(error), 'error', False

    def state(self, *, render=False, compact=False, since_state_id=None, session_id=None):
        with self.lock:
            if session_id is not None and (self.identity or {}).get('session_id') != session_id:
                # A stale native-view tab must not keep asking a replacement
                # room to generate costly PNGs before its identity is checked.
                raise ValueError('该房间已关闭或重建，请使用新的邀请链接。')
            if render:
                # Many spectators share one capture, and leaving the native
                # image tab automatically expires its rendering interest.
                self.render_until = time.monotonic() + 2.
                self.wake.set()
            if self.job:
                self._collect_completion()
            unchanged = bool(self.last_result and self.last_result.get('state_id') and
                             since_state_id == self.last_result['state_id'])
            result = None if unchanged else self.compact_result if compact else self.last_result
            engine = ({**{key: deepcopy(value) for key, value in self.last_engine.items() if key != 'last_result'},
                       'last_result': deepcopy(result)} if self.last_engine else
                      {'ready': False, 'busy': bool(self.job), 'scene_id': self.scene_key,
                       'version': self.version, 'last_result': None})
            return {'active': self.active, 'playing': self.playing, 'phase': self.phase,
                    'error': self.error, 'scene_id': self.scene_key, 'version': self.version,
                    'state_unchanged': unchanged,
                    'room': self.room.snapshot(include_objects=not compact,
                                               include_vehicle_state=not compact) if self.identity else None,
                    'engine': engine}

    def claim(self, body):
        with self.lock:
            if self.traffic_options is not None and body.get('kind', 'manual') == 'manual':
                raise ValueError('Automatic traffic experiment: manual driving is deferred')
            self._authorize(body)
            if self.phase in {'starting', 'error', 'closing'}:
                raise ValueError('等待房间完成启动后再接管车辆。')
            return self.room.claim(body.get('vehicle_id'), vehicle_ids=body.get('vehicle_ids'),
                session_id=body['session_id'], epoch=body['epoch'],
                host_token=body.get('host_token'), join_token=body.get('join_token'),
                kind=body.get('kind', 'manual'), client_label=body.get('client_label', 'participant'))

    def input(self, body):
        with self.lock:
            self._check(body)
            actions = body.get('actions')
            if actions is None and 'vehicle_id' in body and 'action' in body:
                actions = {body['vehicle_id']: body['action']}
            if actions is None:
                raise ValueError('Provide actions keyed by the controlled vehicle ID')
            versions = body.get('planning_state_versions')
            if versions is not None:
                if not isinstance(versions, dict) or set(versions) != set(actions) or type(body.get('observation_step')) is not int:
                    raise ValueError('Planning versions must identify every submitted vehicle and observation step')
                for identifier, version in versions.items():
                    if identifier not in self.observed_maps or not isinstance(version, str) or not self.observed_maps[identifier].accepts_version(version, body['observation_step']):
                        raise ValueError('Planning state version is unknown, expired or from the future')
            receipt = self.room.input(body.get('token', body.get('lease_token')),
                session_id=body['session_id'], epoch=body['epoch'], seq=body.get('seq'),
                actions=actions, observation_step=body.get('observation_step'))
            if versions is not None:
                receipt['accepted_planning_versions'] = deepcopy(versions)
                self.planning_receipts.append({'observation_step':body['observation_step'],
                    'seq':body['seq'], 'state_versions':deepcopy(versions), 'scope':'accepted_not_execution_proof'})
                self.planning_receipts = self.planning_receipts[-64:]
            return receipt

    def lease_command(self, action, body):
        with self.lock:
            self._check(body)
            return getattr(self.room, action)(body.get('token', body.get('lease_token')),
                session_id=body['session_id'], epoch=body['epoch'])

    def camera(self, body):
        from .simulation_session import validated_camera
        with self.lock:
            self._authorize(body)
            camera = body.get('camera')
            if not isinstance(camera, dict) or set(camera) - {'yaw', 'pitch', 'distance', 'pan', 'target', 'preset'}:
                raise ValueError('Invalid camera fields')
            validated_camera((self.last_result or {}).get('camera', {}), camera)
            self.pending_camera = camera
            self.wake.set()
            return {'accepted': True}

    def control(self, body):
        with self.lock:
            self._authorize(body, host=True)
            action = body.get('action')
            if action == 'play':
                if self.phase not in {'paused', 'running'}:
                    raise ValueError('等待引擎就绪后再开始。')
                self.playing, self.phase, self.next_tick = True, 'running', 0.
            elif action == 'pause':
                self.playing = False
                if self.phase == 'running':
                    self.phase = 'paused'
            elif action == 'mode':
                if self.traffic_options is not None and body.get('mode') == 'manual':
                    raise ValueError('Automatic traffic experiment: manual driving is deferred')
                self.room.set_mode(body.get('vehicle_id'), body.get('mode'),
                    session_id=body['session_id'], epoch=body['epoch'], host_token=body['host_token'])
            elif action == 'close':
                self.playing = False
                if self.phase == 'error' and not self.job:
                    self.phase = 'paused'
                self.pending_close = True
            elif action == 'reset':
                if self.job:
                    raise ValueError('请暂停并等待当前物理帧完成后重建房间。')
                key, version = self.scene_key, self.version
                self.active, self.playing = False, False
                # Native open closes the old environment. A fresh room gets
                # new credentials so delayed inputs cannot steer new vehicles.
                return self.open(key, version, traffic=deepcopy(self.traffic_options))
            else:
                raise ValueError('Unsupported room control')
            self.wake.set()
            return {'accepted': True, 'playing': self.playing, 'phase': self.phase}

    def observation(self, session_id, vehicle_id='ego'):
        with self.lock:
            # A completed physics decision may have failed while rendering
            # its frame. Do not advertise the previous pose as current ground
            # truth merely because an older successful result is cached.
            if self.job:
                try:
                    self._collect_completion()
                except Exception as error:
                    self.error, self.phase, self.playing = str(error), 'error', False
                    raise ValueError('Current physical observation is unavailable; reset or close the failed room') from error
            if self.phase not in {'paused', 'running'}:
                raise ValueError('Current physical observation is unavailable while the room is starting, closing or failed')
            if not self.active or session_id != self.identity['session_id'] or not self.last_result:
                raise ValueError('The requested room is not ready or has been replaced')
            result = self.last_result
            interactive = result['interactive']
            vehicles = interactive['vehicles']
            if vehicle_id not in vehicles:
                raise ValueError('Unknown interactive vehicle')
            own = deepcopy(vehicles[vehicle_id])
            return {'schema': 'interactive-planning-observation-1.0',
                    'session_id': session_id, 'epoch': self.identity['epoch'],
                    'scene_id': self.scene_key, 'step': result['step'],
                    'sim_time_s': interactive.get('sim_time_s', result['step'] * .1),
                    'state_id': result.get('state_id'), 'frame_id': result.get('frame_id'), 'vehicle_id': vehicle_id,
                    'own_vehicle': own, 'vehicles': deepcopy(vehicles),
                    'current_objects': deepcopy(interactive.get('static_actors', [])) +
                                       [deepcopy(v) for key, v in vehicles.items() if key != vehicle_id],
                    'map_version': result['map_features_sha256'],
                    'coordinate_reference': {'frame': 'source_city_xy', 'units': 'm', 'heading': 'radians', 'speed': 'm/s'},
                    'observation_scope': 'current_simulator_ground_truth_no_sensor_occlusion',
                    'recorded_future_tracks_included': False}

    def perception(self, session_id, vehicle_id='ego'):
        from .simulation_traffic import vehicle_perception
        with self.lock:
            observation = self.observation(session_id, vehicle_id)
            measured = vehicle_perception(self.last_result['interactive'], vehicle_id,
                clearance_queries=self.observed_maps[vehicle_id].clearance_queries())
            return {**{k: observation[k] for k in ('session_id', 'epoch', 'scene_id', 'step',
                    'sim_time_s', 'state_id', 'vehicle_id', 'coordinate_reference', 'map_version')},
                    'schema': 'interactive-vehicle-perception-1.0', **measured}

    def planning_input(self, session_id, vehicle_id='ego', policy='observed'):
        with self.lock:
            perception = self.perception(session_id, vehicle_id)
            layer = self.observed_maps[vehicle_id].snapshot(policy)
            return {**perception, 'schema':'interactive-planning-input-1.0',
                    'road_version':layer['road_version'], 'state_layer':layer,
                    'planning_version':layer['road_version']+':'+layer['state_version']}

    def planning_map(self, session_id):
        context = self.map(session_id)
        return {k: context[k] for k in ('session_id', 'epoch', 'scene_id', 'map_features',
                                       'map_version', 'coordinate_reference')}

    def map(self, session_id):
        from .simulation_bridge import feature_sha256
        with self.lock:
            if not self.active or session_id != self.identity['session_id']:
                raise ValueError('The requested room has been replaced')
            payload = self.server.scene(self.scene_key)
            features = payload['maps'][self.version]
            return {'schema': 'interactive-planning-map-1.0', 'session_id': session_id,
                    'epoch': self.identity['epoch'], 'scene_id': self.scene_key,
                    'map_features': deepcopy(features), 'map_version': feature_sha256(features),
                    'coordinate_reference': {'frame': payload['coordinate'], 'map': payload['map'], 'units': 'm'},
                    'state_prior': deepcopy(payload.get('state_overview')),
                    'static_actors': deepcopy((self.last_result or {}).get('interactive', {}).get('static_actors', [])),
                    'offline_prior': True, 'source': deepcopy(payload['source']),
                    'recorded_future_tracks_included': False}

    def close(self):
        self.stopping = True
        self.wake.set()
        self.thread.join(timeout=5)
