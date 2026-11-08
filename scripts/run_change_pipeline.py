"""Run the reliability -> structure/state routing -> write-eligibility pipeline."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.pipeline_v2 import run_pipeline

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, default=ROOT/'AIE1903 原始资料')
    parser.add_argument('--delivery-root', type=Path, default=ROOT/'external/perception_delivery')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(run_pipeline(args.raw_root, args.delivery_root, args.out))
    except (ValueError, FileExistsError) as error:
        parser.exit(2, str(error)+'\n')
