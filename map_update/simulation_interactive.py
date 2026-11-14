"""Opt-in live six-car interaction, independent of recorded driving policies.

P0 (the existing ego) waits for a player or external planner. P1..P5 can use
independent source-lane controllers, player controls, or external actions. All
movement is produced by MetaDrive/Bullet; only reset initializes poses.
"""
from copy import deepcopy
import math
import time

from shapely.geometry import LineString, Polygon

from .simulation_planner import LaneGraphController, _length, _project, _slice
from .simulation_runtime import scenario_position


IDS = ('ego', 'P1', 'P2', 'P3', 'P4', 'P5')
COLORS = {'ego': (1., .72, .22, 1.), 'P1': (.2, .7, 1., 1.), 'P2': (.8, .45, 1., 1.),
          'P3': (.25, 1., .55, 1.), 'P4': (1., .45, .65, 1.), 'P5': (.3, .95, .95, 1.)}
BRAKE = [0., -1.]


def checked_action(action):
    if not isinstance(action, (list, tuple)) or len(action) != 2:
        raise ValueError('Action requires [steering, throttle]')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not -1 <= v <= 1 for v in action):
        raise ValueError('Action values must be finite numbers in [-1, 1]')
    return [float(v) for v in action]


def validated_controls(controls, ids=IDS):
    """Validate the entire batch before changing any car's control mode."""
    if not isinstance(controls, dict) or set(controls) - set(ids):
        raise ValueError('Controls must address ego or P1..P5')
    result = {}
    for identifier, row in controls.items():
        if not isinstance(row, dict) or set(row) - {'mode', 'action', 'expires_at_monotonic'}:
            raise ValueError('Vehicle controls accept mode, action and expiry only')
        item = {}
        if 'mode' in row:
            if row['mode'] not in {'ai', 'manual', 'external'}:
                raise ValueError('Vehicle mode must be ai, manual or external')
            if identifier == 'ego' and row['mode'] == 'ai':
                raise ValueError('P0 requires player or external Planning control')
            item['mode'] = row['mode']
        if 'action' in row:
            item['action'] = checked_action(row['action'])
        if 'expires_at_monotonic' in row:
            expiry = row['expires_at_monotonic']
            if isinstance(expiry, bool) or not isinstance(expiry, (float, int)) or not math.isfinite(expiry) or expiry < 0:
                raise ValueError('Action expiry must be a finite nonnegative monotonic timestamp')
            item['expires_at_monotonic'] = float(expiry)
        result[identifier] = item
    return result


def validated_missions(config):
    if not isinstance(config, dict) or not isinstance(config.get('missions'), list):
        raise ValueError('Interactive launch requires five source-lane missions')
    rows = deepcopy(config['missions'])
    count = config.get('traffic', {}).get('vehicle_count', 5)
    if type(count) is not int or not 1 <= count <= 12:
        raise ValueError('Traffic count must be 1..12')
    if len(rows) != count or {row.get('vehicle_id') for row in rows if isinstance(row, dict)} != {f'P{i}' for i in range(1,count+1)}:
        raise ValueError('Interactive missions must identify each configured vehicle exactly once')
    for row in rows:
        for key in ('position_scenario_m', 'goal_scenario_m'):
            point = row.get(key)
            if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
                    isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in point):
                raise ValueError(key + ' requires two finite source-city coordinates')
        heading = row.get('heading_rad')
        if isinstance(heading, bool) or not isinstance(heading, (float, int)) or not math.isfinite(heading):
            raise ValueError('Mission heading must be finite radians')
        speed = row.get('cruise_speed_m_s', 2.5)
        if isinstance(speed, bool) or not isinstance(speed, (float, int)) or not math.isfinite(speed) or not .2 <= speed <= 5:
            raise ValueError('AI cruise speed must be between 0.2 and 5 m/s')
        row['cruise_speed_m_s'] = float(speed)
        goals = row.get('patrol_goals_scenario_m', [row['goal_scenario_m']])
        if not isinstance(goals, list) or not goals or type(row.get('patrol_loop', False)) is not bool:
            raise ValueError('Patrol goals require a nonempty list and a boolean loop flag')
        for point in goals:
            if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
                    isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in point):
                raise ValueError('Patrol goals require finite source-city XY points')
        if list(goals[0]) != list(row['goal_scenario_m']):
            raise ValueError('The first patrol goal must be the configured mission goal')
        row['patrol_goals_scenario_m'] = goals
    return {row['vehicle_id']: row for row in rows}


