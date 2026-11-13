"""Run repeatable B/C planning comparisons in the actual MetaDrive physics world.

All variants load the SAME updated physical map, recipient start/goal poses,
recorded source traffic and frozen static objects. Only received map knowledge
changes. Run with the Python environment used by run_simulation_platform.py.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def route_errors(route, features):
    """Check source identities, explicit directed connections and endpoint gaps."""
    errors = []
    lanes = route.get("lane_ids", [])
    for lane_id in lanes:
        if lane_id not in features:
            errors.append("missing_lane:" + lane_id)
    for first, second in zip(lanes, lanes[1:]):
        if first not in features or second not in features:
            continue
        if second not in features[first].get("exit_lanes", []):
            errors.append("invented_connection:" + first + "->" + second)
        a, b = features[first].get("polyline", []), features[second].get("polyline", [])
        if a and b and math.dist(a[-1][:2], b[0][:2]) > 5.001:
            errors.append("disconnected_geometry:" + first + "->" + second)
    for point in route.get("points", []):
        if len(point) < 2 or not all(math.isfinite(float(x)) for x in point[:2]):
            errors.append("nonfinite_route_position")
    return errors


def summarize_vehicle(initial, final, observations, features, timestep_s):
    trace = final["actual_trace"]
    positions = [row["position_city_m"] for row in trace]
    initial_route = initial["current_route"]
    route_checks = []
    for row in observations:
        route_checks.extend(route_errors(row["current_route"], features[row["map_key"]]))
    first_brake = next((row for row in observations if row["stopped_reason"] and
                        row["stopped_reason"] != "goal_reached"), None)
    moved = [row for row in trace if row["speed_m_s"] > .2]
    reached = next((row for row in observations if row["stopped_reason"] == "goal_reached"), None)
    return {
        "motion_source": final["motion_source"],
        "uses_recorded_trajectory": final["uses_recorded_trajectory"],
        "initial_position_city_m": positions[0], "final_position_city_m": positions[-1],
        "goal_city_m": final["goal_city_m"],
        "distance_travelled_m": sum(math.dist(a, b) for a, b in zip(positions, positions[1:])),
        "net_displacement_m": math.dist(positions[0], positions[-1]),
        "goal_distance_m": math.dist(positions[-1], final["goal_city_m"]),
        "max_speed_m_s": max(row["speed_m_s"] for row in trace),
        "max_reported_lateral_error_m": max(
            (abs(float(row["current_route"].get("lateral_error_m", 0) or 0)) for row in observations), default=0),
        "first_motion_step": moved[0]["step"] if moved else None,
        "first_braking_step": first_brake["step"] if first_brake else None,
        "first_braking_reason": first_brake["stopped_reason"] if first_brake else None,
        "goal_reached_step": reached["step"] if reached else None,
        "goal_reached_time_s": reached["step"] * timestep_s if reached else None,
        "final_stopped_reason": final["stopped_reason"],
        "first_route_status": initial_route["status"],
        "first_lane_ids": initial_route.get("lane_ids", []),
        "final_lane_ids": final["current_route"].get("lane_ids", []),
        "route_errors": sorted(set(route_checks)),
        "collision_detected": any(row["crash_vehicle"] or row["crash_object"] or row.get("collision_stop") for row in observations),
        "first_collision_step": next((row["step"] for row in observations
                                      if row["crash_vehicle"] or row["crash_object"] or row.get("collision_stop")), None),
        "received_version": final["received_version"], "received_step": final["received_step"],
        "known_state_objects": final["known_state_objects"],
        "replan_count": final["replan_count"], "last_replan_reason": final["last_replan_reason"],
        "sample_count": len(trace),
    }


def _observe(session):
    fleet = session.fleet
    step = int(session.env.episode_step)
    result = {}
    for identifier, native in fleet.vehicles.items():
        row = fleet.service.vehicle_status(identifier)
        result[identifier] = {"step": step, **row,
                              "crash_vehicle": bool(native.crash_vehicle),
                              "crash_object": bool(native.crash_object),
                              "collision_stop": bool(getattr(fleet, "collision_steps", {}).get(identifier))}
    return result


def run_case(payload, variant, *, mode="offline", steps=None, output_dir=None):
    from map_update.simulation_fleet_scenarios import choose_fleet_missions
    from map_update.simulation_platform_data import scene_database
    from map_update.simulation_session import SimulationSession, _suspended_rendering

    end_step = min(payload["frame_count"] - 1, steps if steps is not None else payload["frame_count"] - 1)
    missions = choose_fleet_missions(payload)
    start = time.monotonic()
    deliveries = []
    with tempfile.TemporaryDirectory(prefix="fleet-validation-") as directory:
        session = SimulationSession(directory, width=480, height=320)
        try:
            initial = session.open(scene_database(ROOT, payload["key"], "updated"),
                fleet={"mode": mode, "variant": variant, "maps": payload["maps"], "missions": missions})
            observations = {identifier: [row] for identifier, row in _observe(session).items()}
            if mode == "online":
                session.fleet.command({"action": "subscription", "vehicle_id": "C", "enabled": False})
                deliveries.append({"step": 0, "action": "disconnect_C", "status": session.fleet.service.status()})
            # Every native tick and controller tick still runs. Only rendering
            # is batched; playback/rendering frequency cannot alter the physics.
            with _suspended_rendering(session.env):
                for step in range(1, end_step + 1):
                    session._step()
                    if mode == "online" and step in {5, 10}:
                        command = ({"action": "publish"} if step == 5 else
                                   {"action": "subscription", "vehicle_id": "C", "enabled": True})
                        session.fleet.command(command)
                        deliveries.append({"step": step, "action": command["action"],
                                           "status": session.fleet.service.status()})
                    for identifier, row in _observe(session).items():
                        observations[identifier].append(row)
                    if step % 50 == 0:
                        print(f"  physics step {step}/{end_step}", file=sys.stderr, flush=True)
            final = session.frame()
            if output_dir:
                destination = Path(output_dir) / (payload["key"] + "-" + mode + "-" + variant + ".png")
                destination.parent.mkdir(parents=True, exist_ok=True)
                frame_path = Path(directory) / final["frame_file"]
                destination.write_bytes(frame_path.read_bytes())
            cars = {identifier: summarize_vehicle(initial["fleet"]["vehicles"][identifier], vehicle,
                         observations[identifier], payload["maps"], payload["timestep_s"])
                    for identifier, vehicle in final["fleet"]["vehicles"].items()}
            own_pose_matches = all(
                math.dist(final["fleet"]["planning_inputs"][key]["vehicle_state"]["position"],
                          final["fleet"]["vehicles"][key]["position_city_m"]) < 1e-6
                and final["fleet"]["planning_inputs"][key]["vehicle_state"]["step"] == end_step
                for key in cars)
            replay_checks = None
            if mode == "online":
                rewound = session.seek(0)
                replayed = session.seek(end_step)
                replay_checks = {
                    "rewind_removes_later_structure_delivery": all(
                        row["map_key"] == "original" for row in rewound["fleet"]["vehicles"].values()),
                    "delivery_history_replays_same_versions": all(
                        replayed["fleet"]["vehicles"][key]["received_version"] ==
                        final["fleet"]["vehicles"][key]["received_version"] for key in cars),
                    "physics_replay_position_error_m": {key: math.dist(
                        replayed["fleet"]["vehicles"][key]["position_city_m"],
                        final["fleet"]["vehicles"][key]["position_city_m"]) for key in cars}}
            return {"scene_key": payload["key"], "mode": mode, "variant": variant,
                    "physical_map_version": "updated", "physical_map_sha256": final["map_features_sha256"],
                    "steps": end_step, "simulated_time_s": end_step * payload["timestep_s"],
                    "wall_seconds": time.monotonic() - start, "missions": missions,
                    "planning_inputs_match_actual_car_and_frame": own_pose_matches,
                    "vehicles": cars, "delivery_checkpoints": deliveries, "replay_checks": replay_checks}
        finally:
            session.close()


def comparison_checks(cases):
    offline = {case["variant"]: case for case in cases if case["mode"] == "offline"}
    checks = {"same_physical_map": len({case["physical_map_sha256"] for case in cases}) == 1,
              "same_missions": len({json.dumps(case["missions"], sort_keys=True) for case in cases}) == 1,
              "frame_bound_own_vehicle_inputs": all(case["planning_inputs_match_actual_car_and_frame"] for case in cases),
              "all_routes_use_source_connections": all(not car["route_errors"] for case in cases
                                                        for car in case["vehicles"].values()),
              "no_recipient_collision": all(not car["collision_detected"] for case in cases
                                             for car in case["vehicles"].values()),
              "recipients_use_physics": all(car["motion_source"] == "metadrive_bullet_physics"
                                             and not car["uses_recorded_trajectory"]
                                             for case in cases for car in case["vehicles"].values())}
    if {"old", "structure", "full"} <= set(offline):
        a, b, c = (offline[key]["vehicles"]["B"] for key in ("old", "structure", "full"))
        checks["clear_corridor_vehicle_actually_reaches_goal"] = all(
            case["vehicles"]["C"]["goal_reached_step"] is not None for case in offline.values())
        if cases[0]["scene_key"] == "nuscenes-scene0980":
            checks["no_invented_nuscenes_structure_changes"] = a["first_lane_ids"] == b["first_lane_ids"]
            checks["full_prior_blocks_occupied_route"] = c["first_route_status"] == "no_route"
            checks["full_prior_vehicle_remains_stopped"] = c["net_displacement_m"] < .5 and c["max_speed_m_s"] < .2
            checks["old_map_vehicle_physically_drives"] = a["distance_travelled_m"] > 5
            checks["shared_state_brakes_earlier_than_old_map"] = (
                c["first_braking_step"] is not None and
                (a["first_braking_step"] is None or c["first_braking_step"] < a["first_braking_step"]))
        else:
            checks["real_structure_changes_route"] = a["first_lane_ids"] != b["first_lane_ids"]
            checks["no_invented_argoverse_state_changes"] = b["first_lane_ids"] == c["first_lane_ids"]
            checks["both_road_versions_physically_reach_same_goal"] = all(
                case["vehicles"]["B"]["goal_reached_step"] is not None for case in offline.values())
    for case in cases:
        if case["mode"] != "online" or len(case["delivery_checkpoints"]) != 3:
            continue
        disconnected, published, reconnected = [p["status"]["per_vehicle"] for p in case["delivery_checkpoints"]]
        checks["disconnected_vehicle_retains_knowledge"] = published["C"]["received_version"] == disconnected["C"]["received_version"]
        checks["reconnected_vehicle_receives_latest"] = reconnected["B"]["received_version"] == reconnected["C"]["received_version"]
        replay = case.get("replay_checks")
        if replay:
            checks["rewind_does_not_leak_later_structure"] = replay["rewind_removes_later_structure_delivery"]
            checks["delivery_history_replays_same_versions"] = replay["delivery_history_replays_same_versions"]
            checks["physics_replay_is_repeatable"] = max(replay["physics_replay_position_error_m"].values()) < .01
        if case["scene_key"] == "argoverse-winter2021":
            checks["structure_publish_replans_subscriber_only"] = (
                published["B"]["map_key"] == "updated" and published["C"]["map_key"] == "original"
                and published["B"]["replan_count"] > disconnected["B"]["replan_count"])
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", action="append", choices=["nuscenes-scene0980", "argoverse-winter2021"])
    parser.add_argument("--steps", type=int, help="Limit native ticks; default runs the complete source scene")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/fleet-validation.json")
    parser.add_argument("--snapshots", type=Path, help="Optional directory for real final engine PNGs")
    parser.add_argument("--skip-online", action="store_true")
    args = parser.parse_args()
    if args.steps is not None and args.steps < 1:
        parser.error("--steps must be positive")
    from map_update.simulation_platform_data import load_scene
    report = {"schema": "native-shared-map-comparison-1.0", "scenes": [], "limitations": [
        "Low-speed directed-lane planning and emergency braking; traffic signals and right of way are not implemented.",
        "Source traffic replays recorded motion and does not cooperate with recipient vehicles.",
        "Offline full knowledge is an explicit preloaded prior; online observations use their source timestamps.",
        "An unavailable legal detour produces a stop, not an invented road or lane change.",
        "These are two supplied-scene engineering checks, not a statistical driving safety or detection accuracy benchmark."]}
    for key in args.scene or ["nuscenes-scene0980", "argoverse-winter2021"]:
        payload = load_scene(ROOT, key)
        cases = []
        scene_report = {"scene_key": key, "checks": {}, "cases": cases}
        report["scenes"].append(scene_report)
        for variant in ("old", "structure", "full"):
            print("Native validation: " + key + " / offline / " + variant, file=sys.stderr, flush=True)
            cases.append(run_case(payload, variant, steps=args.steps, output_dir=args.snapshots))
            print(json.dumps({"variant": variant, "vehicles": cases[-1]["vehicles"]}), flush=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        if not args.skip_online:
            print("Native validation: " + key + " / online / full", file=sys.stderr, flush=True)
            cases.append(run_case(payload, "full", mode="online", steps=min(args.steps or 20, 20), output_dir=args.snapshots))
        checks = comparison_checks(cases)
        scene_report["checks"] = checks
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"scene": key, "checks": checks}, ensure_ascii=False), flush=True)
    print("Report: " + str(args.output), flush=True)
    return 0 if all(all(scene["checks"].values()) for scene in report["scenes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
