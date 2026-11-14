"""Synthetic, source-lane cyclic traffic; never a planner for the user's ego."""
from copy import deepcopy
import math
import random

from shapely.geometry import LineString, Point

from .simulation_interactive_scenarios import _graph, _cycle, vehicle_footprint
from .simulation_planner import _length, _point_at, _project, _angle


def traffic_options(value):
    if not isinstance(value, dict) or set(value) - {'seed', 'speed_m_s', 'stop_start_s', 'stop_duration_s', 'vehicle_count', 'speed_spread', 'area', 'following_model', 'driver_style', 'construction', 'construction_duration_s', 'construction_min_s', 'construction_max_s', 'construction_count_min', 'construction_count_max', 'stop_enabled', 'route_mode', 'pedestrians', 'pedestrian_count', 'incidents', 'recurring_construction'}:
        raise ValueError('Invalid cyclic traffic options')
    options = dict(route_mode='single', pedestrians=False, pedestrian_count=3, incidents=False, recurring_construction=False, seed=0, speed_m_s=2.5, stop_start_s=30., stop_duration_s=15., vehicle_count=5, speed_spread=0.,
                   area='isolated', following_model='legacy', driver_style='normal', construction=False, construction_min_s=45., construction_max_s=120., construction_count_min=2, construction_count_max=5, stop_enabled=True)
    options.update(value)
    if 'construction_duration_s' in value:
        legacy=options.pop('construction_duration_s')
        if (type(legacy) not in (int,float) or not math.isfinite(legacy) or not 5 <= legacy <= 120
                or 'construction_min_s' in value or 'construction_max_s' in value):
            raise ValueError('Use construction_min_s/max_s; do not mix legacy duration with a range')
        options.update(construction_min_s=max(5.,legacy*.75),construction_max_s=legacy*2.)
    if type(options['seed']) is not int or not 0 <= options['seed'] < 2**32:
        raise ValueError('Traffic seed must be a uint32')
    if type(options['vehicle_count']) is not int or not (0 if options['pedestrians'] is True else 1) <= options['vehicle_count'] <= 12:
        raise ValueError('Background vehicle count must be 1..12, or 0 with pedestrians')
    for key in ('construction_count_min','construction_count_max'):
        if type(options[key]) is not int or not 1 <= options[key] <= 8:
            raise ValueError('Construction counts must be integers in 1..8')
    if options['construction_count_min'] > options['construction_count_max']:
        raise ValueError('Construction count requires minimum <= maximum')
    if type(options['pedestrian_count']) is not int or not 1<=options['pedestrian_count']<=4:
        raise ValueError('Pedestrian count must be 1..4')
    for key in ('construction','stop_enabled','pedestrians','incidents','recurring_construction'):
        if type(options[key]) is not bool:
            raise ValueError('Traffic flag must be boolean: '+key)
    for key, allowed in [('route_mode',('single','network')), ('area', ('isolated','near_recording')), ('following_model', ('legacy','idm')),
                         ('driver_style', ('normal','cautious','assertive','mixed'))]:
        if options[key] not in allowed:
            raise ValueError('Invalid traffic option: '+key)
    for key, low, high in [('speed_m_s', .5, 3.), ('stop_start_s', 5., 3600.), ('stop_duration_s', 1., 120.), ('speed_spread', 0., .5), ('construction_min_s', 5., 3600.), ('construction_max_s', 5., 3600.)]:
        v = options[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('Invalid traffic option: ' + key)
    if options['construction_min_s'] >= options['construction_max_s']:
        raise ValueError('Construction clearance requires minimum < maximum')
    return options


def cyclic_traffic(payload, options):
    """Select a safe source ring; preserve source ego and map in either area."""
    options = traffic_options(options)
    if payload['key'] != 'nuscenes-scene0980':
        raise ValueError('Synthetic cyclic traffic currently supports nuScenes scene-0980 only')
    if options['route_mode']=='network':
        from .simulation_network import network_traffic
        return network_traffic(payload,options)
    points, graph = _graph(payload['maps']['updated'])
    ego = payload['tracks'][payload['ego_track_id']]
    occupied = [vehicle_footprint(ego['position'][0], ego['heading'][0])]
    occupied += [vehicle_footprint(a['position'], a['heading'], a['length'], a['width'], margin=0.)
                 for a in payload.get('static_overview_actors', [])]
    candidates, seen = [], set()
    for lane in sorted(graph):
        cycle = _cycle(lane, points, graph)
        if not cycle:
            continue
        identity = tuple(sorted(cycle[:-1]))
        if identity in seen:
            continue
        seen.add(identity)
        path = []
        for key in cycle[:-1]:
            for point in points[key]:
                if not path or point != path[-1]:
                    path.append(point)
        if path[-1] != path[0]:
            path.append(path[0])
        line = LineString(path)
        clearance = min(line.distance(o) for o in occupied)
        if 200 <= line.length <= 600 and line.is_simple and clearance >= 4.:
            candidates.append((clearance, -line.length, cycle[:-1], path))
    if not candidates:
        raise ValueError('No safe source-lane cycle; do not invent connecting roads')
    if options['area'] == 'near_recording':
        selected = min(candidates, key=lambda c: (LineString(c[3]).distance(Point(ego['position'][0][:2])), -c[0], -c[1]))
    else:
        selected = max(candidates)
    clearance, _, lanes, path = selected
    length = _length(path)
    rng = random.Random(options['seed'])
    count = options['vehicle_count']
    if count and length/count < 15.:
        raise ValueError('Requested density exceeds the safe initial spacing')
    spacing = length/max(count, 1)
    phase = rng.uniform(0., spacing)
    if options['area'] == 'near_recording':
        phase = (_project(ego['position'][0][:2], path)[1] + phase/2-spacing/4) % length
    missions = []
    for i in range(count):
        start_s = (phase+i*length/count) % length
        position = list(_point_at(path, start_s))
        heading = _project(position, path)[3]
        envelope = vehicle_footprint(position, heading)
        if any(envelope.intersects(o) for o in occupied):
            raise ValueError('Synthetic traffic spawn overlaps a physical actor')
        occupied.append(envelope)
        goal = list(_point_at(path, (start_s+length/2) % length))
        missions.append(dict(vehicle_id=f'P{i+1}', position_scenario_m=position, heading_rad=heading,
            goal_scenario_m=goal, patrol_goals_scenario_m=[goal, position], patrol_loop=True,
            cruise_speed_m_s=max(.5, min(3., options['speed_m_s']*(1+rng.uniform(-options['speed_spread'],options['speed_spread'])))), cycle_follower=True, source_lane_ids=lanes,
            source_route_length_m=length, synthetic_actor=True, initial_clearance_checked=True,
            mission_origin='synthetic_source_lane_cycle', uses_recorded_trajectory=False))
    from .simulation_following import driver_parameters
    for mission in missions:
        mission['driver_parameters'] = driver_parameters(options['seed'], mission['vehicle_id'], options['driver_style'])
    from .simulation_events import construction_specs
    constructions, generation = construction_specs(path, missions, occupied, options) if options['construction'] else ([], None)
    return dict(missions=missions, cyclic_path=path, constructions=constructions, construction_generation=generation,
        construction=deepcopy(constructions[0]) if constructions else None, traffic=dict(options, profile='synthetic_cycle_v1', synthetic=True),
        traffic_area=dict(selection=options['area'], source_ego_unchanged=True, route_length_m=length,
            route_distance_to_recorded_ego_m=LineString(path).distance(Point(ego['position'][0][:2])),
            minimum_source_actor_clearance_m=clearance))


class CycleFollower:
    """Low-speed background pure pursuit; all poses still come from Bullet.

    On re-entry match the actual pose, never restore a saved pose. Off-route or
    reverse-facing cars stop and report the problem instead of snapping back.
    """
    def __init__(self, path, speed):
        self.path, self.length, self.speed = path, _length(path), speed
        self.last_s = None
        self.start_s = None
        self.distance = 0.
        self._diagnostics = {}

    def diagnostics(self):
        return deepcopy(self._diagnostics)

    def __call__(self, state):
        position = state['position_runtime_m']
        lateral, progress, _, tangent = _project(position, self.path)
        heading = state['heading_rad']
        valid = lateral <= 2.5 and abs(_angle(heading-tangent)) < math.pi/2
        if self.last_s is not None:
            delta = (progress-self.last_s+self.length/2) % self.length-self.length/2
            if abs(delta) > max(3., state['speed_m_s']*.3+1):
                valid = False
            elif valid:
                self.distance = max(0., self.distance + delta)
        if self.start_s is None:
            self.start_s = progress
        self.last_s = progress
        upcoming = [list(_point_at(self.path, (progress+d) % self.length)) for d in range(0, 41, 2)]
        self._diagnostics = dict(status='tracking' if valid else 'stopped',
            reason=None if valid else 'cycle_reentry_requires_forward_lane_pose', points=upcoming,
            lateral_error_m=lateral, progress_m=progress, travelled_m=self.distance,
            completed_laps=int(self.distance/self.length), length_m=self.length,
            goal_scenario_m=list(_point_at(self.path, (self.start_s +
                (int(self.distance/(self.length/2))+1)*self.length/2) % self.length)))
        if not valid:
            return [0., -1.]
        lookahead = max(3., state['speed_m_s']*1.2)
        target = _point_at(self.path, (progress+lookahead) % self.length)
        alpha = _angle(math.atan2(target[1]-position[1], target[0]-position[0])-heading)
        steering = math.atan2(2*state['wheelbase_m']*math.sin(alpha), max(.1, math.dist(position,target))) / state['max_steering_rad']
        speed = self.speed/(1+2*abs(steering))
        self._diagnostics['target_speed_m_s'] = speed
        return [max(-1.,min(1.,steering)), max(-1.,min(.5,.4*(speed-state['speed_m_s'])))]


# Deliberate allowlist: other drivers' controls, routes, goals, events and seeds
# are not measurements, even when stored beside a current pose in engine state.
_MEASURED = ('vehicle_id', 'type', 'position_city_m', 'heading_rad', 'velocity_m_s',
             'speed_m_s', 'length_m', 'width_m', 'height_m', 'synthetic', 'source_scope')


def vehicle_perception(interactive, vehicle_id, radius=50., clearance_queries=()):
    own = interactive['vehicles'][vehicle_id]
    def measure(actor):
        result = {k: deepcopy(actor[k]) for k in _MEASURED if k in actor}
        if actor.get('vehicle_id') in interactive['vehicles']:
            result['type'] = 'VEHICLE'
        return result
    objects = list(interactive['vehicles'].values()) + interactive.get('static_actors', [])
    visible = [measure(a) for a in objects if a.get('vehicle_id') != vehicle_id
               and math.dist(own['position_city_m'][:2], a['position_city_m'][:2]) <= radius]
    # Query only previously observed footprints. A complete ideal geometric
    # scan can confirm emptiness; absence from the object list alone cannot.
    from .simulation_live_map import footprint
    from shapely.geometry import Polygon
    checks=[]
    visible_ids={a['vehicle_id'] for a in visible}
    for query in clearance_queries:
        if query['actor_id'] in visible_ids:
            continue
        region=query['geometry_city_m']
        if not all(math.dist(own['position_city_m'][:2],p[:2]) <= radius for p in region):
            continue
        polygon=Polygon(region)
        occupied=False
        for actor in objects:
            # Includes the observer itself, and objects whose centres are
            # outside range but whose bodies intersect the queried region.
            if not all(k in actor for k in ('length_m','width_m','heading_rad')):
                occupied=True  # Missing geometry cannot certify free space.
                break
            reach=(actor['length_m']**2+actor['width_m']**2)**.5/2
            if polygon.distance(Point(actor['position_city_m'][:2])) > reach:
                continue
            if polygon.intersects(Polygon(footprint(actor))):
                occupied=True
                break
        checks.append(dict(actor_id=query['actor_id'],geometry_city_m=deepcopy(region),
            observed_free=not occupied, source='ideal_current_geometry_scan'))
    return dict(own_vehicle=measure(own), current_objects=visible, free_space_checks=checks,
        observation_scope='synthetic_ideal_range_limited_360deg', range_m=radius,
        occlusion_modelled=False, noise_modelled=False, synthetic=True,
        future_intentions_included=False)