def native_environment(base):
    """Remove recorded-trajectory observations, reward and navigation from ego."""
    import gymnasium as gym
    import numpy as np
    from metadrive.obs.observation_base import BaseObservation

    class ActualVehicleObservation(BaseObservation):
        @property
        def observation_space(self):
            return gym.spaces.Box(-np.inf, np.inf, shape=(5,), dtype=np.float32)

        def observe(self, vehicle):
            return np.asarray([*vehicle.position[:2], *vehicle.velocity[:2], vehicle.heading_theta], dtype=np.float32)

    class InteractiveScenarioEnv(base):
        def reward_function(self, vehicle_id):
            return 0., {'interactive_control': True}

        def cost_function(self, vehicle_id):
            return 0., {}

        def done_function(self, vehicle_id):
            # Live control is ended/reset explicitly. A recorded destination or
            # source recording length must not remove a controlled vehicle.
            return False, {'arrive_dest': False, 'max_step': False}

    return InteractiveScenarioEnv, ActualVehicleObservation


def _vehicle_class():
    from metadrive.component.vehicle.vehicle_type import TrafficDefaultVehicle
    return TrafficDefaultVehicle


def actor_state(vehicle, metadata, identifier=None):
    position = scenario_position(vehicle.position, metadata)
    heading, height = float(vehicle.heading_theta), float(getattr(vehicle, 'HEIGHT', 1.) or 1.)
    # The road-plane position is useful for planning, but is not a rendered
    # chassis pose: Bullet can lift, pitch and roll a car during an impact.
    # Export the actual collision-box center and a right-handed length/width/
    # height basis. Vehicle boxes use local +Y forward, obstacle boxes +X.
    c, s = math.cos(heading), math.sin(heading)
    origin_xyz, center = [*position, 0.], [*position, height/2]
    basis = {'forward': [c, s, 0.], 'left': [-s, c, 0.], 'up': [0., 0., 1.]}
    if hasattr(vehicle, 'origin') and hasattr(vehicle, 'body'):
        quat = vehicle.origin.getQuat()
        local_forward = (0., 1., 0.) if hasattr(vehicle, 'FRONT_WHEELBASE') else (1., 0., 0.)
        local_left = (-1., 0., 0.) if hasattr(vehicle, 'FRONT_WHEELBASE') else (0., 1., 0.)
        basis = {key: [float(v) for v in quat.xform(axis)] for key, axis in
                 (('forward', local_forward), ('left', local_left), ('up', (0., 0., 1.)))}
        origin_xyz = [*position, float(vehicle.origin.getZ())]
        offset = quat.xform(vehicle.body.getShapeTransform(0).getPos())
        center = [origin_xyz[i]+float(offset[i]) for i in range(3)]
    return {'vehicle_id': identifier or str(vehicle.id), 'native_object_id': str(vehicle.id),
            'position_scenario_m': position, 'position_city_m': position,
            'position_3d_city_m': origin_xyz, 'chassis_center_city_m': center,
            'orientation_basis_city': basis,
            'roll_rad': float(getattr(vehicle, 'roll', 0.)), 'pitch_rad': float(getattr(vehicle, 'pitch', 0.)),
            'position_runtime_m': [float(v) for v in vehicle.position[:2]],
            'velocity_m_s': [float(v) for v in vehicle.velocity[:2]],
            'heading_rad': heading, 'speed_m_s': float(vehicle.speed),
            'length_m': float(vehicle.LENGTH), 'width_m': float(vehicle.WIDTH),
            'height_m': height, 'type': type(vehicle).__name__}


