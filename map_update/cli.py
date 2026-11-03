import argparse
import json
from pathlib import Path

from .adapters import adapt_proposal
from .demo import base_map, demo_records
from .engine import Maintainer, Policy, rollback
from .output import write_bundle
from .output import write_intake
from .delivery import inspect_delivery
from .scene import inspect_scene
from .output import write_scene
from .output import write_maintenance
from .reliability import integrate_m3


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Shared map maintenance research prototype")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run explicitly synthetic repeated visits")
    demo.add_argument("--out", required=True)
    run = commands.add_parser("run", help="replay canonical evidence from a verified baseline")
    run.add_argument("--map", required=True)
    run.add_argument("--observations", required=True)
    run.add_argument("--policy")
    run.add_argument("--as-of-ns", help="causal cutoff; later evidence is excluded")
    run.add_argument("--out", required=True)
    rb = commands.add_parser("rollback", help="append a restoration version; original bundle unchanged")
    rb.add_argument("--bundle", required=True)
    rb.add_argument("--version", required=True)
    rb.add_argument("--out", required=True)
    adapt = commands.add_parser("adapt-proposal", help="join explicit ChangeRecord context with M3 windows")
    adapt.add_argument("--input", required=True)
    adapt.add_argument("--output", required=True)
    intake = commands.add_parser("inspect-delivery", help="audit actual stage3/5/6 files; evidence viewer only, no invented map")
    intake.add_argument("--root", required=True)
    intake.add_argument("--out", required=True)
    scene = commands.add_parser("inspect-scene", help="load original nuScenes map and exact-frame geometry; no automatic writeback")
    scene.add_argument("--raw-root", required=True)
    scene.add_argument("--delivery-root", default="external/perception_delivery")
    scene.add_argument("--out", required=True)
    m3 = commands.add_parser("integrate-m3", help="join the real M3 supplement and export reference / event / review layers")
    m3.add_argument("--raw-root", required=True)
    m3.add_argument("--delivery-root", default="external/perception_delivery")
    m3.add_argument("--handoff", required=True)
    m3.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "integrate-m3":
            data = integrate_m3(args.raw_root, args.delivery_root, args.handoff)
            dest = write_maintenance(data, args.out)
            print(f"Saved {dest}; M3 review units={len(data['m3']['review_units'])}; "
                  f"event candidates={len(data['event_layer']['events'])}; base map updates=0")
            print("M3 joined. Coverage/revisits/verified edit evidence remain insufficient; planning is not a prerequisite.")
            return
        if args.command == "inspect-scene":
            data = inspect_scene(args.raw_root, args.delivery_root)
            dest = write_scene(data, args.out)
            print(f"Saved {dest}; frames={len(data['replay'])}; reference_features={len(data['reference_map']['elements'])}; updates=0")
            return
        if args.command == "inspect-delivery":
            data = inspect_delivery(args.root)
            dest = write_intake(data, args.out)
            print(f"Saved {dest}; candidates={data['summary']['candidates']}; map_updates=0; validation checks={len(data['validation']['checks'])}")
            print("Evidence intake only: verified map, M3 reliability and independent revisits remain unavailable.")
            return
        if args.command == "adapt-proposal":
            data = adapt_proposal(read(args.input))
            with open(args.output, "x", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            print(f"Normalized {len(data)} records; run performs full baseline/schema validation.")
            return
        if args.command == "demo":
            bundle = Maintainer(base_map()).run(demo_records())
        elif args.command == "run":
            policy = Policy(**read(args.policy)) if args.policy else Policy()
            bundle = Maintainer(read(args.map), policy).run(read(args.observations), args.as_of_ns)
        else:
            bundle = rollback(read(args.bundle), args.version)
        dest = write_bundle(bundle, args.out)
        print(f"Saved {dest}; version={bundle['current_map']['version']}; updates={len(bundle['versions'])-1}")
        print("Offline viewer: index.html; integration output: map.json / planning.json / audit.json")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
