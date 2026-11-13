"""Five controllable traffic cars on verified, unmodified source lane graphs.

The original ego is left to the external Planning client. P1..P5 are new
experimental actors, not replacements for recorded trajectories. Initial
clearance is checked against the actual ego envelope and the frozen static
world; moving recorded traffic is absent from this experiment.
"""
from __future__ import annotations

import heapq
import math

from shapely.geometry import Polygon

from .simulation_planner import _lane_accessible, _length, _point_at, _project, plan_route


_LANE_TYPES = {"LANE_SURFACE_STREET", "LANE_SURFACE_UNSTRUCTURE", "LANE_FREEWAY"}
_PRESETS = {
    "nuscenes-scene0980": (
        ("P1", "71213690-7222-4882-a4cb-94610eda0ff3", 3.,
         "fc54378d-a2ee-401d-a1ed-245253eddc48", 12.),
        ("P2", "82d0cfc8-d47e-4b12-9ba9-3b8c52c7e99e", 6.,
         "6fd0b3c9-85e1-4610-971a-8ab57779b400", 15.),
        ("P3", "ec65b625-ab99-49a2-bd3e-ba1716184c0a", 110.,
         "b6d62d76-5fb8-4406-9c29-9267cbb3c9ef", 15.),
        ("P4", "6fd0b3c9-85e1-4610-971a-8ab57779b400", 8.,
         "8f74cf95-642c-4683-abd0-b7f661ba7fbd", 10.),
        ("P5", "c7c1fb15-c672-4e30-89e8-194b47efdb14", 68.,
         "d8f00bbd-afa6-42d1-b3bf-129e914379bd", 12.),
    ),
    "argoverse-winter2021": (
        ("P1", "ls-168084875", 4., "ls-168084753", 12.),
        ("P2", "ls-168084962", 20., "ls-168084750", 10.),
        ("P3", "ls-168084951", 15., "ls-168084864", 12.),
        ("P4", "ls-168084879", 10., "ls-168084815", 8.),
        ("P5", "ls-168084876", 4., "ls-168084981", 10.),
    ),
}


def vehicle_footprint(position, heading_rad, length=4.6, width=2., *, margin=.3):
    """Envelope covering native DefaultVehicle/TrafficDefaultVehicle geometry."""
    values = [*position[:2], heading_rad, length, width, margin]
    if not all(math.isfinite(float(value)) for value in values) or min(length, width) <= 0 or margin < 0:
        raise ValueError("Invalid interactive actor footprint")
    cosine, sine = math.cos(heading_rad), math.sin(heading_rad)
    half_l, half_w = length / 2 + margin, width / 2 + margin
    return Polygon([(position[0] + x * cosine - y * sine, position[1] + x * sine + y * cosine)
                    for x, y in [(-half_l, -half_w), (half_l, -half_w),
                                 (half_l, half_w), (-half_l, half_w)]])


def _lane_points(feature, lane_id):
    if not feature or feature.get("type") not in _LANE_TYPES or not _lane_accessible(feature, "vehicle"):
        raise ValueError("Interactive mission source lane is missing or inaccessible: " + lane_id)
    points = [(float(p[0]), float(p[1])) for p in feature.get("polyline", [])]
    if len(points) < 2 or not all(math.isfinite(v) for p in points for v in p) or _length(points) <= 1e-6:
        raise ValueError("Interactive mission lane has no finite centerline: " + lane_id)
    return points


def _graph(features):
    points = {key: _lane_points(feature, str(key)) for key, feature in features.items()
              if feature.get("type") in _LANE_TYPES and _lane_accessible(feature, "vehicle")}
    graph = {key: sorted(target for target in features[key].get("exit_lanes", [])
                         if target in points and math.dist(points[key][-1], points[target][0]) <= 5.)
             for key in points}
    return points, graph


def _cycle(first, points, graph):
    """A shortest explicit directed cycle, without guessed neighbour links."""
    pending, best = [], {}
    for target in graph[first]:
        if target != first:
            heapq.heappush(pending, (0., target, (first, target)))
    while pending:
        distance, key, path = heapq.heappop(pending)
        if key == first:
            return path
        if distance >= best.get(key, float("inf")):
            continue
        best[key] = distance
        for target in graph[key]:
            cost = distance + _length(points[key]) + math.dist(points[key][-1], points[target][0])
            if cost < best.get(target, float("inf")):
                heapq.heappush(pending, (cost, target, (*path, target)))
    return None