def physical_contacts(world, owned_bodies, actor_bodies, *, step):
    """Read solved Bullet manifolds, excluding normal wheel/road contact.

    Unlike a latched crash flag this reports contacts still present after the
    current physics decision. Impulse comes from Bullet's actual solver; it is
    not an invented damage value or a substitute for physical response.
    """
    contacts = {identifier: {} for identifier in owned_bodies.values()}
    for manifold in world.getManifolds():
        nodes = (manifold.getNode0(), manifold.getNode1())
        points = [point for point in manifold.getManifoldPoints() if point.getDistance() <= 1e-5]
        if not points:
            continue
        for index, node in enumerate(nodes):
            identifier, other = owned_bodies.get(node), actor_bodies.get(nodes[1-index])
            if identifier is None or other is None:
                continue
            key = other['native_object_id']
            impulse = sum(max(0., float(point.getAppliedImpulse())) for point in points)
            row = contacts[identifier].setdefault(key, dict(other, step=int(step),
                normal_impulse_ns=0., distance_m=float(points[0].getDistance())))
            row['normal_impulse_ns'] += impulse
            row['distance_m'] = min(row['distance_m'], *(float(point.getDistance()) for point in points))
    return {identifier: list(rows.values()) for identifier, rows in contacts.items()}


def _footprint(actor):
    x, y = actor['position_city_m']
    heading = actor['heading_rad']
    c, s = math.cos(heading), math.sin(heading)
    length, width = actor['length_m'], actor['width_m']
    return Polygon([(x+c*dx-s*dy, y+s*dx+c*dy) for dx, dy in
                    ((-length/2, -width/2), (length/2, -width/2), (length/2, width/2), (-length/2, width/2))])


def braking_actor(own, others, route_points=()):
    """Conservative near-field braking for current physical actors, not tracks."""
    speed = own['speed_m_s']
    horizon = own['length_m']/2 + 1.5 + speed*1.0 + speed*speed/4.
    position = own['position_city_m']
    if len(route_points) >= 2:
        progress = _project(position, route_points)[1]
        end = min(_length(route_points), progress+horizon)
        points = _slice(route_points, progress, end) if end > progress+1e-5 else []
    else:
        heading = own['heading_rad']
        points = [position, [position[0]+horizon*math.cos(heading), position[1]+horizon*math.sin(heading)]]
    if len(points) < 2:
        return None
    corridor = LineString(points).buffer(own['width_m']/2+.35, cap_style=2)
    for actor in others:
        if actor['native_object_id'] == own['native_object_id']:
            continue
        if math.dist(position, actor['position_city_m']) <= 30 and corridor.intersects(_footprint(actor)):
            return {'vehicle_id': actor['vehicle_id'], 'native_object_id': actor['native_object_id'],
                    'distance_m': math.dist(position, actor['position_city_m']), 'horizon_m': horizon}
    return None


