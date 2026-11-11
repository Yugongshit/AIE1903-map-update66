"""Small lane-graph planning/controller example for updated simulation maps.

This research baseline follows explicitly supplied directed lane connections and
uses a user-selected goal. It never reads an ego recording or old navigation
trajectory. Source lane-use restrictions are honored, but this is not a full
traffic-rule, collision-avoidance or free-space planner; the simulation runner
must retain collision termination. Coordinates below are
metric XY in one common frame (runtime-centered XY in ``controller``).
"""
from __future__ import annotations

import heapq
import math


_SOURCE_LANE_ACCESS = {
    "VEHICLE": {"vehicle", "bus"},
    "BUS": {"bus"},
    "BIKE": {"bicycle"},
}
_VEHICLE_CLASSES = {"vehicle", "bus"}


def _validate_vehicle_class(vehicle_class):
    if not isinstance(vehicle_class, str) or vehicle_class not in _VEHICLE_CLASSES:
        raise ValueError("vehicle_class must be 'vehicle' or 'bus'")
    return vehicle_class


def _lane_accessible(feature, vehicle_class):
    """Keep supplied lane restrictions even when MetaDrive types are identical.

Old nuScenes exports have neither field and retain their existing lane graph.
When source restrictions are present, missing/unknown values cannot grant new
access. An explicit access list may narrow a known source lane's permission,
but cannot turn a BUS or BIKE lane into an ordinary vehicle lane.
"""
    if "source_lane_type" in feature:
        source_type = feature["source_lane_type"]
        if not isinstance(source_type, str) or vehicle_class not in _SOURCE_LANE_ACCESS.get(source_type, set()):
            return False
    if "vehicle_access" in feature:
        access = feature["vehicle_access"]
        if not isinstance(access, list) or any(
                not isinstance(value, str) or value not in {"vehicle", "bus", "bicycle"}
                for value in access):
            return False
        if vehicle_class not in access:
            return False
    return True


def _xy(point):
    result = (float(point[0]), float(point[1]))
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Coordinates must be finite metric XY")
    return result


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _project(point, points):
    """Return distance, forward arclength, closest point and segment heading."""
    best, traveled = None, 0.0
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        ratio = max(0.0, min(1.0, ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / length**2))
        projected = (a[0]+ratio*dx, a[1]+ratio*dy)
        row = (math.dist(point, projected), traveled+ratio*length,
               projected, math.atan2(dy, dx))
        if best is None or row[0] < best[0]:
            best = row
        traveled += length
    if best is None:
        raise ValueError("A lane centerline needs two distinct points")
    return best


def _point_at(points, distance):
    remaining = max(0.0, distance)
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length > 1e-9 and remaining <= length:
            ratio = remaining / length
            return (a[0]+ratio*(b[0]-a[0]), a[1]+ratio*(b[1]-a[1]))
        remaining -= length
    return points[-1]


def _slice(points, start, end):
    result, distance = [_point_at(points, start)], 0.0
    for a, b in zip(points, points[1:]):
        distance += math.dist(a, b)
        if start < distance < end:
            result.append(b)
    result.append(_point_at(points, end))
    return result


def _angle(value):
    return (value + math.pi) % (2 * math.pi) - math.pi


