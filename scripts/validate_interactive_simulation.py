"""Record real MetaDrive six-car interaction checks, JSON and rendered PNGs.

Run with the simulation interpreter, without another native simulator running:
  python scripts/validate_interactive_simulation.py --out outputs/interactive-check

Each scene runs in its own native process. The external controller below is a
test-only longitudinal headway example, not the user's Planning implementation.
HTTP ownership/lease and browser checks belong to the platform integration test.
Add --patrol-only for a separate long AI run past the source recording length;
it records actual target transitions and whether any source-graph cycle finishes.
"""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCENES = ("nuscenes-scene0980", "argoverse-winter2021")
IDS = ("ego", "P1", "P2", "P3", "P4", "P5")
BRAKE = [0., -1.]


def brake_controls():
    return {identifier: {"mode": "external", "action": list(BRAKE)} for identifier in IDS}


def validation_passed(checks, completed):
    return bool(completed and checks) and all(item["passed"] for item in checks)


def native_map_hash(session):
    """Re-hash actual loaded map data; do not trust a cached status label."""
    from map_update.simulation_bridge import feature_sha256
    return feature_sha256(session.env.engine.data_manager.current_scenario["map_features"])


def headway_test_controller(observation):
    """Use current physical P1/ego poses, solely to verify the external chain."""
    cars = observation["vehicles"]
    ego, leader = cars["ego"], cars["P1"]
    delta = [leader["position_city_m"][i] - ego["position_city_m"][i] for i in (0, 1)]
    heading = ego["heading_rad"]
    gap = delta[0] * math.cos(heading) + delta[1] * math.sin(heading)
    if gap <= 12.:
        return list(BRAKE), {"reason": "test_headway_brake", "observed_gap_m": gap}
    error = (math.atan2(delta[1], delta[0]) - heading + math.pi) % (2 * math.pi) - math.pi
    action = [max(-.15, min(.15, error * .5)), max(-.3, min(.4, (2.5 - ego["speed_m_s"]) * .4))]
    return action, {"reason": "test_follow_current_p1", "observed_gap_m": gap}


def compact_vehicles(status):
    keys = ("mode", "position_city_m", "heading_rad", "speed_m_s", "action", "requested_action",
            "collision", "collision_contact_steps", "stopped_reason", "motion_source", "patrol_goal_index",
            "completed_patrol_goals")
    return {identifier: {key: deepcopy(row.get(key)) for key in keys}
            for identifier, row in status["interactive"]["vehicles"].items()}


def collision_rows(status, phase):
    return [{"phase": phase, "step": status["step"], "vehicle_id": identifier,
             "contacts": [kind for kind, value in row["collision"].items() if value],
             "recorded_contact_steps": row.get("collision_contact_steps", []),
             "position_city_m": deepcopy(row.get("position_city_m")),
             "speed_m_s": row.get("speed_m_s"), "action": deepcopy(row.get("action")),
             "all_vehicle_positions": {key: deepcopy(actor.get("position_city_m"))
                                       for key, actor in status["interactive"]["vehicles"].items()}}
            for identifier, row in status["interactive"]["vehicles"].items()
            if any(row["collision"].values()) or row.get("collision_contact_steps")]


def patrol_transitions(previous, current, missions):
    """Report observed native target changes, distinguishing a completed loop."""
    transitions = []
    for identifier in IDS[1:]:
        old_index = previous["interactive"]["vehicles"][identifier].get("patrol_goal_index", 0)
        new_index = current["interactive"]["vehicles"][identifier].get("patrol_goal_index", 0)
        if old_index != new_index:
            mission = missions[identifier]
            complete = (mission.get("patrol_loop", False) and new_index == 0 and
                        old_index == len(mission.get("patrol_goals_scenario_m", []))-1)
            transitions.append({"vehicle_id": identifier, "step": current["step"],
                                "from_goal": old_index, "to_goal": new_index,
                                "completed_source_cycle": bool(complete)})
    return transitions


