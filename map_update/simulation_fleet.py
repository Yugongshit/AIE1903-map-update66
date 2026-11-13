"""Physical recipient cars driven by independent shared-map controllers.

The recorded source car remains owned by ScenarioEnv. B/C are Bullet vehicles:
only their initial spawn/reset sets a pose; every subsequent pose comes from
MetaDrive physics. Map delivery changes knowledge and steering, never poses.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .simulation_runtime import map_xy_digest, scenario_position
from .simulation_states import state_overview, state_snapshot


FLEET_COLORS = {'B': (0.20, 0.70, 1.0, 1.0), 'C': (0.85, 0.45, 1.0, 1.0)}


def _vehicle_class():
    from metadrive.component.vehicle.vehicle_type import TrafficDefaultVehicle
    return TrafficDefaultVehicle


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(name + ' requires two finite source-city coordinates')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(name + ' requires two finite source-city coordinates')
    return [float(v) for v in value]


def validated_fleet(config, loaded_features):
    """Validate trusted broker input before importing native vehicle classes."""
    if not isinstance(config, dict):
        raise ValueError('Fleet configuration must be an object')
    result = deepcopy(config)
    if result.get('mode') not in {'offline', 'online'}:
        raise ValueError('Fleet mode must be offline or online')
    if result.get('variant', 'full') not in {'old', 'structure', 'full'}:
        raise ValueError('Fleet variant must be old, structure or full')
    result.setdefault('variant', 'full')
    maps = result.get('maps')
    if not isinstance(maps, dict) or not all(isinstance(maps.get(k), dict) and maps[k] for k in ('original', 'updated')):
        raise ValueError('Fleet requires both verified source map versions')
    if map_xy_digest(maps['updated']) != map_xy_digest(loaded_features):
        raise ValueError('Fleet physical world must use the updated source map')
    missions = result.get('missions')
    if not isinstance(missions, list) or len(missions) != 2:
        raise ValueError('Fleet requires two independent recipient missions, B and C')
    if {m.get('vehicle_id') for m in missions if isinstance(m, dict)} != {'B', 'C'}:
        raise ValueError('Fleet recipient IDs must be B and C')
    for mission in missions:
        mission['position_scenario_m'] = _point(mission.get('position_scenario_m'), 'Initial position')
        mission['goal_scenario_m'] = _point(mission.get('goal_scenario_m'), 'Goal')
        heading = mission.get('heading_rad')
        if isinstance(heading, bool) or not isinstance(heading, (int, float)) or not math.isfinite(heading):
            raise ValueError('Initial heading must be finite radians')
        speed = mission.get('cruise_speed_m_s', 3.)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.2 <= speed <= 5.:
            raise ValueError('Fleet cruise speed must be between 0.2 and 5 m/s')
        mission['cruise_speed_m_s'] = float(speed)
    result['missions'] = sorted(missions, key=lambda m: m['vehicle_id'])
    return result


def checked_action(action):
    """A malformed controller output is never forwarded to native physics."""
    if not isinstance(action, (tuple, list)) or len(action) != 2:
        raise ValueError('Fleet controller must return steering and throttle')
    values = [float(value) for value in action]
    if not all(math.isfinite(value) and -1 <= value <= 1 for value in values):
        raise ValueError('Fleet controller action must be finite and within [-1, 1]')
    return values


def actor_state(actor, metadata):
    """Read a native actor in source-city coordinates, translating exactly once."""
    position = scenario_position(list(actor.position[:2]), metadata)
    length = float(getattr(actor, 'LENGTH', 1.) or 1.)
    width = float(getattr(actor, 'WIDTH', 1.) or 1.)
    return {'id': str(actor.id), 'position': position,
            'heading_rad': float(getattr(actor, 'heading_theta', 0.)),
            'length_m': length, 'width_m': width,
            'speed_m_s': float(getattr(actor, 'speed', 0.)),
            'type': type(actor).__name__}


def _route_points(route):
    return route.get('points', []) if isinstance(route, dict) else route or []


class FleetRuntime:
    """Own B/C lifecycle while the existing session continues to replay A."""
    def __init__(self, env, bound_export, config, source_features):
        self.env, self.bound = env, bound_export
        self.config = validated_fleet(config, source_features)
        self.missions = self.config['missions']
        self.events = []
        self._applied_events = set()
        self.vehicles, self.actions, self.traces = {}, {}, {}
        self.visual_node = None
        self.service = None
        self._synchronized_step = None
        try:
            self.reset()
        except BaseException:
            self.before_reset()
            raise

    @property
    def metadata(self):
        return self.env.engine.data_manager.current_scenario['metadata']

    def reset(self):
        """Called after ScenarioEnv.reset; historical deliveries are replayed."""
        from .simulation_shared_map import SharedMapService
        vehicle_class = _vehicle_class()
        self._applied_events = set()
        self._synchronized_step = None
        self.actions, self.traces, self.vehicles = {}, {}, {}
        self.vehicle_states = {}
        self.collision_steps = {}
        self.service = SharedMapService(self.config['maps']['original'], self.config['maps']['updated'],
            scene_id=self.env.engine.data_manager.current_scenario['id'],
            mode=self.config['mode'], variant=self.config['variant'])
        offset = self.metadata.get('old_origin_in_current_coordinate', [0., 0.])
        for index, mission in enumerate(self.missions):
            identifier = mission['vehicle_id']
            cfg = deepcopy(self.env.config['vehicle_config'])
            cfg.update({'navigation_module': None, 'show_navi_mark': False, 'show_dest_mark': False,
                        'show_line_to_dest': False, 'random_color': False, 'overtake_stat': False})
            vehicle = self.env.engine.spawn_object(vehicle_class,
                name='shared-map-recipient-' + identifier, random_seed=100 + index,
                vehicle_config=cfg,
                position=[mission['position_scenario_m'][i] + float(offset[i]) for i in (0, 1)],
                heading=mission['heading_rad'])
            self.vehicles[identifier] = vehicle
            self.traces[identifier] = []
            self.collision_steps[identifier] = []
            self.service.subscribe(identifier, mission['goal_scenario_m'],
                cruise_speed_m_s=mission['cruise_speed_m_s'], enabled=True)
        self._record_poses()
        self._synchronize()

    def before_reset(self):
        """Native reset requires every custom physics body and node removed."""
        if self.visual_node is not None:
            self.visual_node.removeNode()
            self.visual_node = None
        if self.vehicles:
            self.env.engine.clear_objects([v.id for v in self.vehicles.values()], force_destroy=True)
            self.vehicles = {}

    close = before_reset

    def _observations(self):
        bundle = self.bound.get('states')
        if bundle is None:
            return [], None
        if self.config['mode'] == 'offline':
            if self.config['variant'] != 'full':
                return [], None
            snapshot = state_overview(bundle)
        else:
            snapshot = state_snapshot(bundle, int(self.env.episode_step))
        return snapshot.get('objects', []), snapshot.get('source_timestamp_ns')

    def _publish(self, *, structure=False):
        step = int(self.env.episode_step)
        objects, timestamp = self._observations()
        self.service.publish(step, state_objects=objects, source_timestamp_ns=timestamp,
            observed_step=step, offline_prior=self.config['mode'] == 'offline',
            publish_structure=structure)
        for identifier in self.vehicles:
            self.service.deliver(identifier, step)

    def _synchronize(self):
        step = int(self.env.episode_step)
        if self._synchronized_step != step:
            if self.config['mode'] == 'online' or step == 0:
                self._publish(structure=self.config['mode'] == 'offline' and self.config['variant'] != 'old')
            for index, event in enumerate(self.events):
                if event['step'] == step and index not in self._applied_events:
                    self._apply_command(event['command'])
                    self._applied_events.add(index)
            self._synchronized_step = step
        self._update_actions()

    def _actors(self):
        # This is a deliberately short-range local sensor guard, distinct from
        # shared-map knowledge. It can brake for a nearby unshared road user.
        result = []
        for obj in self.env.engine.get_objects().values():
            if obj is self.env.agent or (hasattr(obj, 'position') and hasattr(obj, 'LENGTH') and hasattr(obj, 'WIDTH')):
                try:
                    result.append(actor_state(obj, self.metadata))
                except (TypeError, ValueError, AttributeError):
                    continue
        return result

    def _update_actions(self):
        actors = self._actors()
        step = int(self.env.episode_step)
        for identifier, vehicle in self.vehicles.items():
            own = actor_state(vehicle, self.metadata)
            nearby = [a for a in actors if a['id'] != vehicle.id and math.dist(a['position'], own['position']) <= 25.]
            state = dict(own, step=step, wheelbase_m=float(vehicle.FRONT_WHEELBASE + vehicle.REAR_WHEELBASE),
                         max_steering_rad=math.radians(float(vehicle.max_steering)), nearby_actors=nearby)
            self.vehicle_states[identifier] = state
            self.actions[identifier] = checked_action(self.service.action(identifier, state))
            if self.collision_steps[identifier]:
                self.actions[identifier] = [0., -1.]

    def before_step(self):
        for identifier, vehicle in self.vehicles.items():
            vehicle.before_step(self.actions[identifier])

    def after_step(self):
        for identifier, vehicle in self.vehicles.items():
            vehicle.after_step()
            if any(bool(getattr(vehicle, name, False)) for name in
                   ('crash_vehicle', 'crash_object', 'crash_human', 'crash_building')):
                self.collision_steps[identifier].append(int(self.env.episode_step))
        self._record_poses()
        self._synchronize()

    def _record_poses(self):
        for identifier, vehicle in self.vehicles.items():
            position = scenario_position(list(vehicle.position[:2]), self.metadata)
            self.traces[identifier].append({'step': int(self.env.episode_step),
                'position_scenario_m': position, 'position_city_m': list(position),
                'speed_m_s': float(vehicle.speed)})

    def command(self, message):
        action = message.get('action')
        if action not in {'publish', 'subscription', 'deliver'}:
            raise ValueError('Unknown fleet command')
        if action == 'publish' and self.config['mode'] != 'online':
            raise ValueError('Offline prior variants are fixed; launch an online sharing session to publish')
        identifier = message.get('vehicle_id')
        if action != 'publish' and identifier not in self.vehicles:
            raise ValueError('Choose recipient B or C')
        if action == 'subscription' and type(message.get('enabled')) is not bool:
            raise ValueError('Subscription enabled must be boolean')
        command = {'action': action}
        if action != 'publish':
            command['vehicle_id'] = identifier
        if action == 'subscription':
            command['enabled'] = message['enabled']
        step = int(self.env.episode_step)
        # An interaction after rewind branches delivery history at this frame.
        self.events = [event for event in self.events if event['step'] <= step]
        self.events.append({'step': step, 'command': command})
        self._apply_command(command)
        self._applied_events = set(range(len(self.events)))
        self._update_actions()

    def _apply_command(self, command):
        if command['action'] == 'publish':
            self._publish(structure=True)
        elif command['action'] == 'subscription':
            self.service.set_subscription(command['vehicle_id'], command['enabled'])
            if command['enabled']:
                self.service.deliver(command['vehicle_id'], int(self.env.episode_step))
        else:
            self.service.deliver(command['vehicle_id'], int(self.env.episode_step))

    def report(self):
        service = self.service.status()
        per_vehicle = service.get('per_vehicle', {})
        vehicles = {}
        planning_inputs = {}
        for mission in self.missions:
            identifier = mission['vehicle_id']
            vehicle = self.vehicles[identifier]
            native = actor_state(vehicle, self.metadata)
            record = deepcopy(per_vehicle.get(identifier, {}))
            current_route = record.get('current_route', {})
            previous_route = record.get('previous_route', {})
            record.update(vehicle_id=identifier, native_object_id=vehicle.id,
                position_scenario_m=native['position'], position_city_m=native['position'],
                heading_rad=native['heading_rad'], speed_m_s=native['speed_m_s'],
                length_m=native['length_m'], width_m=native['width_m'],
                goal_scenario_m=mission['goal_scenario_m'], goal_city_m=mission['goal_scenario_m'],
                actual_trace=deepcopy(self.traces[identifier]), action=list(self.actions[identifier]),
                current_route_points_city=deepcopy(_route_points(current_route)),
                previous_route_points_city=deepcopy(_route_points(previous_route)),
                crash_vehicle=bool(getattr(vehicle, 'crash_vehicle', False)),
                crash_object=bool(getattr(vehicle, 'crash_object', False)),
                crash_human=bool(getattr(vehicle, 'crash_human', False)),
                crash_building=bool(getattr(vehicle, 'crash_building', False)),
                collision_contact_steps=list(self.collision_steps[identifier]),
                collision_stop=bool(self.collision_steps[identifier]),
                motion_source='metadrive_bullet_physics', uses_recorded_trajectory=False,
                color=list(FLEET_COLORS[identifier]))
            if self.collision_steps[identifier]:
                record['stopped_reason'] = 'native_collision_stop'
            vehicles[identifier] = record
            planning_input = self.service.planning_input(identifier, self.vehicle_states[identifier], include_map=False)
            # The immutable frame cache binds this compact per-car knowledge;
            # the broker restores its verified source map on export requests.
            planning_input.pop('map_features', None)
            planning_input['local_sensing_scope'] = {
                'source': 'native_actor_poses_in_fixed_preview_world', 'radius_m': 25.,
                'occlusion_modelled': False, 'is_new_perception_algorithm_output': False}
            planning_inputs[identifier] = planning_input
        source = actor_state(self.env.agent, self.metadata)
        return {'enabled': True, 'mode': self.config['mode'], 'variant': self.config['variant'],
                'world_map_version': 'updated', 'coordinate_frame': 'source_city_xy_m',
                'source_vehicle': dict(source, vehicle_id='A', position_city_m=source['position'],
                    observation_source='recorded_source_observations', motion_source='recorded_replay'),
                'vehicles': vehicles, 'service': service, 'planning_inputs': planning_inputs,
                'delivery_history': deepcopy(self.events),
                'static_preview_is_planner_knowledge': self.config['mode'] == 'offline' and self.config['variant'] == 'full',
                'static_environment_scope': 'fixed_full_scene_preview',
                'structural_publication': 'precomputed_algorithm_result_manually_published' if self.config['mode'] == 'online' else 'offline_precomputed_prior',
                'sensor_guard': 'idealized_local_25m_actor_positions_without_occlusion_not_shared_map',
                'replay_semantics': 'seeded_physics_and_recorded_delivery_commands'}

    def refresh_visuals(self):
        """Draw routes/traces and labelled physical cars, only for rendered frames."""
        from panda3d.core import LineSegs, TextNode, TransparencyAttrib
        from .simulation_visuals import dashed_segments
        if self.visual_node is not None:
            self.visual_node.removeNode()
        self.visual_node = self.env.engine.render.attachNewNode('shared-map-fleet')
        # Source road/vehicle PBR shaders turn annotation vertex colors white.
        # Keep these non-colliding overlays in the same unlit pass as map edits.
        self.visual_node.setLightOff(10)
        self.visual_node.setShaderOff(10)
        per_vehicle = self.service.status()['per_vehicle']
        offset = self.metadata.get('old_origin_in_current_coordinate', [0., 0.])
        for identifier, row in per_vehicle.items():
            color = FLEET_COLORS[identifier]
            for name, points, alpha, width, z in [
                ('previous', _route_points(row.get('previous_route')), .7, 2., .15),
                ('planned', _route_points(row.get('current_route')), .6, 2., .20),
                ('actual', [p['position_city_m'] for p in self.traces[identifier]], 1., 4., .30),
            ]:
                if len(points) < 2:
                    continue
                lines = LineSegs(identifier + '-' + name)
                line_color = (1., .7, .2) if name == 'previous' else color[:3]
                lines.setColor(*line_color, alpha)
                lines.setThickness(width)
                paths = dashed_segments(points) if name == 'previous' else [points]
                for path in paths:
                    lines.moveTo(path[0][0] + offset[0], path[0][1] + offset[1], z)
                    for point in path[1:]:
                        lines.drawTo(point[0] + offset[0], point[1] + offset[1], z)
                line_node = self.visual_node.attachNewNode(lines.create())
                line_node.setTransparency(TransparencyAttrib.MAlpha)
            x, y = self.vehicles[identifier].position[:2]
            label = TextNode('recipient-' + identifier)
            label.setText(identifier + '  shared-map planner')
            label.setTextColor(*color)
            label.setAlign(TextNode.ACenter)
            label.setShadow(.06, .06)
            label.setShadowColor(0., 0., 0., 1.)
            node = self.visual_node.attachNewNode(label)
            node.setTransparency(TransparencyAttrib.MAlpha)
            node.setTwoSided(True)
            node.setDepthTest(False)
            node.setDepthWrite(False)
            node.setBin('fixed', 71)
            node.setLightOff(10)
            node.setShaderOff(10)
            node.setPos(float(x), float(y), 3.)
            node.setScale(.8)
            node.setBillboardPointEye()
            outline = LineSegs(identifier + '-outline')
            outline.setColor(*color)
            outline.setThickness(3.)
            for i in range(17):
                angle = math.pi * i / 8
                point = (float(x) + 2.8 * math.cos(angle), float(y) + 2.8 * math.sin(angle), .3)
                outline.moveTo(*point) if i == 0 else outline.drawTo(*point)
            self.visual_node.attachNewNode(outline.create())
