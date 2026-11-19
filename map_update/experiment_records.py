"""Offline experiment accounting and group-separated dataset manifests."""
from hashlib import sha256
import json
import math
from pathlib import Path


def read_journal(path):
    records = []
    lines = Path(path).read_text().splitlines()
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'Incomplete or corrupt journal line {index + 1}') from error
        if not isinstance(row, dict) or set(row) != {'event', 'data'}:
            raise ValueError('Invalid journal record')
        records.append(row)
    return records


def summarize_journal(path):
    runs = []
    current = None
    previous = {}
    for row in read_journal(path):
        data = row['data']
        if row['event'] == 'open':
            current = dict(session_id=data['session_id'], scene_id=data['scene_id'],
                           start_step=data['step'], end_step=data['step'], frames=0,
                           distance_m={}, contact_rows=0, replay_error_max_m=None,
                           plans=0, map_changes=0, evidence_submissions=0, stopped=None)
            runs.append(current)
            previous = data['objects']
        elif current is not None:
            if row['event'] == 'advance':
                current['end_step'] = data['step']
                current['frames'] += 1
                current['stopped'] = data.get('stopped') or data.get('service_stopped')
                for key, actor in data['objects'].items():
                    if key in previous:
                        current['distance_m'][key] = current['distance_m'].get(key, 0.) + math.dist(
                            previous[key]['position_m'], actor['position_m'])
                previous = data['objects']
            elif row['event'] == 'evaluation':
                current['contact_rows'] += len(data['contacts'])
                current['replay_error_max_m'] = max(current['replay_error_max_m'] or 0., data['replay_error_m'])
            elif row['event'] == 'plan_accepted':
                current['plans'] += 1
            elif row['event'] == 'map_published':
                current['map_changes'] += 1
            elif row['event'] == 'evidence_accepted':
                current['evidence_submissions'] += 1
    return dict(schema='recorded-experiment-report-1', runs=runs,
                contact_semantics='per-frame contact rows; not independent crashes or planner fault',
                evaluation_scope='executed state and interface accounting; no planning success claim')


def freeze_manifest(rows, root):
    """Explicit group labels keep overlapping source scenes in one split."""
    if not isinstance(rows, list) or not rows:
        raise ValueError('Manifest rows are required')
    root = Path(root).resolve()
    seen, groups, result = set(), {}, []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'scene_id', 'group_id', 'split', 'source'}:
            raise ValueError('Manifest rows require path, scene_id, group_id, split and source')
        if any(not isinstance(value, str) or not value for value in row.values()):
            raise ValueError('Manifest labels must be nonempty strings')
        if row['split'] not in ('development', 'validation', 'test'):
            raise ValueError('Unknown dataset split')
        if row['group_id'] in groups and groups[row['group_id']] != row['split']:
            raise ValueError('A source group cannot appear in multiple splits')
        path = (root / row['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Manifest file must be inside the dataset root')
        digest = sha256(path.read_bytes()).hexdigest()
        if row['scene_id'] in seen or digest in seen:
            raise ValueError('Repeated scene identity or file content')
        seen.update((row['scene_id'], digest))
        groups[row['group_id']] = row['split']
        result.append(dict(row, sha256=digest))
    return dict(schema='recorded-group-manifest-1', records=result,
                grouping='caller-declared source/overlap/location groups; not inferred from frame splits')


def verify_manifest(manifest, root):
    rows = [{key:value for key,value in row.items() if key != 'sha256'} for row in manifest['records']]
    checked = freeze_manifest(rows, root)
    if checked != manifest:
        raise ValueError('Manifest content or file digest mismatch')
    return checked