class InteractiveRuntime:
    def __init__(self, env, config, source_features, *, map_sha256):
        self.env = env
        self.missions = validated_missions(config)
        self.ids = ('ego', *[f'P{i}' for i in range(1,len(self.missions)+1)])
        self.cyclic_path = config.get('cyclic_path')
        self.traffic_area = deepcopy(config.get('traffic_area'))
        from .simulation_traffic import traffic_options
        self.traffic = traffic_options({k: v for k, v in config['traffic'].items() if k not in {'profile', 'synthetic'}}) if config.get('traffic') else None
        self.source_features, self.map_sha256 = source_features, map_sha256
        self.visual_node = None
        self.vehicles = {}
        try:
            self.reset()
        except BaseException:
            self.close()
            raise

    @property
    def metadata(self):
        return self.env.engine.data_manager.current_scenario['metadata']

    def reset(self):
        self.vehicles = {'ego': self.env.agent}
        self.env.agent.set_velocity([0., 0.])
        self.modes = {identifier: 'external' if identifier == 'ego' else 'ai' for identifier in self.ids}
        self.actions = {identifier: list(BRAKE) for identifier in self.ids}
        self.requested = {identifier: list(BRAKE) for identifier in self.ids}
        self.expires = {identifier: None for identifier in self.ids}
        self.reasons = {identifier: 'waiting_for_action' if identifier == 'ego' else None for identifier in self.ids}
        self.collisions = {identifier: [] for identifier in self.ids}
        self.current_contacts = {identifier: [] for identifier in self.ids}
        self.last_collision = {identifier: None for identifier in self.ids}
        self.collision_event_count = {identifier: 0 for identifier in self.ids}
        self._previous_contact_keys = {identifier: set() for identifier in self.ids}
        self._speeds_before = {identifier: 0. for identifier in self.ids}
        self.controllers, self.diagnostics, self.braking = {}, {}, {}
        self.patrol_indices = {key: 0 for key in self.ids[1:]}
        self.completed_goals = {key: 0 for key in self.ids[1:]}
        self.goal_counted = {key: False for key in self.ids[1:]}
        offset = self.metadata.get('old_origin_in_current_coordinate', [0., 0.])
        for index, identifier in enumerate(self.ids[1:]):
            mission = self.missions[identifier]
            cfg = deepcopy(self.env.config['vehicle_config'])
            cfg.update({'navigation_module': None, 'show_navi_mark': False, 'show_dest_mark': False,
                        'show_line_to_dest': False, 'random_color': False, 'overtake_stat': False,
                        'spawn_velocity': None})
            self.vehicles[identifier] = self.env.engine.spawn_object(_vehicle_class(),
                name='interactive-' + identifier, random_seed=201+index, vehicle_config=cfg,
                position=[mission['position_scenario_m'][i]+float(offset[i]) for i in (0, 1)],
                heading=mission['heading_rad'])
            self.controllers[identifier] = self._controller(identifier)

    def _controller(self, identifier):
        from .simulation_traffic import CycleFollower
        mission = self.missions[identifier]
        if mission.get('cycle_follower'):
            return CycleFollower(self.cyclic_path, mission['cruise_speed_m_s'])
        return LaneGraphController(mission['cruise_speed_m_s'])

    def close(self):
        if self.visual_node is not None:
            self.visual_node.removeNode()
            self.visual_node = None
        extras = [vehicle.id for key, vehicle in self.vehicles.items() if key != 'ego']
        if extras:
            self.env.engine.clear_objects(extras, force_destroy=True)
        self.vehicles = {}

    def controls(self, controls):
        updates = validated_controls(controls, self.ids)
        # Actions are leases for this tick only. The broker must renew player
        # and external-planner commands; an omitted control brakes immediately.
        for identifier in self.ids:
            row = updates.get(identifier, {})
            mode = row.get('mode', self.modes[identifier])
            if mode != self.modes[identifier] and identifier in self.missions:
                self.controllers[identifier] = self._controller(identifier)
                self.diagnostics.pop(identifier, None)
            self.modes[identifier] = mode
            self.requested[identifier] = list(row.get('action', BRAKE))
            self.expires[identifier] = row.get('expires_at_monotonic')
            self.reasons[identifier] = None if mode == 'ai' or 'action' in row else 'waiting_for_action'

    def actors(self):
        aliases = {vehicle.id: identifier for identifier, vehicle in self.vehicles.items()}
        manager = getattr(self.env.engine, 'traffic_manager', None)
        definitions = getattr(manager, 'persistence_definitions', {})
        aliases.update({value: key for key, value in getattr(manager, '_scenario_id_to_obj_id', {}).items()})
        result = []
        for vehicle in self.env.engine.get_objects().values():
            if hasattr(vehicle, 'LENGTH') and hasattr(vehicle, 'WIDTH') and hasattr(vehicle, 'position'):
                row = actor_state(vehicle, self.metadata, aliases.get(vehicle.id))
                if row['vehicle_id'] in definitions:
                    row['type'] = definitions[row['vehicle_id']]['type']
                elif row['vehicle_id'] in self.ids:
                    row['type'] = 'VEHICLE'
                result.append(row)
        return result

    def goal(self, identifier):
        if self.missions[identifier].get('cycle_follower'):
            goal = self.diagnostics.get(identifier, {}).get('goal_scenario_m')
            if goal is not None:
                return goal
        return self.missions[identifier]['patrol_goals_scenario_m'][self.patrol_indices[identifier]]

    def before_step(self):
        actors = self.actors()
        use_idm = bool(self.traffic and self.traffic['following_model'] == 'idm' and self.cyclic_path)
        if use_idm:
            from .simulation_following import project_actors, current_leader, idm_acceleration, acceleration_action
            projections = project_actors(self.cyclic_path, [a for a in actors if a.get('type') == 'VEHICLE'])
        for identifier, vehicle in self.vehicles.items():
            own = actor_state(vehicle, self.metadata, identifier)
            self._speeds_before[identifier] = own['speed_m_s']
            action = list(self.requested[identifier])
            self.braking[identifier] = None
            if (self.modes[identifier] != 'ai' and self.expires[identifier] is not None
                    and time.monotonic() >= self.expires[identifier]):
                action = list(BRAKE)
                self.reasons[identifier] = 'action_expired'
            if self.modes[identifier] == 'ai':
                controller = self.controllers[identifier]
                state = {'step': int(self.env.episode_step), 'position_runtime_m': own['position_city_m'],
                    'heading_rad': own['heading_rad'], 'speed_m_s': own['speed_m_s'],
                    'map_features_runtime': self.source_features, 'map_version': self.map_sha256,
                    'scenario_id': self.env.engine.data_manager.current_scenario['id'],
                    'runtime_origin_in_scenario_m': [0., 0.],
                    'goal_scenario_m': self.goal(identifier),
                    'wheelbase_m': float(vehicle.FRONT_WHEELBASE+vehicle.REAR_WHEELBASE),
                    'max_steering_rad': math.radians(float(vehicle.max_steering))}
                action = checked_action(controller(state))
                self.diagnostics[identifier] = controller.diagnostics()
                if self.missions[identifier].get('cycle_follower'):
                    route = self.diagnostics[identifier]
                    self.completed_goals[identifier] = int(route['travelled_m'] / (route['length_m']/2))
                    self.patrol_indices[identifier] = self.completed_goals[identifier] % 2
                if self.diagnostics[identifier]['status'] == 'goal_reached':
                    if not self.goal_counted[identifier]:
                        self.completed_goals[identifier] += 1
                        self.goal_counted[identifier] = True
                    mission = self.missions[identifier]
                    next_index = self.patrol_indices[identifier]+1
                    if next_index >= len(mission['patrol_goals_scenario_m']):
                        next_index = 0 if mission.get('patrol_loop') else None
                    if next_index is not None:
                        self.patrol_indices[identifier] = next_index
                        self.goal_counted[identifier] = False
                        state['goal_scenario_m'] = self.goal(identifier)
                        action = checked_action(controller(state))
                        self.diagnostics[identifier] = controller.diagnostics()
                self.reasons[identifier] = (self.diagnostics[identifier].get('reason')
                    if self.diagnostics[identifier]['status'] not in {'tracking', 'planned'} else None)
                if self.diagnostics[identifier]['status'] == 'goal_reached':
                    self.reasons[identifier] = 'goal_reached'
                if use_idm and self.missions[identifier].get('cycle_follower') and self.diagnostics[identifier]['status'] == 'tracking':
                    parameters = self.missions[identifier]['driver_parameters']
                    leader = current_leader(own, actors, self.cyclic_path, projections)
                    acceleration = idm_acceleration(own['speed_m_s'],
                        self.diagnostics[identifier]['target_speed_m_s'], leader, parameters)
                    action = [action[0], acceleration_action(acceleration, vehicle)]
                    self.diagnostics[identifier]['following'] = dict(model='idm', leader=leader,
                        desired_acceleration_m_s2=acceleration, parameters=deepcopy(parameters),
                        observation_range_m=50., emergency_override=False)
                hit = braking_actor(own, actors, self.diagnostics[identifier].get('points', []))
                if hit:
                    self.braking[identifier] = hit
                    action = [action[0], -1.]
                    self.reasons[identifier] = 'nearby_actor_braking'
                    if 'following' in self.diagnostics[identifier]:
                        self.diagnostics[identifier]['following']['emergency_override'] = True
            if self.traffic and identifier == 'P1' and self.modes[identifier] == 'ai':
                sim_time = self.env.episode_step * self.env.config['physics_world_step_size'] * self.env.config['decision_repeat']
                start = self.traffic['stop_start_s']
                if start <= sim_time < start + self.traffic['stop_duration_s']:
                    action = [action[0], -1.]
                    self.reasons[identifier] = 'synthetic_scheduled_stop'
                    if 'following' in self.diagnostics.get(identifier, {}):
                        self.diagnostics[identifier]['following']['scheduled_stop_override'] = True
            self.actions[identifier] = action
            if identifier != 'ego':
                vehicle.before_step(action)
        return self.actions['ego']

    def after_step(self):
        world = getattr(getattr(self.env.engine, 'physics_world', None), 'dynamic_world', None)
        if world is not None:
            aliases = {vehicle.id: identifier for identifier, vehicle in self.vehicles.items()}
            manager = getattr(self.env.engine, 'traffic_manager', None)
            definitions = getattr(manager, 'persistence_definitions', {})
            aliases.update({value: key for key, value in getattr(manager, '_scenario_id_to_obj_id', {}).items()})
            actors = {obj.body: {'object_id': str(aliases.get(obj.id, obj.id)),
                      'native_object_id': str(obj.id),
                      'type': definitions.get(aliases.get(obj.id), {}).get('type', type(obj).__name__)}
                      for obj in self.env.engine.get_objects().values()
                      if hasattr(obj, 'body') and hasattr(obj, 'LENGTH') and hasattr(obj, 'WIDTH')}
            self.current_contacts = physical_contacts(world,
                {vehicle.body: identifier for identifier, vehicle in self.vehicles.items()}, actors,
                step=self.env.episode_step)
        for identifier, vehicle in self.vehicles.items():
            if identifier != 'ego':
                vehicle.after_step()
            flags = {kind: bool(getattr(vehicle, 'crash_'+kind, False)) for kind in ('vehicle', 'object', 'human', 'building')}
            contacts = self.current_contacts[identifier]
            if contacts or any(flags.values()):
                self.collisions[identifier].append(int(self.env.episode_step))
                del self.collisions[identifier][:-100]
            # A brief impact can end between decisions; retain native collision
            # flags as evidence, without inventing an object ID or contact.
            keys = {row['native_object_id'] for row in contacts} or {
                'native_flag:'+kind for kind, hit in flags.items() if hit}
            previous = self._previous_contact_keys[identifier]
            previous_actors = {key for key in previous if not key.startswith('native_flag:')}
            if keys and (not previous or (contacts and previous_actors and keys-previous_actors)):
                self.collision_event_count[identifier] += 1
                self.last_collision[identifier] = {'step': int(self.env.episode_step),
                    'sim_time_s': float(self.env.episode_step*self.env.config['physics_world_step_size']*
                                        self.env.config['decision_repeat']),
                    'speed_before_m_s': self._speeds_before[identifier], 'speed_after_m_s': float(vehicle.speed),
                    'contacts': deepcopy(contacts), 'native_collision_flags': flags}
            self._previous_contact_keys[identifier] = keys

    def report(self):
        actors = self.actors()
        native_ids = {vehicle.id for vehicle in self.vehicles.values()}
        vehicles = {}
        for identifier, vehicle in self.vehicles.items():
            row = actor_state(vehicle, self.metadata, identifier)
            row.update(label='P0' if identifier == 'ego' else identifier, mode=self.modes[identifier],
                action=list(self.actions[identifier]), requested_action=list(self.requested[identifier]),
                expires_at_monotonic=self.expires[identifier],
                stopped_reason=self.reasons[identifier], braking=self.braking.get(identifier),
                collision={kind: bool(getattr(vehicle, 'crash_'+kind, False)) for kind in ('vehicle', 'object', 'human', 'building')},
                collision_contact_steps=list(self.collisions[identifier][-100:]),
                collision_active=bool(self.current_contacts[identifier]),
                current_contacts=deepcopy(self.current_contacts[identifier]),
                last_collision=deepcopy(self.last_collision[identifier]),
                collision_event_count=self.collision_event_count[identifier],
                motion_source='metadrive_bullet_physics', uses_recorded_trajectory=False,
                color=list(COLORS.get(identifier, COLORS['P1'])),
                route=deepcopy(self.diagnostics.get(identifier)) if self.modes[identifier] == 'ai' else None,
                route_scope=('controller_plan_not_prediction' if self.modes[identifier] == 'ai'
                             and self.diagnostics.get(identifier) else 'unavailable'),
                intent_source='builtin_lane_controller' if self.modes[identifier] == 'ai' else 'not_provided',
                goal_scenario_m=self.goal(identifier) if self.modes[identifier] == 'ai' else None,
                patrol_goal_index=self.patrol_indices.get(identifier) if self.modes[identifier] == 'ai' else None,
                completed_patrol_goals=self.completed_goals.get(identifier, 0))
            vehicles[identifier] = row
        step = int(self.env.episode_step)
        sim_time = step * self.env.config['physics_world_step_size'] * self.env.config['decision_repeat']
        static = [dict(row, id=row['vehicle_id'], position=row['position_city_m'],
                       heading=row['heading_rad'], length=row['length_m'], width=row['width_m'],
                       height=row['height_m'], static=True, source_scope='full_scene_offline_prior')
                  for row in actors if row['native_object_id'] not in native_ids]
        return {'traffic_profile': 'synthetic_cycle_v1' if self.traffic else 'source_presets',
                'traffic_area': deepcopy(self.traffic_area),
                'following_model': self.traffic['following_model'] if self.traffic else 'legacy',
                'synthetic_traffic': bool(self.traffic), 'enabled': True, 'step': step, 'recorded_step': 0, 'sim_time_s': float(sim_time), 'vehicles': vehicles,
                'static_actors': static,
                'map_features_sha256': self.map_sha256, 'coordinate_frame': 'source_city_xy_m',
                'recorded_moving_traffic': False, 'recorded_ego_policy': False,
                'source_recording_is_planning_input': False,
                'observation_source': 'current_simulator_actor_states', 'occlusion_modelled': False,
                'static_environment_scope': 'fixed_full_scene_offline_prior',
                'static_collision_model': 'fixed_source_dimensioned_boxes_including_cones',
                'collision_sampling': 'end_of_decision_bullet_manifolds_with_native_flags_for_transient_impacts',
                'collision_contact_scope': 'controlled_cars_and_source_static_objects',
                'vehicle_damage_modelled': False,
                'ai_scope': ('source_cycle_pure_pursuit_idm_with_emergency_guard'
                    if self.traffic and self.traffic['following_model']=='idm'
                    else 'independent_source_lane_following_and_nearby_actor_braking'),
                'ai_is_user_planning_module': False, 'traffic_signal_compliance': False,
                'reset_supported': True, 'seek_supported': False,
                'action_scope': 'one_tick_lease_brake_if_missing', 'ego_label': 'P0'}

    def refresh_visuals(self):
        from panda3d.core import LineSegs, TextNode, TransparencyAttrib
        if self.visual_node is not None:
            self.visual_node.removeNode()
        self.visual_node = self.env.engine.render.attachNewNode('interactive-car-labels')
        self.visual_node.setLightOff(10)
        self.visual_node.setShaderOff(10)
        for identifier, vehicle in self.vehicles.items():
            x, y = map(float, vehicle.position[:2])
            color = COLORS.get(identifier, COLORS['P1'])
            text = TextNode('interactive-label-'+identifier)
            text.setText(('P0' if identifier == 'ego' else identifier)+'  '+self.modes[identifier])
            text.setTextColor(*color)
            text.setAlign(TextNode.ACenter)
            text.setShadow(.06, .06)
            text.setShadowColor(0., 0., 0., 1.)
            node = self.visual_node.attachNewNode(text)
            node.setPos(x, y, 3.)
            node.setScale(.7)
            node.setBillboardPointEye()
            node.setTwoSided(True)
            node.setTransparency(TransparencyAttrib.MAlpha)
            node.setDepthTest(False)
            node.setDepthWrite(False)
            node.setBin('fixed', 71)
            ring = LineSegs('interactive-ring-'+identifier)
            ring.setColor(*color)
            ring.setThickness(3.)
            for index in range(17):
                angle = math.pi*index/8
                point = (x+2.8*math.cos(angle), y+2.8*math.sin(angle), .3)
                ring.moveTo(*point) if index == 0 else ring.drawTo(*point)
            self.visual_node.attachNewNode(ring.create())
