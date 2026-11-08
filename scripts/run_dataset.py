"""Run each supplied dataset through its own adapter and output namespace."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.datasets import run_paths
from map_update.evaluation import build_dashboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=['nuscenes', 'argoverse'], default='nuscenes')
    parser.add_argument('--run-name', default='review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    try:
        paths = run_paths(ROOT, args.dataset, args.run_name)
        if args.dataset == 'argoverse':
            from map_update.argoverse import run_argoverse
            output = run_argoverse(paths['raw_root'], paths['output'])
        else:
            output = build_dashboard(**paths, dataset_id=args.dataset, workbench=True)
    except ValueError as error:
        parser.exit(2, str(error)+'\n')
    print(f'{args.dataset}: {output}')


if __name__ == '__main__':
    main()