def plan_route(map_features, start, goal, *, heading_rad=None,
               max_snap_distance_m=3.0, max_connection_gap_m=5.0,
               vehicle_class="vehicle"):
    """Dijkstra over the current map's directed ``exit_lanes`` connections.

The nearest heading-compatible start lane and nearest goal lane are selected.
Goals behind the start on that same lane are rejected: no invented reverse or
lane-change connection is introduced. Goals must lie near a lane centerline;
their projected endpoint and requested endpoint are both reported. Explicit
connections with a large geometric gap are not bridged through unknown space.
Source BUS/BIKE restrictions are applied before matching or searching lanes.
"""
    vehicle_class = _validate_vehicle_class(vehicle_class)
    start, goal = _xy(start), _xy(goal)
    if heading_rad is not None and not math.isfinite(heading_rad):
        raise ValueError("heading_rad must be finite")
    if max_snap_distance_m <= 0 or max_connection_gap_m < 0:
        raise ValueError("Invalid lane matching distance")
    lanes, excluded_lanes = {}, []
    for key, feature in map_features.items():
        if feature.get("type") not in {"LANE_SURFACE_STREET", "LANE_SURFACE_UNSTRUCTURE", "LANE_FREEWAY"}:
            continue
        if not _lane_accessible(feature, vehicle_class):
            excluded_lanes.append(str(key))
            continue
        points = [_xy(p) for p in feature.get("polyline", [])]
        if len(points) < 2 or _length(points) <= 1e-9:
            continue
        lanes[str(key)] = (points, [str(k) for k in feature.get("exit_lanes", [])])

    result = {"status": "no_route", "reason": None, "lane_ids": [],
              "points": [], "requested_goal": list(goal),
              "vehicle_class": vehicle_class,
              "access_excluded_lane_ids": sorted(excluded_lanes),
              "uses_recorded_trajectory": False,
              "scope": "directed_lane_graph_only_no_obstacle_or_signal_avoidance"}
    if not lanes:
        return dict(result, reason="no_accessible_lane_centerlines" if excluded_lanes else "no_supported_lane_centerlines")
    starts, goals = [], []
    for key, (points, _) in lanes.items():
        a, b = _project(start, points), _project(goal, points)
        if heading_rad is None or abs(_angle(a[3] - heading_rad)) < math.pi / 2:
            starts.append((a[0], key, a))
        goals.append((b[0], key, b))
    if not starts:
        return dict(result, reason="no_forward_start_lane")
    start_distance, first, start_projection = min(starts)
    goal_distance, last, goal_projection = min(goals)
    if max(start_distance, goal_distance) > max_snap_distance_m:
        return dict(result, reason="start_or_goal_too_far_from_lane")
    if first == last and goal_projection[1] < start_projection[1] - 1e-6:
        return dict(result, reason="goal_behind_on_same_lane")

    frontier = [(-start_projection[1], first)]
    costs, previous = {first: -start_projection[1]}, {}
    while frontier:
        cost, lane = heapq.heappop(frontier)
        if cost != costs[lane]:
            continue
        if lane == last:
            break
        points, successors = lanes[lane]
        for successor in sorted(successors):
            if successor not in lanes:
                continue  # Cropped source databases can contain external references.
            gap = math.dist(points[-1], lanes[successor][0][0])
            if gap > max_connection_gap_m:
                continue
            candidate = cost + _length(points) + gap
            if candidate < costs.get(successor, float("inf")):
                costs[successor], previous[successor] = candidate, lane
                heapq.heappush(frontier, (candidate, successor))
    if last not in costs:
        return dict(result, reason="no_directed_path_to_goal")
    route = [last]
    while route[-1] != first:
        route.append(previous[route[-1]])
    route.reverse()
    points = []
    for index, lane in enumerate(route):
        centerline = lanes[lane][0]
        begin = start_projection[1] if index == 0 else 0.0
        end = goal_projection[1] if index == len(route) - 1 else _length(centerline)
        for point in _slice(centerline, begin, end):
            if not points or math.dist(points[-1], point) > 1e-8:
                points.append(point)
    return dict(result, status="planned", reason="updated_lane_graph_path",
                lane_ids=route, points=[list(p) for p in points],
                snapped_goal=list(goal_projection[2]), goal_snap_distance_m=goal_distance,
                length_m=_length(points))


