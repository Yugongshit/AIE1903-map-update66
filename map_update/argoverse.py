"""Rerun the delivered Argoverse M3/M4 algorithms from frozen M2 tables.

Prediction uses an isolated copy containing no M8 data. Supplied M8 exports are
opened only after prediction artifacts have been written and hashed. Retrieval
over the supplied map is a development diagnostic, not visible-road accuracy.
"""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import shutil
import subprocess
import sys


M2_FILES = ('prior_map.json', 'tables/frames.jsonl', 'tables/views.jsonl',
            'tables/observations.jsonl', 'tables/matches.jsonl', 'summary.json',
            'run.json', 'prior_provenance.json')


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _package(raw, stage):
    found = list((raw/'deliveries').glob('*_'+stage+'_v1'))
    if len(found) != 1:
        raise ValueError('Exactly one supplied '+stage+' package is required')
    return found[0]


def _checked(raw, package, names, role, sources):
    manifest = package/'file_manifest.json'
    entries = {r['path'].replace('\\', '/'): r for r in _read(manifest)['files']}
    sources.append({'path': manifest.relative_to(raw).as_posix(), 'sha256': _hash(manifest), 'role': role+'_manifest'})
    for name in names:
        path = package/name
        entry = entries.get(name)
        if not entry or _hash(path) != entry['sha256']:
            raise ValueError('Supplied manifest hash mismatch: '+str(path))
        sources.append({'path': path.relative_to(raw).as_posix(), 'sha256': _hash(path), 'role': role})


def _identity(m2):
    run, prior = _read(m2/'run.json'), _read(m2/'prior_map.json')
    scene, run_id = run['scene_id'], run['run_id']
    if prior['scene_id'] != scene or m2.name != scene+'_M2_v1' or run.get('inference_gt_read') is not False:
        raise ValueError('M2 prior, package and inference identity disagree')
    tables = {name: _rows(m2/'tables'/f'{name}.jsonl') for name in ('frames', 'views', 'observations', 'matches')}
    indices = {}
    for name, field in [('frames', 'frame_id'), ('views', 'view_id'), ('observations', 'observation_id'), ('matches', 'observation_id')]:
        rows = tables[name]
        indices[name] = {r[field]: r for r in rows}
        if len(indices[name]) != len(rows) or any(r['run_id'] != run_id for r in rows):
            raise ValueError('Duplicate or cross-run M2 '+name)
        if name != 'matches' and any(r['scene_id'] != scene for r in rows):
            raise ValueError('Cross-scene M2 '+name)
    if set(indices['observations']) != set(indices['matches']):
        raise ValueError('M2 observation/match identities differ')
    for row in tables['frames']:
        if row['timestamp_ns'] != row['frame_id'] or not row['timestamp_ns'].isdecimal():
            raise ValueError('M2 frame timestamps must retain exact nanosecond identity')
    for row in tables['views']:
        if row['frame_id'] not in indices['frames']:
            raise ValueError('M2 view references unknown frame')
    for row in tables['observations']:
        view = indices['views'].get(row['view_id'])
        frame = indices['frames'].get(row['frame_id'])
        match = indices['matches'][row['observation_id']]
        if not view or not frame or row['clip_id'] != frame['clip_id'] or any(row[k] != view[k] for k in ('frame_id', 'camera', 'image_timestamp_ns')) or match['view_id'] != row['view_id']:
            raise ValueError('M2 observation attribution mismatch')
    return scene, tables