def _patrol(features, points, graph, goal_lane, goal):
    cycle = _cycle(goal_lane, points, graph)
    if cycle:
        # A second goal on the opposite portion of the real directed cycle
        # makes repeated goal selection meaningful; neither leg reverses.
        for middle in sorted(cycle[1:-1], key=lambda key: (-_length(points[key]), key)):
            target = list(_point_at(points[middle], _length(points[middle]) / 2))
            first_heading = _project(goal, points[goal_lane])[3]
            middle_heading = _project(target, points[middle])[3]
            out = plan_route(features, goal, target, heading_rad=first_heading)
            back = plan_route(features, target, goal, heading_rad=middle_heading)
            if (out["status"] == back["status"] == "planned" and
                    out["lane_ids"][0] == goal_lane and out["lane_ids"][-1] == middle and
                    back["lane_ids"][0] == middle and back["lane_ids"][-1] == goal_lane):
                return [goal, target], True, [out["lane_ids"], back["lane_ids"]]
    # A cropped graph need not contain a closed route. Advance to a verified
    # successor if possible, then stop; never teleport/reverse to restart it.
    for target_lane in graph[goal_lane]:
        target = list(_point_at(points[target_lane], _length(points[target_lane]) / 2))
        onward = plan_route(features, goal, target, heading_rad=_project(goal, points[goal_lane])[3])
        if onward["status"] == "planned" and onward["lane_ids"][0] == goal_lane and onward["lane_ids"][-1] == target_lane:
            return [goal, target], False, [onward["lane_ids"]]
    return [goal], False, []


def choose_interactive_missions(payload):
    """Return five checked source-city missions; never assign an ego planner."""
    key = payload.get("key")
    if key not in _PRESETS:
        raise ValueError("No verified five-car interactive missions for scene: " + str(key))
    features = payload["maps"]["updated"]
    points, graph = _graph(features)
    ego = payload["tracks"][payload["ego_track_id"]]
    if not ego["valid"][0]:
        raise ValueError("Interactive ego has no valid source-frame-zero position")
    ego_position, ego_heading = ego["position"][0][:2], ego["heading"][0]
    # Native vehicles are 4.515 x 1.852 m. nuScenes's converted source ego
    # length/width fields are not the native car's collision dimensions.
    occupied = [("ego", vehicle_footprint(ego_position, ego_heading))]
    occupied.extend((str(actor["id"]), vehicle_footprint(actor["position"], actor["heading"],
                    actor["length"], actor["width"], margin=0.))
                    for actor in payload.get("static_overview_actors", []))
    missions = []
    for identifier, start_lane, start_s, goal_lane, goal_s in _PRESETS[key]:
        start_points = _lane_points(features.get(start_lane), start_lane)
        goal_points = _lane_points(features.get(goal_lane), goal_lane)
        if start_s > _length(start_points) or goal_s > _length(goal_points):
            raise ValueError("Interactive mission arclength exceeds the source lane: " + identifier)
        start, goal = list(_point_at(start_points, start_s)), list(_point_at(goal_points, goal_s))
        heading = _project(start, start_points)[3]
        envelope = vehicle_footprint(start, heading)
        collisions = sorted({actor_id for actor_id, geometry in occupied if envelope.intersects(geometry)})
        if collisions:
            raise ValueError("Interactive mission " + identifier + " spawn overlaps: " + ", ".join(collisions))
        route = plan_route(features, start, goal, heading_rad=heading)
        if (route["status"] != "planned" or route["lane_ids"][0] != start_lane or
                route["lane_ids"][-1] != goal_lane):
            raise ValueError("Interactive mission lacks its declared directed source route: " + identifier)
        patrol_goals, patrol_loop, patrol_routes = _patrol(features, points, graph, goal_lane, goal)
        missions.append({
            "vehicle_id": identifier, "position_scenario_m": start, "heading_rad": heading,
            "goal_scenario_m": goal, "cruise_speed_m_s": 2.5,
            "start_lane_id": start_lane, "goal_lane_id": goal_lane,
            "source_lane_ids": route["lane_ids"], "source_route_length_m": route["length_m"],
            "patrol_goals_scenario_m": patrol_goals, "patrol_loop": patrol_loop,
            "patrol_lane_routes": patrol_routes,
            "coordinate_frame": "source_city_XY_metres", "source_map_selection": "updated",
            "mission_origin": "configured_interactive_actor_on_verified_source_lanes",
            "synthetic_actor": True, "source_geometry_modified_for_mission": False,
            "uses_recorded_trajectory": False, "initial_clearance_checked": True,
            "initial_clearance_m": min(envelope.distance(geometry) for _, geometry in occupied),
            "spawn_distance_to_ego_m": math.dist(start, ego_position),
            "spawn_ahead_of_ego_m": (start[0] - ego_position[0]) * math.cos(ego_heading)
                                    + (start[1] - ego_position[1]) * math.sin(ego_heading),
        })
        occupied.append((identifier, envelope))
    return missions


def interactive_profile(payload):
    """Trusted scenario metadata; ego remains stopped awaiting external input."""
    return {
        "schema": "simulation-interactive-profile-1.0", "scene_key": payload["key"],
        "missions": choose_interactive_missions(payload),
        "ego_control": "external_waiting", "recorded_moving_traffic": False,
        "static_world_policy": "full_scene_fixed_overview",
        "source_geometry_modified": False, "recipient_routes_use_recorded_trajectory": False,
        "coordinate_frame": "source_city_XY_metres",
        "notes": ["P1 位于主车前方约 20 米，P2—P5 位于同一路口或相邻源车道。",
                  "五辆附加车可切换玩家、AI 或外部控制；主车等待外部 Planning 输入。",
                  "只有源车道图存在有向回环时才循环 AI 目标；否则到最后目标停车。"],
    }