class LaneGraphController:
    """Low-speed pure-pursuit example, with fail-closed braking on no route.

Maps must remain immutable during an episode. A new map object, ``map_version``
or step zero invalidates the cached route. Goal changes also trigger planning.
This controller ignores traffic and obstacles; it is a planning integration
example, not a claim of safe autonomous driving.
"""
    def __init__(self, cruise_speed_m_s=3.0, *, vehicle_class="vehicle"):
        if not math.isfinite(cruise_speed_m_s) or cruise_speed_m_s <= 0:
            raise ValueError("Cruise speed must be finite and positive")
        self.cruise_speed_m_s = cruise_speed_m_s
        self.vehicle_class = _validate_vehicle_class(vehicle_class)
        self._key = None
        self._plan = {"status": "not_started", "reason": "no_callback_yet"}
        self._last_step = None

    def diagnostics(self):
        return {**self._plan, "coordinate_frame": "metadrive_centered_m",
                "cruise_speed_m_s": self.cruise_speed_m_s,
                "vehicle_class": self.vehicle_class,
                "collision_avoidance": False, "traffic_signal_compliance": False}

    def __call__(self, state):
        goal_city = state.get("goal_scenario_m")
        if goal_city is None:
            self._plan = {"status": "no_route", "reason": "user_goal_required"}
            return [0.0, -1.0]
        features = state["map_features_runtime"]
        position = _xy(state["position_runtime_m"])
        origin = _xy(state["runtime_origin_in_scenario_m"])
        goal_city = _xy(goal_city)
        goal = (goal_city[0]-origin[0], goal_city[1]-origin[1])
        heading, speed = float(state["heading_rad"]), float(state["speed_m_s"])
        if not math.isfinite(heading) or not math.isfinite(speed) or speed < 0:
            raise ValueError("Controller requires finite heading and nonnegative speed")
        goal_radius = float(state.get("goal_radius_m", 2.0))
        if not math.isfinite(goal_radius) or goal_radius <= 0:
            raise ValueError("Goal radius must be finite and positive")
        goal_distance = math.dist(position, goal)
        step = state.get("step", 0)
        key = (id(features), state.get("map_version"), state.get("scenario_id"), origin, goal, goal_radius, self.vehicle_class)
        if key != self._key or (self._last_step is not None and step < self._last_step) or step == 0:
            self._plan = plan_route(features, position, goal, heading_rad=heading,
                                    vehicle_class=self.vehicle_class)
            self._plan["goal_radius_m"] = goal_radius
            # This controller follows the centerline. It cannot reach a goal
            # disk that does not intersect that line, even if lane matching
            # considered the nearby requested point a plausible lane target.
            if (self._plan["status"] == "planned"
                    and self._plan["goal_snap_distance_m"] >= goal_radius):
                self._plan = dict(self._plan, status="no_route",
                                  reason="goal_radius_does_not_reach_lane_centerline")
            self._key = key
        self._last_step = step
        # Match the runner's exact goal contract, including off-center goals.
        # A single projected route point alone does not prove arrival.
        if goal_distance <= goal_radius:
            self._plan = dict(self._plan, status="goal_reached")
            return [0.0, -1.0]
        if self._plan["status"] not in {"planned", "tracking"}:
            return [0.0, -1.0]
        points = self._plan["points"]
        if len(points) < 2:
            self._plan = dict(self._plan, status="no_route", reason="no_forward_path_into_goal_radius")
            return [0.0, -1.0]
        lateral, progress, _, _ = _project(position, points)
        if lateral > 4.0:
            self._plan = dict(self._plan, status="stopped", reason="route_tracking_error")
            return [0.0, -1.0]
        remaining = max(0.0, _length(points) - progress)
        lookahead = max(3.0, speed * 1.2)
        target = _point_at(points, progress+lookahead)
        distance = math.dist(position, target)
        alpha = _angle(math.atan2(target[1]-position[1], target[0]-position[0]) - heading)
        if remaining < 1e-6 and abs(alpha) > math.pi / 2:
            self._plan = dict(self._plan, status="stopped", reason="goal_missed_without_reverse_route")
            return [0.0, -1.0]
        wheelbase = float(state.get("wheelbase_m", 2.8))
        max_steering = float(state.get("max_steering_rad", math.radians(60)))
        if not (math.isfinite(wheelbase) and wheelbase > 0 and math.isfinite(max_steering) and max_steering > 0):
            raise ValueError("Invalid vehicle steering geometry")
        steering = math.atan2(2 * wheelbase * math.sin(alpha), max(distance, 0.1)) / max_steering
        # Keep moving until the *actual* goal disk is reached. A fixed endpoint
        # standoff would make a small --goal-radius permanently unreachable.
        target_speed = min(self.cruise_speed_m_s,
                           max(0.3, math.sqrt(2 * max(remaining, distance))))
        target_speed /= 1 + 2 * abs(steering)
        throttle = max(-1.0, min(0.5, 0.4 * (target_speed-speed)))
        self._plan = dict(self._plan, status="tracking", progress_m=progress,
                          lateral_error_m=lateral, target_speed_m_s=target_speed)
        return [max(-1.0, min(1.0, steering)), throttle]


_default_controller = LaneGraphController()


def controller(state):
    """CLI callback: ``--controller map_update.simulation_planner:controller``."""
    return _default_controller(state)


controller.diagnostics = _default_controller.diagnostics