# Only delivered scripts and frozen M2 inputs are staged for this subprocess.
# M4's numeric functions avoid its optional QA-image/ZIP packaging dependencies.
_WORKER = r'''
import sys,json,hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0,sys.argv[1])
import m3_changes as m3
m3.ROOT=Path(sys.argv[2]);m3.main()
import m4_fuse as m4
source=next((m3.ROOT/'deliveries').glob('*_M3_v1'))
m2=next((m3.ROOT/'deliveries').glob('*_M2_v1'))
candidates=m3.rows(source/'change_candidates.jsonl')
frames=m3.rows(m2/'tables/frames.jsonl');checks=m3.rows(source/'map_checks.jsonl')
changes=[];evidence=[];groups=m4.group_candidates(candidates)
for group in groups:
 c,e=m4.fuse(group,frames,checks);changes.append(c);evidence.extend(e)
out=Path(sys.argv[3]);out.mkdir()
for name,rows in [('change_records',changes),('frame_evidence',evidence)]:
 (out/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in rows),encoding='utf-8')
m3.write(out/'rules.json',m4.RULES)
m3.write(out/'summary.json',{'input_candidate_observations':len(candidates),'candidate_groups':len(groups),'change_records':len(changes),'statuses':dict(Counter(c['status'] for c in changes)),'confirmed_changes':sum(c['status']=='confirmed' for c in changes),'available_visibility_scores':sum(c['evidence_score'] is not None for c in changes),'frame_evidence_rows':len(evidence),'reasons':dict(Counter(r for c in changes for r in c['reason_codes'])),'gt_read':False})
m3.write(out/'provenance.json',{'entrypoints':['group_candidates','fuse'],'source_script_sha256':hashlib.sha256(Path(m4.__file__).read_bytes()).hexdigest(),'gt_read':False,'qa_images_generated':False})
'''


