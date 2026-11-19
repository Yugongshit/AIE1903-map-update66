"""State-level planner contracts, source-road tasks and observation permissions."""
from collections import deque
from copy import deepcopy
import math

from .evidence_intake import require_identity
from .simulation_bridge import LANE_TYPES


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return float(value)


def xy(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('position_m must contain XY')
    return [number(v, 'coordinate') for v in value]


def validate_plan(plan, observation, controlled, remaining_s):
    if not isinstance(plan, dict) or set(plan) != {'plan_id', 'actor_id', 'coordinate_frame', 'points'}:
        raise ValueError('Plan requires plan_id, actor_id, coordinate_frame and points')
    require_identity(plan['plan_id'], 'plan_id')
    actor_id = plan['actor_id']
    if not isinstance(actor_id, str) or actor_id not in controlled or actor_id not in observation['objects']:
        raise ValueError('Plan actor must be currently controlled and valid')
    if plan['coordinate_frame'] != observation['coordinate_frame']:
        raise ValueError('Plan coordinate frame mismatch')
    points = plan['points']
    if not isinstance(points, list) or not 2 <= len(points) <= 1000:
        raise ValueError('Plan requires 2 to 1000 time samples')
    previous = -1.
    for point in points:
        if not isinstance(point, dict) or set(point) != {'time_offset_s', 'position_m', 'heading_rad', 'speed_m_s'}:
            raise ValueError('Trajectory point fields do not match the contract')
        time = number(point['time_offset_s'], 'time_offset_s')
        if not previous < time <= remaining_s or time < 0:
            raise ValueError('Trajectory times must increase within the remaining recording')
        previous = time
        xy(point['position_m'])
        number(point['heading_rad'], 'heading_rad')
        if number(point['speed_m_s'], 'speed_m_s') < 0:
            raise ValueError('speed_m_s must be nonnegative')
    if points[0]['time_offset_s'] != 0:
        raise ValueError('First trajectory sample must describe the current state at offset zero')
    actor = observation['objects'][actor_id]
    if math.dist(points[0]['position_m'], actor['position_m']) > .05:
        raise ValueError('Trajectory start must match current position within 5 cm')
    if abs(math.remainder(points[0]['heading_rad'] - actor['heading_rad'], 2 * math.pi)) > .05:
        raise ValueError('Trajectory start must match current heading within .05 radians')
    if abs(points[0]['speed_m_s'] - math.hypot(*actor['velocity_m_s'])) > .1:
        raise ValueError('Trajectory start must match current speed within .1 m/s')
    return deepcopy(plan)


def scoped_observation(observation, actor_id, scope):
    result = deepcopy(observation)
    own = result['objects'].get(actor_id)
    if own is None:
        raise ValueError('Actor is unavailable at this frame')
    if scope['kind'] == 'radius':
        result['objects'] = {key:row for key,row in result['objects'].items()
                             if math.dist(own['position_m'], row['position_m']) <= scope['radius_m']}
        # Signals lack a position in this adapter, so local views omit them.
        result['signals'] = {}
        result['road_states'] = None
    result['own_actor_id'] = actor_id
    result['observation_scope'] = deepcopy(scope)
    result['observation_kind'] = 'ideal_state_with_explicit_scope'
    return result


def validate_scope(scope):
    if scope == {'kind': 'global'}:
        return deepcopy(scope)
    if not isinstance(scope, dict) or set(scope) != {'kind', 'radius_m'} or scope['kind'] != 'radius':
        raise ValueError('Scope must be global or radius with radius_m')
    radius = number(scope['radius_m'], 'radius_m')
    if radius <= 0:
        raise ValueError('radius_m must be positive')
    return dict(kind='radius', radius_m=radius)


def road_task(features, start, goal):
    """Return source-lane reachability, without constructing a driving trajectory."""
    from shapely.geometry import Point, Polygon
    start, goal = xy(start), xy(goal)
    lanes = {key:row for key,row in features.items() if row.get('type') in LANE_TYPES}
    polygons = {}
    for key, row in lanes.items():
        coordinates = row.get('polygon')
        if coordinates is not None and len(coordinates) >= 3:
            polygon = Polygon([p[:2] for p in coordinates])
            if polygon.is_valid and not polygon.is_empty:
                polygons[key] = polygon
    starts = sorted(key for key, poly in polygons.items() if poly.covers(Point(start)))
    goals = {key for key, poly in polygons.items() if poly.covers(Point(goal))}
    result = dict(start_m=start, goal_m=goal, geometry_scope='explicit_source_lane_polygons',
                  polygon_lane_count=len(polygons), lane_count=len(lanes), lane_ids=[])
    if not starts or not goals:
        result['status'] = 'outside_available_lane_geometry'
        return result
    queue = deque((key, [key]) for key in starts)
    seen = set(starts)
    while queue:
        key, path = queue.popleft()
        if key in goals:
            result.update(status='reachable', lane_ids=path)
            return result
        for raw in lanes[key].get('exit_lanes') or []:
            target = raw.get('id', raw.get('feature_id')) if isinstance(raw, dict) else raw
            if target in lanes and target not in seen:
                seen.add(target)
                queue.append((target, path + [target]))
    result['status'] = 'unreachable'
    return result
