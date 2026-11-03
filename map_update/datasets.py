"""Dataset boundaries for the current, reviewed execution scope.

Source files keep their original locations. Registry status concerns permission
to run this workflow, not scientific ground truth or calibration quality.
"""
from copy import deepcopy
from pathlib import Path


REGISTRY = {
    'nuscenes': {
        'dataset_id': 'nuscenes', 'label': 'nuScenes', 'status': 'active',
        'enabled': True, 'scene_ids': ['scene-0980'],
        'raw_root': 'AIE1903 原始资料',
        'delivery_root': 'external/perception_delivery',
        'm3_root': 'AIE1903 原始资料/M3_scene0980_proposal_supplement 2',
        'note': '使用此前处理好的 scene-0980；允许运行不代表地图变化已确认。',
    },
    'argoverse': {
        'dataset_id': 'argoverse', 'label': 'Argoverse', 'status': 'pending_review',
        'enabled': False, 'scene_ids': [],
        'raw_root': None, 'delivery_root': None, 'm3_root': None,
        'note': '场景待审核；本轮不运行、不纳入评估，也不复用 nuScenes 输出。',
    },
}


def require_active(dataset_id):
    if dataset_id not in REGISTRY:
        raise ValueError('Unknown dataset: '+str(dataset_id))
    entry = REGISTRY[dataset_id]
    if not entry['enabled']:
        raise ValueError(entry['label']+' 待审核：本轮不运行、不评估。请使用 --dataset nuscenes。')
    return deepcopy(entry)


def registry_snapshot():
    return deepcopy(REGISTRY)


def run_paths(project_root, dataset_id, run_name):
    entry = require_active(dataset_id)  # Reject pending data before filesystem work.
    if not run_name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_name):
        raise ValueError('run_name must contain only letters, digits, hyphens or underscores')
    root = Path(project_root)
    return {key: root / entry[key] for key in ('raw_root', 'delivery_root', 'm3_root')} | {
        'output': root / 'outputs' / dataset_id / entry['scene_ids'][0] / run_name}
