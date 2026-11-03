"""Evidence dashboard data and denominator-aware controlled evaluation.

Real-scene observations and controlled perturbations remain distinct datasets.
An abstention is never counted as a true negative or silently dropped from the
decision-coverage and all-positive recall denominators.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from math import isclose, isfinite
from pathlib import Path

from .delivery import read_csv, read_json
from .reliability import integrate_m3
from .output import _write_files


POSITIVE = 'SUSPECTED_MAP_CHANGE'
NEGATIVE = 'NO_SIGNIFICANT_MISMATCH'
METHODS = ['geometric_threshold', 'normalized_test', 'quality_gated_repeated_evidence']
ABSTAIN = {'INSUFFICIENT_EVIDENCE', 'NEEDS_FURTHER_VERIFICATION'}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def controlled_metrics(rows, method):
    if method not in METHODS:
        raise ValueError('Unknown evaluation method')
    truth = []
    identifiers = set()
    for row in rows:
        if row['case_id'] in identifiers:
            raise ValueError('Duplicate controlled case')
        identifiers.add(row['case_id'])
        if row['data_type'] != 'CONTROLLED_PERTURBATION':
            raise ValueError('Only labeled controlled perturbations belong in this evaluation')
        if row['scripted_change'] not in {'True', 'False'}:
            raise ValueError('Missing or invalid controlled truth')
        if row[method] not in {POSITIVE, NEGATIVE} | ABSTAIN:
            raise ValueError('Unknown decision')
        truth.append(row['scripted_change'] == 'True')
    predictions = [row[method] for row in rows]
    tp = sum(t and p == POSITIVE for t, p in zip(truth, predictions))
    fp = sum(not t and p == POSITIVE for t, p in zip(truth, predictions))
    tn = sum(not t and p == NEGATIVE for t, p in zip(truth, predictions))
    fn = sum(t and p == NEGATIVE for t, p in zip(truth, predictions))
    resolved = tp + fp + tn + fn
    return dict(method=method, cases=len(rows), resolved=resolved, tp=tp, fp=fp, tn=tn, fn=fn,
                insufficient=predictions.count('INSUFFICIENT_EVIDENCE'),
                needs_review=predictions.count('NEEDS_FURTHER_VERIFICATION'),
                precision_resolved=ratio(tp, tp+fp), recall_resolved=ratio(tp, tp+fn),
                f1_resolved=ratio(2*tp, 2*tp+fp+fn), fpr_resolved=ratio(fp, fp+tn),
                coverage=ratio(resolved, len(rows)), recall_all_positives=ratio(tp, sum(truth)),
                positives=sum(truth), negatives=len(rows)-sum(truth),
                abstained_positives=sum(t and p in ABSTAIN for t, p in zip(truth, predictions)),
                abstained_negatives=sum(not t and p in ABSTAIN for t, p in zip(truth, predictions)))


def evaluate_controlled(rows, comparisons):
    strata = {'all': rows}
    for level in ('full', 'intermittent', 'low'):
        strata[level] = [r for r in rows if r['scripted_coverage'] == level]
    for noise in (0., .20):
        strata[f'single_frame_error_{noise:.2f}m'] = [r for r in rows if float(r['scripted_single_frame_error_m']) == noise]
    expected = {(r['stratum'], r['method']): r for r in comparisons}
    if len(expected) != len(comparisons):
        raise ValueError('Duplicate comparison row')
    results = {}
    for name, subset in strata.items():
        results[name] = []
        for method in METHODS:
            calculated = controlled_metrics(subset, method)
            original = expected.get((name, method))
            if original is None:
                raise ValueError(f'Missing supplied comparison: {name}/{method}')
            for field, value in original.items():
                if field in {'stratum', 'method'}:
                    continue
                actual = calculated[field]
                same = actual is None and value == ''
                if actual is not None and value != '':
                    number = float(value)
                    same = isfinite(number) and isclose(actual, number, rel_tol=1e-10, abs_tol=1e-10)
                if not same:
                    raise ValueError(f'Comparison does not reproduce: {name}/{method}/{field}')
            results[name].append(calculated)
    if len(expected) != len(strata)*len(METHODS):
        raise ValueError('Unexpected comparison strata')
    return {'dataset_type': 'controlled_perturbation', 'cases': len(rows),
            'scope': '126 dependent perturbation variants derived from three windows in one scene; not an independent real test set.',
            'unchanged_label_scope': 'No injected offset, not independently verified unchanged-road truth.',
            'comparison_verified': True, 'strata': results}


def dashboard_data(scene, controlled):
    if scene.get('schema_version') != 'scene-m3-maintenance-1.0':
        raise ValueError('An integrated, validated real M3 scene is required')
    candidates = []
    for c in scene['candidates']:
        candidates.append({k: deepcopy(c[k]) for k in (
            'candidate_id', 'change_type', 'map_element_id', 'frame_window', 'window_timestamp_ns',
            'evidence_score', 'observed_frames', 'support_frames', 'valid_visibility_frames',
            'frame_indices', 'reliability', 'reasons', 'flags', 'geometry')})
        candidates[-1].update(track_id=c['source_context']['track_id'],
                              median_difference=float(c['raw']['median_difference']),
                              difference_unit=c['raw']['difference_unit'], evidence_level=c['raw']['evidence_level'])
    frame_evidence = []
    for row in scene['frame_evidence']:
        frame_evidence.append(dict(candidate_id=row['candidate_id'], frame=int(row['frame']),
            supports=row['supports_hypothesis']=='True', usable=row['observation_usable']=='True',
            value=float(row['difference_value']) if row['difference_value'] else None,
            unit=row['difference_unit'], source_file=row['source_file']))
    public_map = {k: deepcopy(scene['reference_map'][k]) for k in (
        'map_id', 'version', 'source_version', 'source_sha256', 'map_role', 'bounds_xyxy', 'coordinate_reference')}
    public_map['elements'] = [{k: deepcopy(e[k]) for k in ('element_id', 'layer', 'geometry_city')}
                              for e in scene['reference_map']['elements']]
    labels = deepcopy(scene['summary']['labeled_rows'])
    data = {'schema_version': 'evidence-dashboard-1.0', 'real': {
        'scene_id': scene['scene_id'], 'visit_id': scene['visit_id'], 'map': public_map,
        'frames': deepcopy(scene['replay']), 'candidates': candidates,
        'frame_evidence': frame_evidence, 'behaviors': deepcopy(scene['behaviors']),
        'associations': deepcopy(scene['associations']),
        'association_frames': deepcopy(scene['association_frame_evidence']),
        'm3': deepcopy(scene['m3']), 'maintenance': deepcopy(scene['maintenance']),
        'source_files': deepcopy(scene['source_files']),
        'summary': deepcopy(scene['summary']),
        'accuracy': {'precision': None, 'recall': None, 'f1': None, 'geometry_error': None,
                     'reason': 'No independent real-world labels; candidate scores and consistency checks are not accuracy.',
                     'labeled_rows': labels},
        'checks': {'source_checks': len(scene['raw_validation']['checks']),
                   'm3_checks': len(scene['m3']['validation']['checks'])}},
        'controlled': deepcopy(controlled)}
    data['real']['summary']['evidence_levels'] = dict(Counter(c['evidence_level'] for c in candidates))
    return data


def build_dashboard(raw_root, delivery_root, m3_root, output):
    raw, m3 = Path(raw_root), Path(m3_root)
    handoff = m3/'results/real_scene_handoff.json'
    cases = m3/'results/controlled_case_decisions.csv'
    comparisons = m3/'results/controlled_comparison.csv'
    scene = integrate_m3(raw, delivery_root, handoff)
    evaluation = evaluate_controlled(read_csv(cases), read_csv(comparisons))
    payload = dashboard_data(scene, evaluation)
    payload['provenance'] = [{ 'file': p.name, 'sha256': sha256(p.read_bytes()).hexdigest()}
                             for p in (handoff, cases, comparisons)]
    files = {'evaluation': {'real': payload['real']['accuracy'], 'controlled': evaluation},
             'provenance': payload['provenance']}
    return _write_files(files, 'evidence_dashboard.html', payload, output)
