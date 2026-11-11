"""View or control the ego car on an exported, updated ScenarioNet map."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_runtime import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--mode", choices=("manual", "replay", "controller"), default="manual")
    parser.add_argument("--render", choices=("none", "2D", "3D"), default="3D")
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--scenario-index", type=int, default=0)
    parser.add_argument("--controller", help="package.module:function; callable(state) -> [steering, throttle/brake]")
    parser.add_argument("--action", type=float, nargs=2, metavar=("STEERING", "THROTTLE"),
                        help="Fixed action for a physics smoke test, not a planner")
    parser.add_argument("--goal", type=float, nargs=2, metavar=("X", "Y"),
                        help="New goal in source scenario XY metres, before MetaDrive centralization")
    parser.add_argument("--goal-radius", type=float, default=2.0)
    parser.add_argument("--no-traffic", action="store_true", help="Disable moving traffic/signals; persistent map obstacles remain")
    parser.add_argument("--environment-geometry", type=Path, help="Source-attributed building/terrain geometry JSON in the scene's city coordinates")
    parser.add_argument("--hide-updates", action="store_true", help="Hide update annotations; map geometry is unchanged")
    parser.add_argument("--snapshot", type=Path, help="Save a 3D PNG at the end; refuses an existing path")
    parser.add_argument("--offscreen", action="store_true", help="3D offscreen render via Panda3D EGL; replay/controller mode only")
    parser.add_argument("--report", type=Path, help="New JSON report path; never overwrites a file")
    args = parser.parse_args()
    if args.report is not None and args.report.exists():
        parser.error("Report path already exists; choose a new path")
    try:
        result = run(args.database_path, mode=args.mode, render=args.render, steps=args.steps,
                     scenario_index=args.scenario_index, controller=args.controller,
                     action=args.action, goal=args.goal, goal_radius=args.goal_radius,
                     no_traffic=args.no_traffic, snapshot=args.snapshot, offscreen=args.offscreen,
                     environment_geometry=args.environment_geometry, show_updates=not args.hide_updates)
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
        print(json.dumps({k: v for k, v in result.items() if k not in {"trace", "obstacle_trace", "export_manifest"}},
                         ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, ImportError, FileExistsError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