def _native_tick(session, controls, steps=1):
    """Same physical sequence as interactive_tick, render only checkpoints.

Controls are renewed for each call, with at most three decisions per lease.
This avoids repeatedly encoding hundreds of PNGs in a physics validation run.
The actual public interactive_tick is separately exercised below.
"""
    from map_update.simulation_session import _suspended_rendering
    if not 1 <= steps <= 3:
        raise ValueError("Validation leases contain 1..3 decisions")
    session.interactive.controls(controls)
    with _suspended_rendering(session.env):
        for _ in range(steps):
            session.env.step(session.interactive.before_step())
            session.interactive.after_step()
    session._refresh_state()
    return session.status()


def validate_scene(scene_key, output, *, width=480, height=320, patrol_only=False):
    from map_update.simulation_interactive import _footprint
    from map_update.simulation_interactive_scenarios import choose_interactive_missions
    from map_update.simulation_platform_data import build_snapshot, load_scene, scene_database
    from map_update.simulation_session import SimulationSession

    output.mkdir(parents=True, exist_ok=False)
    frames = output / "native-frames"
    frames.mkdir()
    source = load_scene(ROOT, scene_key)
    report = {"schema": "simulation-interactive-native-validation-1.0", "scene_key": scene_key,
              "checks": [], "phases": {}, "unexpected_collisions": [], "screenshots": [],
              "external_controller_scope": "test_only_headway_controller_not_user_planning_module",
              "render_size": [width, height], "source_map_sha256": source["versions"]["updated"]["map_sha256"],
              "real_native_physics": True, "http_and_browser_ownership_tested_here": False}
    report["validation_scope"] = "long_ai_patrol" if patrol_only else "controls_and_recording_independence"
    session = SimulationSession(frames, width=width, height=height)
    started = time.monotonic()
    completed = False

    def check(name, passed, details=None):
        report["checks"].append({"name": name, "passed": bool(passed), "details": details})
        print(("PASS " if passed else "FAIL ") + scene_key + ": " + name, flush=True)

    def picture(label):
        status = session.frame()
        destination = output / (label + ".png")
        shutil.copyfile(frames / status["frame_file"], destination)
        data = destination.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssertionError("Native snapshot is not a PNG")
        report["screenshots"].append({"file": destination.name, "step": status["step"],
                                      "sha256": sha256(data).hexdigest(), "bytes": len(data)})
        return status

    def observe(status, phase):
        report["unexpected_collisions"].extend(collision_rows(status, phase))
        return status

    def phase_end(label, initial, final):
        before, after = compact_vehicles(initial), compact_vehicles(final)
        displacement = {identifier: math.dist(before[identifier]["position_city_m"], after[identifier]["position_city_m"])
                        for identifier in IDS}
        report["phases"][label] = {"start_step": initial["step"], "end_step": final["step"],
                                  "initial": before, "final": after, "displacement_m": displacement}
        actual_hash = native_map_hash(session)
        report["phases"][label]["actual_runtime_map_sha256"] = actual_hash
        check(label + " preserves the loaded map", actual_hash == report["initial_runtime_map_sha256"])
        picture(label)
        return displacement

    try:
        missions = choose_interactive_missions(source)
        first = session.open(scene_database(ROOT, scene_key), interactive={"missions": missions})
        report["initial_runtime_map_sha256"] = native_map_hash(session)
        cars = first["interactive"]["vehicles"]
        report["initial"] = compact_vehicles(first)
        check("six native physics vehicles with no recorded moving traffic",
              set(cars) == set(IDS) and not first["ego_replay"] and not first["interactive"]["recorded_moving_traffic"]
              and all(row["motion_source"] == "metadrive_bullet_physics" for row in cars.values()))
        physical = list(cars.values()) + first["interactive"]["static_actors"]
        overlaps = [(identifier, other["vehicle_id"]) for identifier, car in cars.items() for other in physical
                    if car["native_object_id"] != other["native_object_id"] and _footprint(car).intersects(_footprint(other))]
        check("initial native vehicle footprints do not intersect other cars or static objects", not overlaps, overlaps)
        check("physical static world retains all source static objects",
              len(first["interactive"]["static_actors"]) == len(source["static_overview_actors"]),
              {"actual": len(first["interactive"]["static_actors"]), "source": len(source["static_overview_actors"])})
        picture("initial-six-cars")

        current, transitions = first, []
        ai_target = max(250, first["length"] + 10) if patrol_only else 60
        by_id = {mission["vehicle_id"]: mission for mission in missions}
        while current["step"] < ai_target:
            previous = current
            current = observe(_native_tick(session, {}, min(3, ai_target-current["step"])), "ai")
            transitions.extend(patrol_transitions(previous, current, by_id))
        movement = phase_end("ai", first, current)
        check("all five independent AI cars actually move", all(movement[k] > .3 for k in IDS[1:]), movement)
        check("ego waits braked for external Planning", movement["ego"] < .15 and
              current["interactive"]["vehicles"]["ego"]["action"] == BRAKE, movement["ego"])
        if patrol_only:
            report["patrol_transitions"] = transitions
            report["completed_source_cycles"] = [row for row in transitions if row["completed_source_cycle"]]
            report["completed_cycle_in_run"] = bool(report["completed_source_cycles"])
            check("AI world continues beyond the source recording", current["step"] == ai_target and not current["at_end"],
                  {"live_step": current["step"], "source_last_step": current["last_step"]})
            check("at least one AI advances to another verified source-lane goal", bool(transitions), transitions)
            check("long AI run has no observed collision", not report["unexpected_collisions"],
                  report["unexpected_collisions"][:10])
            completed = True
            return report

        initial = session.seek(0)
        controls = brake_controls()
        controls["P1"] = {"mode": "manual", "action": [.35, .5]}
        for _ in range(20):
            current = observe(_native_tick(session, controls), "manual")
        movement = phase_end("manual", initial, current)
        angle = abs((current["interactive"]["vehicles"]["P1"]["heading_rad"] -
                     initial["interactive"]["vehicles"]["P1"]["heading_rad"] + math.pi) % (2 * math.pi) - math.pi)
        check("manual steering and throttle change actual P1 position and heading", movement["P1"] > .5 and angle > .02,
              {"movement_m": movement["P1"], "heading_change_rad": angle})
        omitted = brake_controls()
        del omitted["P1"]
        missing = session.interactive_tick(omitted)
        row = missing["interactive"]["vehicles"]["P1"]
        check("public interactive_tick brakes a manual car when its action is missing",
              row["mode"] == "manual" and row["action"] == BRAKE and row["stopped_reason"] == "waiting_for_action", row["action"])
        for _ in range(20):
            current = observe(_native_tick(session, omitted), "manual-missing-action")
        check("missing manual action brings P1 to rest", current["interactive"]["vehicles"]["P1"]["speed_m_s"] < .2)

        initial = session.seek(0)
        controls = brake_controls()
        for identifier in IDS[1:]:
            controls[identifier] = {"mode": "external", "action": [0., .45]}
        current = session.interactive_tick(controls, 3)
        for _ in range(9):
            current = observe(_native_tick(session, controls), "external-five-car-batch")
        movement = phase_end("external-five-car-batch", initial, current)
        check("all five external actions execute through actual physics", all(
            movement[k] > .2 and current["interactive"]["vehicles"][k]["action"] == [0., .45]
            and current["interactive"]["vehicles"][k]["mode"] == "external" for k in IDS[1:]), movement)

        initial = session.seek(0)
        current, decisions = initial, []
        for tick in range(100):
            action, rationale = headway_test_controller(current["interactive"])
            controls = brake_controls()
            controls["ego"]["action"] = action
            if tick < 10:
                controls["P1"]["action"] = [0., .3]
            observation = current["interactive"]["vehicles"]
            decisions.append({"observation_step": current["step"], "ego_position": observation["ego"]["position_city_m"],
                              "p1_position": observation["P1"]["position_city_m"], "ego_action": action, **rationale})
            current = observe(_native_tick(session, controls), "external-test-controller")
        movement = phase_end("external-test-controller", initial, current)
        report["test_controller_decisions"] = decisions
        reasons = {item["reason"] for item in decisions}
        check("test external controller reads moving P1 and produces drive then brake actions",
              movement["P1"] > .05 and movement["ego"] > 3 and
              reasons == {"test_follow_current_p1", "test_headway_brake"}, movement)
        check("test external ego stops after current headway becomes small",
              current["interactive"]["vehicles"]["ego"]["speed_m_s"] < .2,
              {"last_observed_gap_m": decisions[-1]["observed_gap_m"],
               "speed_m_s": current["interactive"]["vehicles"]["ego"]["speed_m_s"]})

        initial = session.seek(0)
        target = max(250, initial["length"] + 10)
        current = initial
        while current["step"] < target:
            current = observe(_native_tick(session, brake_controls(), min(3, target-current["step"])), "beyond-recording")
        phase_end("beyond-recording", initial, current)
        check("live physics continues beyond the recording with all six cars",
              current["step"] == target and current["step"] > current["last_step"] and not current["at_end"]
              and set(current["interactive"]["vehicles"]) == set(IDS),
              {"live_step": current["step"], "recording_last_step": current["last_step"], "sim_time_s": current["time_s"]})

        replay = session.open(scene_database(ROOT, scene_key))
        replay = session.seek(10)
        expected = build_snapshot(source, 10)["ego"]["position"][:2]
        check("ordinary replay still follows the source recording",
              replay["ego_replay"] and replay["interactive"] is None and
              math.dist(replay["position_scenario_m"], expected) < .05,
              {"actual": replay["position_scenario_m"], "recorded": expected})
        picture("ordinary-replay")
        check("no unreported collision in any physical phase", not report["unexpected_collisions"],
              report["unexpected_collisions"][:10])
        completed = True
    except Exception as error:
        report["exception"] = repr(error)
        check("native validation completed without exception", False, repr(error))
        try:
            picture("failure")
        except Exception:
            pass
    finally:
        session.close()
        report["duration_wall_s"] = time.monotonic() - started
        report["completed"] = completed
        report["passed"] = validation_passed(report["checks"], completed)
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--patrol-only", action="store_true", help="Run only a long six-car AI patrol with ego waiting")
    args = parser.parse_args()
    if min(args.width, args.height) < 320:
        parser.error("Native validation dimensions must be at least 320 pixels")
    if args.out.exists():
        parser.error("Choose a new output directory; existing evidence is not overwritten")
    if args.scene:
        return 0 if validate_scene(args.scene, args.out, width=args.width, height=args.height,
                                  patrol_only=args.patrol_only)["passed"] else 1
    args.out.mkdir(parents=True)
    summaries = []
    for scene in SCENES:
        destination = args.out / scene
        with (args.out / (scene + ".log")).open("w", encoding="utf-8") as log:
            command = [sys.executable, str(Path(__file__).resolve()), "--scene", scene,
                "--out", str(destination), "--width", str(args.width), "--height", str(args.height)]
            if args.patrol_only:
                command.append("--patrol-only")
            result = subprocess.run(command,
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        report_path = destination / "report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {"passed": False}
        summaries.append({"scene_key": scene, "passed": bool(report["passed"] and result.returncode == 0), "exit_code": result.returncode,
                          "report": str(report_path.relative_to(args.out))})
        print(("PASS " if summaries[-1]["passed"] else "FAIL ") + scene, flush=True)
    summary = {"schema": "simulation-interactive-native-validation-summary-1.0", "scenes": summaries,
               "passed": all(row["passed"] for row in summaries)}
    (args.out / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
