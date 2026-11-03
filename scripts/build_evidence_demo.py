"""Build the Evaluation and Evidence dashboard from the supplied real data."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.evaluation import build_dashboard

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', required=True)
parser.add_argument('--workbench', action='store_true', help='Build the user-operated review workbench')
parser.add_argument('--raw-root', default=str(ROOT/'AIE1903 原始资料'))
parser.add_argument('--delivery-root', default=str(ROOT/'external/perception_delivery'))
parser.add_argument('--m3-root', default=str(ROOT/'AIE1903 原始资料/M3_scene0980_proposal_supplement 2'))
args = parser.parse_args()
dest = build_dashboard(args.raw_root, args.delivery_root, args.m3_root, args.out, workbench=args.workbench)
print(f'Built real evidence dashboard: {dest}/index.html')
