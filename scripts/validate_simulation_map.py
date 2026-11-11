"""Validate map/state export integrity and report separately frozen detector metrics."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_validation import build_detection_metrics, load_detection_metrics, validate_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-path', type=Path)
    parser.add_argument('--out', type=Path, help='New report JSON; refuses existing files')
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--pipeline-result', type=Path, help='Freeze portable detector evaluation instead of auditing a database')
    parser.add_argument('--raw-root', type=Path, default=ROOT / 'AIE1903 原始资料')
    args = parser.parse_args()
    if bool(args.database_path) == bool(args.pipeline_result):
        parser.error('Choose exactly one of --database-path and --pipeline-result')
    if args.out and args.out.exists():
        parser.error('Output already exists; choose a new file')
    try:
        if args.pipeline_result:
            result = build_detection_metrics(args.pipeline_result, args.raw_root)
        else:
            manifest = json.loads((args.database_path / 'simulation_manifest.json').read_text(encoding='utf-8'))
            metrics = load_detection_metrics(args.repo_root, manifest['dataset'], manifest['scene_id'],
                                             pipeline_sha256=manifest['provenance'].get('pipeline_result_sha256'))
            result = validate_database(args.database_path, detection_metrics=metrics)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open('x', encoding='utf-8') as stream:
                stream.write(rendered)
        print(rendered)
        if args.database_path and not result['integrity']['passed']:
            return 1
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
