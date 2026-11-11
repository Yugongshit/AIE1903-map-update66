"""Create an explicitly synthetic marking edit to exercise the simulation bridge.

This is an integration example, not an observation-derived map change.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from map_update.simulation_bridge import load_scenario, feature_sha256

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output exists; choose a new file')
    scenario = load_scenario(args.scenario)
    meta = scenario['metadata']
    if meta.get('dataset') != 'nuscenes':
        parser.error('This controlled example expects a nuScenes scene')
    ego = scenario['tracks'][meta['sdc_id']]['state']['position'][0][:2]
    candidates = [(key, feature) for key, feature in scenario['map_features'].items()
                  if feature['type'].startswith('ROAD_LINE_') and len(feature.get('polyline', [])) >= 2]
    if not candidates:
        parser.error('No marking polyline available')
    key, before = min(candidates, key=lambda pair: min(math.dist(ego, p[:2]) for p in pair[1]['polyline']))
    # A fixed one-metre translation is intentionally visible, deterministic and
    # labelled synthetic; it is never added to the scientific prediction branch.
    after = copy.deepcopy(before)
    after['polyline'] = [[float(p[0]) + 1.0, float(p[1])] for p in before['polyline']]
    for field, value in list(after.items()):
        if hasattr(value, 'tolist'):
            after[field] = value.tolist()
    patch = {
        'schema': 'simulation-map-patch-1.0', 'dataset': 'nuscenes',
        'scene_id': scenario['id'], 'map': meta['map'],
        'coordinate': 'nuScenes_global', 'units': 'm',
        'base_scenario_sha256': hashlib.sha256(args.scenario.read_bytes()).hexdigest(),
        'provenance': {'source': 'controlled_demo', 'synthetic': True,
                       'description': 'Translate one nearby marking +1m in city X to test export/load; not a detected change.'},
        'operations': [{'operation': 'replace', 'feature_id': key,
                        'before_sha256': feature_sha256(before), 'feature': after}],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump(patch, handle, ensure_ascii=False, indent=2)
    print(args.out)


if __name__ == '__main__':
    main()