def _predict(output, scripts, m2):
    runtime = output/'prediction_inputs'
    staged = runtime/'deliveries'/m2.name
    staged.mkdir(parents=True)
    for name in M2_FILES:
        dest = staged/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(m2/name, dest)
    completed = subprocess.run([sys.executable, '-c', _WORKER, str(scripts), str(runtime), str(output/'M4')],
                               check=True, capture_output=True, text=True)
    (output/'prediction.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
    shutil.move(str(next((runtime/'deliveries').glob('*_M3_v1'))), output/'M3')


def _equivalent(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equivalent(x, y) for x, y in zip(a, b))
    if type(a) is float and type(b) is float:
        return isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return type(a) is type(b) and a == b


def _m2_diagnostics(prior, tables):
    boundaries = {(lane['id'], side): (tuple((p['x'], p['y']) for p in lane[side+'_lane_boundary']), lane[side+'_lane_mark_type'])
                  for lane in prior['lane_segments'] for side in ('left', 'right')}
    ambiguous = [r for r in tables['matches'] if r['status'] == 'ambiguous']
    duplicate = 0
    for row in ambiguous:
        if len(row['candidates']) < 2:
            continue
        a, b = [boundaries[(c['map_element_id'], c['boundary_side'])] for c in row['candidates'][:2]]
        duplicate += a[1] == b[1] and (a[0] == b[0] or a[0] == b[0][::-1])
    return {'ambiguous_matches': len(ambiguous), 'ambiguous_top2_same_boundary_and_attribute': duplicate,
            'quality_flag_counts': dict(Counter(f for row in tables['observations'] for f in row['quality_flags'])),
            'interpretation': 'Duplicate physical boundaries can create identity ambiguity. A flag on every observation is a global risk notice, not an object-specific curb diagnosis.'}


def _evaluate(raw, output, scene, changes, sources):
    freeze = _read(output/'prediction_freeze.json')
    if any(_hash(output/name) != digest for name, digest in freeze['files_sha256'].items()):
        raise ValueError('Prediction changed after freeze')
    package = _package(raw, 'M8')
    if package.name != scene+'_M8_v1':
        raise ValueError('M8 package belongs to a different scene')
    names = ('gt_object_inventory.jsonl', 'gt_changed_objects.jsonl', 'current_gt_map.json',
             'prior_map_reference.json', 'provenance.json')
    _checked(raw, package, names, 'evaluation_only', sources)
    provenance = _read(package/'provenance.json')
    script = raw/'Python代码/m8_evaluate.py'
    if _hash(script) != provenance['script_sha256']:
        raise ValueError('M8 export script version differs from supplied provenance')
    sources.append({'path': script.relative_to(raw).as_posix(), 'sha256': _hash(script), 'role': 'export_version_only_not_executed'})
    prior = _package(raw, 'M2')/'prior_map.json'
    if provenance['prior_map_sha256'] != _hash(prior) or _read(package/'prior_map_reference.json') != _read(prior):
        raise ValueError('Evaluation GT refers to a different prior map')
    inventory, changed = _rows(package/'gt_object_inventory.jsonl'), _rows(package/'gt_changed_objects.jsonl')
    lookup = {r['element_id']: r for r in inventory}
    if len(lookup) != len(inventory) or any(r['scene_id'] != scene for r in inventory+changed):
        raise ValueError('GT inventory identities differ or are duplicated')
    if {r['element_id'] for r in changed} != {r['element_id'] for r in inventory if r['official_changed']}:
        raise ValueError('Changed-object export disagrees with inventory')
    supported = {r['element_id']: r for r in inventory if r['state_comparison_supported']}
    positives = {eid for eid, row in supported.items() if row['official_changed']}
    predicted = {r['map_element_id'] for r in changes if r['status'] == 'confirmed'}
    if predicted - supported.keys():
        raise ValueError('Confirmed object needs a declared GT correspondence before retrieval scoring')
    tp, fp, fn = len(predicted & positives), len(predicted-positives), len(positives-predicted)
    diagnostics = [{'change_id': r['change_id'], 'map_element_id': r['map_element_id'],
                    'prediction_status': r['status'], 'official_gt_changed': lookup.get(r['map_element_id'], {}).get('official_changed'),
                    'evaluation_role': 'predicted_positive' if r['status'] == 'confirmed' else 'candidate_only_not_final_positive'} for r in changes]
    evaluation = {'scope': 'all_supplied_supported_map_object_retrieval', 'inventory_objects': len(inventory),
        'objects': len(supported), 'excluded_unsupported_objects': len(inventory)-len(supported), 'changed_objects': len(positives),
        'confirmed_object_predictions': len(predicted), 'true_positives': tp, 'false_positives': fp,
        'unretrieved_changed_objects': fn, 'precision': tp/(tp+fp) if tp+fp else None,
        'recall': tp/len(positives) if positives else None, 'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
        'f1_definition': '2TP/(2TP+FP+unretrieved_changed_objects)', 'visibility_conditioned_accuracy': None,
        'candidate_diagnostics': diagnostics, 'candidate_hits': sum(r['official_gt_changed'] is True for r in diagnostics),
        'candidate_count': len(diagnostics), 'gt_type_counts': dict(Counter(t for r in changed for t in r['state_delta_types'])),
        'scope_selection': 'All supported supplied objects, independent of candidate locations, frames and correctness.',
        'limitations': ['Whole supplied map retrieval is not visibility-conditioned perception accuracy.',
                       'No output is not a factual unchanged prediction; no true-negative accuracy is reported.',
                       'Unsupported drivable-area state comparisons are excluded.',
                       'Constructed-train development scene, not an independent test.',
                       'Type counts overlap; candidate diagnostics are separate from confirmed positives.']}
    current_gt = _read(package/'current_gt_map.json')
    if current_gt['scene_id'] != scene:
        raise ValueError('Current GT map belongs to a different scene')
    return evaluation, current_gt


def run_argoverse(raw_root, output):
    """Write a new rerun directory; never overwrite source or existing output."""
    raw, output = Path(raw_root).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    if output.is_relative_to(raw):
        raise ValueError('Rerun output must be outside supplied raw data')
    sources = []
    m2 = _package(raw, 'M2')
    _checked(raw, m2, M2_FILES, 'frozen_prediction_input', sources)
    scene, tables = _identity(m2)
    original = {}
    for stage, script_name, hash_field in [('M3', 'm3_changes.py', 'source_script_sha256'), ('M4', 'm4_fuse.py', 'script_sha256')]:
        original[stage] = _package(raw, stage)
        _checked(raw, original[stage], ('provenance.json',), 'prediction_version', sources)
        script = raw/'Python代码'/script_name
        if _hash(script) != _read(original[stage]/'provenance.json')[hash_field]:
            raise ValueError(stage+' script version differs from supplied provenance')
        sources.append({'path': script.relative_to(raw).as_posix(), 'sha256': _hash(script), 'role': 'executed_script'})
    m3_provenance = _read(original['M3']/'provenance.json')
    if m3_provenance['source_m2_package'] != m2.name or any(_hash(m2/name) != digest for name, digest in m3_provenance['source_files_sha256'].items()):
        raise ValueError('M3 provenance disagrees with consumed M2 input hashes')
    output.mkdir(parents=True)
    scripts = output/'source_code'
    scripts.mkdir()
    for name in ('m3_changes.py', 'm4_fuse.py'):
        shutil.copy2(raw/'Python代码'/name, scripts/name)
    _predict(output, scripts, m2)
    freeze = {'gt_read': False, 'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
              'files_sha256': {p.relative_to(output).as_posix(): _hash(p) for stage in ('M3', 'M4') for p in sorted((output/stage).glob('*')) if p.is_file()}}
    _write(output/'prediction_freeze.json', freeze)
    changes = _rows(output/'M4/change_records.jsonl')
    evaluation, current_gt = _evaluate(raw, output, scene, changes, sources)
    equivalence = {}
    for stage, names in [('M3', ('decisions.jsonl', 'change_candidates.jsonl', 'map_checks.jsonl', 'rules.json')),
                         ('M4', ('change_records.jsonl', 'frame_evidence.jsonl', 'rules.json'))]:
        _checked(raw, original[stage], names, 'historical_comparison_only', sources)
        equivalence[stage] = {name: _equivalent((_rows if name.endswith('jsonl') else _read)(original[stage]/name),
                                               (_rows if name.endswith('jsonl') else _read)(output/stage/name)) for name in names}
    stages = {stage: {'execution': 'rerun', 'summary': _read(output/stage/'summary.json'), 'rules': _read(output/stage/'rules.json')} for stage in ('M3', 'M4')}
    m2_summary = _read(m2/'summary.json')
    stages['M2'] = {'execution': 'frozen_inputs', 'summary': m2_summary,
                    'diagnostics': _m2_diagnostics(_read(m2/'prior_map.json'), tables),
                    'limitations': ['Original RGB, LiDAR, model inference and masks were not rerun or loaded.', 'Source path references do not establish that external sensor files are present.']}
    frame_times = [int(r['timestamp_ns']) for r in tables['frames']]
    result = {'schema': 'argoverse-rerun-1.0', 'dataset_id': 'argoverse', 'scene_id': scene,
        'run_id': 'argoverse_frozen_m2_m3_m4_v1', 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'temporal_scope': {'mode': 'completed_offline_clip', 'first_observed_timestamp_ns': str(min(frame_times)),
                           'last_observed_timestamp_ns': str(max(frame_times)), 'independent_visits': 1,
                           'persistence_classification': 'not_implemented', 'prediction_availability': 'offline_not_source_timestamp'},
        'summary': {'frames': len(tables['frames']), 'views': len(tables['views']), 'observations': len(tables['observations']),
                    'projected_observations': sum(r['geometry_city'] is not None for r in tables['observations']),
                    'm3_candidates': stages['M3']['summary']['candidates'], 'm4_groups': len(changes),
                    'confirmed_changes': sum(r['status'] == 'confirmed' for r in changes), 'base_map_updates': 0, 'pending_manual_reviews': 0},
        'stages': stages, 'candidates': [dict(r, persistence_class='not_implemented', processing_status='complete') for r in changes],
        'm3_candidates': _rows(output/'M3/change_candidates.jsonl'), 'evaluation': evaluation,
        'equivalence': equivalence, 'equivalence_comparison': 'Parsed JSON; floating-point tolerance 1e-12. Line endings and numerical library rounding may differ.',
        'prediction_freeze': freeze, 'sources': sources,
        'map': {'prior': _read(m2/'prior_map.json'), 'current_gt_evaluation_only': current_gt},
        'limitations': ['Persistence classes and map editing are not implemented by supplied M3/M4.',
                       'M8 exports are reused for evaluation; original annotation export is not rerun.']}
    _write(output/'result.json', result)
    _write(output/'provenance.json', {'run_id': result['run_id'], 'generated_at_utc': result['generated_at_utc'],
                                     'sources': sources, 'prediction_freeze': freeze, 'gt_access_stage': 'after_prediction_freeze_evaluation_only'})
    return output
