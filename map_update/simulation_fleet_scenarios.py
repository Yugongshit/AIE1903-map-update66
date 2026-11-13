"""Reproducible recipient-car missions on the delivered source lane graphs.

B/C are experimental vehicles, with independently chosen destinations.  Their
routes are calculated from map connectivity, never copied from the source ego
recording.  Presets identify real source lanes and are checked against the
loaded maps and initial occupied space before a run can begin.
"""
from __future__ import annotations

import math

from shapely.geometry import Polygon

from .simulation_planner import _lane_accessible, _length, _point_at, _project, plan_route


_PRESETS = {
    "nuscenes-scene0980": (
        # Upstream of A and the initially moving recorded car behind A. A
        # stopped subscriber must not be placed in either replay car's path.
        ("B", "c7c1fb15-c672-4e30-89e8-194b47efdb14", 68.0,
         "fc54378d-a2ee-401d-a1ed-245253eddc48", 12.0, "attached_state_corridor"),
        ("C", "82d0cfc8-d47e-4b12-9ba9-3b8c52c7e99e", 6.0,
         "6fd0b3c9-85e1-4610-971a-8ab57779b400", 15.0, "clear_adjacent_corridor"),
    ),
    "argoverse-winter2021": (
        ("B", "ls-168084962", 20.0, "ls-168084753", 6.0, "changed_turn_connections"),
        ("C", "ls-168084962", 3.0, "ls-168084962", 35.0, "unchanged_approach"),
    ),
}


def vehicle_footprint(position, heading_rad, length=4.5, width=2.0, *, margin=0.3):
    """Conservative initial-spawn envelope in source-city metres."""
    values = [*position[:2], heading_rad, length, width, margin]
    if not all(math.isfinite(float(value)) for value in values) or min(length, width) <= 0 or margin < 0:
        raise ValueError("Invalid initial vehicle geometry")
    cosine, sine = math.cos(heading_rad), math.sin(heading_rad)
    half_l, half_w = length / 2 + margin, width / 2 + margin
    return Polygon([(position[0] + x * cosine - y * sine,
                     position[1] + x * sine + y * cosine)
                    for x, y in [(-half_l, -half_w), (half_l, -half_w),
                                 (half_l, half_w), (-half_l, half_w)]])


def _initial_actors(payload):
    """Read only frame zero; future ego/traffic positions do not select goals."""
    actors = []
    for identifier, track in payload.get("tracks", {}).items():
        if track["valid"][0]:
            actors.append({"id": identifier, **{
                key: track[key][0] for key in ("position", "heading", "length", "width")}})
    # The experiment's physical static world is frozen from the full-scene
    # overview.  Avoid spawning inside those objects even if A observes them later.
    actors.extend(payload.get("static_overview_actors", []))
    return actors


def _lane_points(features, lane_id):
    feature = features.get(lane_id)
    if not feature or not _lane_accessible(feature, "vehicle"):
        raise ValueError("Fleet mission source lane is missing or inaccessible: " + lane_id)
    if feature.get("type") not in {"LANE_SURFACE_STREET", "LANE_SURFACE_UNSTRUCTURE", "LANE_FREEWAY"}:
        raise ValueError("Fleet mission source feature is not a lane: " + lane_id)
    points = [(float(point[0]), float(point[1])) for point in feature.get("polyline", [])]
    if len(points) < 2 or _length(points) <= 1e-6:
        raise ValueError("Fleet mission source lane has no usable centerline: " + lane_id)
    return points


def choose_fleet_missions(payload):
    """Return two checked B/C missions, or clearly reject an unsupported scene.

The initial pose and fixed physical goal are identical in all map comparisons.
Only frame-zero actors and the frozen static world are used for spawn clearance.
No guessed lane connections or arbitrary off-road spawn positions are added.
"""
    key = payload.get("key")
    if key not in _PRESETS:
        raise ValueError("No verified recipient-car missions for scene: " + str(key))
    original, updated = payload["maps"]["original"], payload["maps"]["updated"]
    occupied = [(str(actor["id"]), vehicle_footprint(
        actor["position"], actor["heading"], actor["length"], actor["width"], margin=0.0))
        for actor in _initial_actors(payload)]
    missions = []
    for identifier, start_lane, start_s, goal_lane, goal_s, purpose in _PRESETS[key]:
        start_points, goal_points = _lane_points(original, start_lane), _lane_points(original, goal_lane)
        if start_s > _length(start_points) or goal_s > _length(goal_points):
            raise ValueError("Fleet mission arclength is outside the source lane: " + identifier)
        start, goal = _point_at(start_points, start_s), _point_at(goal_points, goal_s)
        heading = _project(start, start_points)[3]
        envelope = vehicle_footprint(start, heading)
        collisions = sorted({actor_id for actor_id, shape in occupied if envelope.intersects(shape)})
        if collisions:
            raise ValueError("Fleet mission " + identifier + " spawn overlaps source actors: " + ", ".join(collisions))
        original_plan = plan_route(original, start, goal, heading_rad=heading)
        if (original_plan["status"] != "planned" or
                original_plan["lane_ids"][0] != start_lane or original_plan["lane_ids"][-1] != goal_lane):
            raise ValueError("Fleet mission does not have the declared directed source route: " + identifier)
        updated_plan = plan_route(updated, start, goal, heading_rad=heading)
        missions.append({
            "vehicle_id": identifier,
            "position_scenario_m": list(start), "heading_rad": heading,
            "goal_scenario_m": list(goal), "start_lane_id": start_lane, "goal_lane_id": goal_lane,
            "source_lane_ids": original_plan["lane_ids"],
            "updated_lane_ids": updated_plan["lane_ids"],
            "source_route_length_m": original_plan["length_m"],
            "updated_route_status": updated_plan["status"],
            "purpose": purpose, "coordinate_frame": "source_city_XY_metres",
            "mission_origin": "configured_experiment_on_verified_source_lanes",
            "uses_recorded_trajectory": False,
            "source_geometry_modified_for_mission": False,
            "initial_clearance_checked": True,
        })
        occupied.append((identifier, envelope))
    return missions


def fleet_profile(payload):
    """API-ready experiment provenance without any future actor trajectories."""
    missions = choose_fleet_missions(payload)
    is_nuscenes = payload["key"] == "nuscenes-scene0980"
    notes = ([
        "B 的源车道通往已观测占用区域；源路网未提供可用绕行连接，收到阻断更新后应停车。",
        "C 使用旁边的独立车道和目标点，用于验证其他车辆能够实际继续行驶。",
        "该 nuScenes 场景没有道路结构补丁，旧地图与仅结构更新应一致；差异来自附着状态。",
    ] if is_nuscenes else [
        "B 的固定目标相同，道路参考更新改变路口后续连接，规划需改用真实新增车道。",
        "C 在未改变的接近车道内独立到达目标，用于对照。",
        "该 Argoverse 场景没有附着状态输出，仅结构更新与完整更新应一致。",
    ])
    return {
        "schema": "simulation-fleet-profile-1.0", "scene_key": payload["key"],
        "missions": missions, "notes": notes,
        "source_kind": payload.get("source", {}).get("kind"),
        "mission_origin": "configured_experiment_on_verified_source_lanes",
        "synthetic_recipient_vehicles": True, "synthetic_map_changes": False,
        "uses_recorded_trajectory_for_recipient_routes": False,
        "unchanged_comparison": "old_equals_structure" if is_nuscenes else "structure_equals_full",
        "coordinate_frame": "source_city_XY_metres",
    }
