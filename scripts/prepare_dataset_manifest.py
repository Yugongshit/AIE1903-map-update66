"""Freeze explicit source-group splits or verify an existing manifest."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.experiment_records import freeze_manifest, verify_manifest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--input', type=Path, required=True)
parser.add_argument('--out', type=Path)
parser.add_argument('--verify', action='store_true')
args = parser.parse_args()
data = json.loads(args.input.read_text())
if args.verify:
    verify_manifest(data, args.root)
    print('Manifest identities, grouping and file checksums verified')
else:
    if args.out is None:
        parser.error('--out is required when freezing a manifest')
    result = freeze_manifest(data, args.root)
    with args.out.open('x') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write('\n')
