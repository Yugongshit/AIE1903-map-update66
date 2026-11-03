"""Build a versioned scene-0980 handoff and a separate controlled perturbation experiment."""

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from itertools import product
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from M3_scene0980_error_analysis import build_samples, read_table, summarize
from M3_scene0980_anomaly_analysis import analyze
from M3_scene0980_observation_quality import geometry_rows
from M3_scene0980_reliability_synthesis import decide, read_csv, synthesize
from coverage_from_lengths import LENGTH_FIELDS, add_coverage_rates


NEGATIVE = 'NO_SIGNIFICANT_MISMATCH'
POSITIVE = 'SUSPECTED_MAP_CHANGE'
METHODS = ('geometric_threshold', 'normalized_test', 'quality_gated_repeated_evidence')
WINDOW_SIZE = 5
MIN_COVERAGE = .70
MIN_VALID_FRAMES = 3
MIN_SUPPORT_RATIO = .70
MIN_CONSECUTIVE_SUPPORT = 3
# Each value is the number of valid equal-length bins of a 20-bin target.
MASKS = {
    'full': (20, 20, 20, 20, 20),
    'intermittent': (20, 20, 8, 20, 20),
    'low': (8, 8, 8, 8, 8),
}
OFFSETS = (-.30, -.16, -.08, 0., .08, .16, .30)


def write_csv(path, rows):
    fields = list(dict.fromkeys(field for row in rows for field in row))
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                    encoding='utf-8')


def map_identity(path, vertices):
    raw = path.read_bytes()
    base = json.loads(raw)
    lines = {row['token']: row['node_tokens'] for row in base['line']}
    nodes = {row['token']: (row['x'], row['y']) for row in base['node']}
    groups = defaultdict(list)
    for row in vertices:
        groups[row['layer'], row['map_token'], row['part_id']].append(row)
    for (layer, token, part), rows in groups.items():
        feature, = [row for row in base[layer] if row['token'] == token]
        source = [nodes[node] for node in lines[feature['line_token']]]
        exported = [(float(r['global_x_m']), float(r['global_y_m']))
                    for r in sorted(rows, key=lambda r: int(r['vertex_id']))]
        if exported != source:
            raise ValueError(f'Base map geometry does not match export {token}, part {part}')
    return dict(file=path.name, version=str(base['version']), sha256=hashlib.sha256(raw).hexdigest(),
                verified_divider_parts=len(groups), verified_vertices=len(vertices),
                role='supplied base map; historical chronology unknown')


def add_provenance(result, map_info, frames):
    frame_by_id = {int(r['frame_index']): r for r in frames}
    result['source_map'] = map_info
    result['evaluable_coverage'] = {
        'units': len(result['cases']),
        'resolved_units': sum(row['final_decision'] in (POSITIVE, NEGATIVE) for row in result['cases']),
        'scope': 'selected lane-marking review units in scene-0980',
    }
    for row in result['cases']:
        row.update(map_version=map_info['version'], map_sha256=map_info['sha256'],
                   first_timestamp_us=int(frame_by_id[row['first_frame']]['timestamp_us']),
                   last_timestamp_us=int(frame_by_id[row['last_frame']]['timestamp_us']),
                   valid_visibility_frames=None, truth=None)
    for row in result['matched_frame_evidence']:
        row.update(map_version=map_info['version'], map_sha256=map_info['sha256'],
                   timestamp_us=int(frame_by_id[row['frame']]['timestamp_us']))


def add_expected_outputs(handoff, samples, reference, qualities, vertices, frames):
    _, parameters, covariances = summarize(samples)
    lateral = [p for p in parameters if p['metric'] == 'lateral_signed_m']
    center = reference['reference_median_m']
    scale = reference['robust_scale_m']
    reference_group = reference['reference_track']
    ref = next(p for p in lateral if p['group'] == reference_group)
    ref_scores = np.array([abs((r['lateral_signed_m'] - center) / scale)
                           for r in samples if r['track'] == reference_group])
    for row in covariances:
        matrix = [[row['xx_m2'], row['xy_m2']], [row['yx_m2'], row['yy_m2']]]
        eigenvalues = np.linalg.eigvalsh(matrix)
        row['minor_variance_share'] = float(eigenvalues[0] / eigenvalues.sum()) if eigenvalues.sum() > 0 else None
    for row in handoff['matched_frame_evidence']:
        residual = row['lateral_residual_m']
        row.update(
            mahalanobis_1d=abs(residual - ref['mean_m']) / ref['sample_sd_m'],
            empirical_tail_score=float((1 + np.count_nonzero(ref_scores >= row['anomaly_score'])) / (len(ref_scores) + 1)),
            p_value=None,
        )
    handoff['statistical_scope'] = {
        'reference_track': reference_group,
        'reference_n': len(ref_scores),
        'calibration': 'within one drive; reference fit and ranked in sample',
        'empirical_tail_score': '(1 + count(reference |z| >= target |z|)) / (n + 1); descriptive only',
        'p_value_status': 'unavailable; no independent, validated null calibration',
        'mahalanobis_1d': 'absolute signed residual centered and scaled by reference mean and sample SD; diagnostic only',
        'mahalanobis_2d_status': 'unavailable; XY components project mostly onto the same lateral direction',
    }
    target_window = handoff['cases'][0]
    local_vertices = [r for r in vertices if r['map_token'] == target_window['map_token']
                      and int(r['part_id']) == target_window['map_part']]
    xs = [float(r['global_x_m']) for r in local_vertices]
    ys = [float(r['global_y_m']) for r in local_vertices]
    target_window['bbox_xy_m'] = [min(xs), min(ys), max(xs), max(ys)]
    for row in handoff['cases']:
        row['bbox_basis'] = 'reference_divider_footprint' if row['map_token'] else 'observed_candidate_footprint'
    quality = [dict(r, field_of_view_ratio=None, occlusion_ratio=None, effective_coverage=None) for r in qualities]
    timestamps = {int(r['frame_index']): int(r['timestamp_us']) for r in frames}
    coverage_inputs = [
        dict(record_id=row['record_id'], frame=frame, timestamp_us=timestamps[frame],
             map_version=row['map_version'], map_token=row['map_token'], **dict.fromkeys(LENGTH_FIELDS, ''),
             evidence_reference='')
        for row in handoff['cases'] for frame in range(row['first_frame'], row['last_frame'] + 1)
    ]
    return dict(
        error_parameters=lateral, xy_covariances=covariances, observation_quality=quality,
        quality_totals=dict(used_points=sum(r['geometry_points'] for r in quality),
                            input_points=sum(r['input_points'] for r in quality)),
        coverage_inputs=coverage_inputs, coverage_results=[add_coverage_rates(r) for r in coverage_inputs],
        review_cases=handoff['cases'], mismatch_frames=handoff['matched_frame_evidence'],
        reference=reference, statistical_scope=handoff['statistical_scope'],
    )


def consecutive_count(flags):
    longest = running = 0
    for flag in flags:
        running = running + 1 if flag else 0
        longest = max(longest, running)
    return longest


def controlled_cases(target, reference, geometric_limit):
    if len(target) % WINDOW_SIZE or any(b['frame'] != a['frame'] + 1 for a, b in zip(target, target[1:])):
        raise ValueError('Controlled test expects complete, consecutive target windows.')
    center = reference['reference_median_m']
    scale = reference['robust_scale_m']
    limit = reference['threshold']
    windows = []
    observations = []
    for start in range(0, len(target), WINDOW_SIZE):
        window = target[start:start + WINDOW_SIZE]
        for (mask_name, bins), single_error, offset in product(MASKS.items(), (0., .20), OFFSETS):
            case_id = f"W{start // WINDOW_SIZE + 1}_{mask_name}_{single_error:.2f}_{offset:+.2f}"
            coverage = [count / 20 for count in bins]
            valid = [c >= MIN_COVERAGE for c in coverage]
            residuals = [row['lateral_residual_m'] - offset + (single_error if i == 2 else 0.)
                         for i, row in enumerate(window)]
            scores = [((r - center) / scale) ** 2 for r in residuals]
            above = [score > limit ** 2 for score in scores]
            geometry_flags = [abs(r) > geometric_limit for r in residuals]
            support = [is_valid and flag for is_valid, flag in zip(valid, above)]
            n_valid = sum(valid)
            n_support = sum(support)
            support_ratio = n_support / n_valid if n_valid else None
            stable = (n_valid >= MIN_VALID_FRAMES and support_ratio >= MIN_SUPPORT_RATIO
                      and consecutive_count(support) >= MIN_CONSECUTIVE_SUPPORT)
            evidence = 'STABLE_SUPPORT' if stable else 'UNRESOLVED' if n_support else 'NO_PERSISTENT_ANOMALY'
            gated = decide('SUFFICIENT' if n_valid >= MIN_VALID_FRAMES else 'INSUFFICIENT',
                           bool(n_support), evidence)
            windows.append(dict(case_id=case_id, data_type='CONTROLLED_PERTURBATION', first_frame=window[0]['frame'],
                                last_frame=window[-1]['frame'], scripted_map_offset_m=offset, scripted_change=bool(offset),
                                scripted_coverage=mask_name, scripted_single_frame_error_m=single_error,
                                valid_frames=n_valid, support_frames=n_support, support_ratio=support_ratio,
                                max_consecutive_support=consecutive_count(support),
                                geometric_threshold=POSITIVE if any(geometry_flags) else NEGATIVE,
                                normalized_test=POSITIVE if any(above) else NEGATIVE,
                                quality_gated_repeated_evidence=gated))
            for row, c, e, score, valid_frame, raw_flag, test_flag in zip(
                    window, coverage, residuals, scores, valid, geometry_flags, above):
                observations.append(dict(case_id=case_id, frame=row['frame'], map_token=row['map_token'],
                                         real_local_residual_m=row['lateral_residual_m'], modified_residual_m=e,
                                         scripted_effective_coverage=c, passes_coverage=valid_frame, T=score,
                                         geometric_flag=raw_flag, normalized_flag=test_flag))
    return windows, observations


def ratio(a, b):
    return a / b if b else None


def metrics(cases, method, stratum):
    resolved = [row for row in cases if row[method] in (POSITIVE, NEGATIVE)]
    tp = sum(row['scripted_change'] and row[method] == POSITIVE for row in resolved)
    fp = sum(not row['scripted_change'] and row[method] == POSITIVE for row in resolved)
    tn = sum(not row['scripted_change'] and row[method] == NEGATIVE for row in resolved)
    fn = sum(row['scripted_change'] and row[method] == NEGATIVE for row in resolved)
    positives = sum(row['scripted_change'] for row in cases)
    return dict(stratum=stratum, method=method, cases=len(cases), resolved=len(resolved), tp=tp, fp=fp, tn=tn, fn=fn,
                insufficient=sum(row[method] == 'INSUFFICIENT_EVIDENCE' for row in cases),
                needs_review=sum(row[method] == 'NEEDS_FURTHER_VERIFICATION' for row in cases),
                precision_resolved=ratio(tp, tp + fp), recall_resolved=ratio(tp, tp + fn),
                f1_resolved=ratio(2 * tp, 2 * tp + fp + fn), fpr_resolved=ratio(fp, fp + tn),
                coverage=ratio(len(resolved), len(cases)), recall_all_positives=ratio(tp, positives))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_zip', type=Path)
    parser.add_argument('base_map', type=Path)
    parser.add_argument('scored_candidates', type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('results'))
    args = parser.parse_args()
    with ZipFile(args.source_zip) as archive:
        frames = read_table(archive, 'frames.csv')
        vertices = read_table(archive, 'scene0980_hdmap_divider_vertices.csv')
        lines = read_table(archive, 'scene0980_sensor_hdmap_line_metrics.csv')
        points = read_table(archive, 'scene0980_sensor_hdmap_point_distances.csv')
        candidates = read_table(archive, 'scene0980_discrepancy_candidates_v1.csv')
    identity = map_identity(args.base_map, vertices)
    samples, _, _ = build_samples(lines, points, vertices, frames)
    anomalies, reference = analyze(samples)
    qualities = geometry_rows(samples, lines)
    handoff = synthesize(anomalies, qualities, lines, candidates, read_csv(args.scored_candidates))
    add_provenance(handoff, identity, frames)
    for frame in handoff['matched_frame_evidence']:
        frame['T'] = frame['anomaly_score'] ** 2
        frame['T_threshold'] = frame['anomaly_threshold'] ** 2
    for case in handoff['cases']:
        score = case['max_anomaly_score']
        case['max_T'] = None if score is None else score ** 2
    report = add_expected_outputs(handoff, samples, reference, qualities, vertices, frames)
    geometric_limit = float(np.quantile([abs(r['lateral_signed_m']) for r in anomalies if r['status'] == 'REFERENCE'],
                                        reference['empirical_quantile']))
    target = handoff['matched_frame_evidence']
    target_part = [r for r in vertices if r['map_token'] == target[0]['map_token']
                   and int(r['part_id']) == target[0]['map_part']]
    if len(target_part) != 2:
        raise ValueError('Analytical shift assumes a straight map divider.')
    target_part.sort(key=lambda r: int(r['vertex_id']))
    a, b = [(float(r['global_x_m']), float(r['global_y_m'])) for r in target_part]
    yaw_by_frame = {int(r['frame_index']): math.radians(float(r['ego_yaw_deg'])) for r in frames}
    along = [(b[0] - a[0]) * math.cos(yaw_by_frame[row['frame']])
             + (b[1] - a[1]) * math.sin(yaw_by_frame[row['frame']]) for row in target]
    if min(along) <= 0 <= max(along):
        raise ValueError('Lane-normal orientation must stay consistent across target frames.')
    cases, observations = controlled_cases(target, reference, geometric_limit)
    strata = {'all': cases}
    for name in MASKS:
        strata[name] = [r for r in cases if r['scripted_coverage'] == name]
    for error in (0., .20):
        strata[f'single_frame_error_{error:.2f}m'] = [r for r in cases if r['scripted_single_frame_error_m'] == error]
    comparison = [metrics(rows, method, name) for name, rows in strata.items() for method in METHODS]

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'real_scene_handoff.json', handoff)
    write_csv(out / 'real_scene_decisions.csv', handoff['cases'])
    write_csv(out / 'real_scene_matched_frames.csv', target)
    write_csv(out / 'real_scene_error_parameters.csv', report['error_parameters'])
    write_csv(out / 'real_scene_error_covariance.csv', report['xy_covariances'])
    write_csv(out / 'real_scene_observation_quality.csv', report['observation_quality'])
    write_csv(out / 'coverage_input_template.csv', report['coverage_inputs'])
    write_csv(out / 'real_scene_coverage_status.csv', report['coverage_results'])
    write_json(out / 'expected_outputs_report_data.json', report)
    write_csv(out / 'controlled_case_decisions.csv', cases)
    write_csv(out / 'controlled_frame_evidence.csv', observations)
    write_csv(out / 'controlled_comparison.csv', comparison)
    write_json(out / 'experiment_protocol.json', dict(
        original_scene=handoff['scene'], split='one development scene; derived cases are dependent',
        geometric_limit_m=geometric_limit, normalized_T_limit=reference['threshold'] ** 2,
        normalized_reference=reference, window_size=WINDOW_SIZE,
        minimum_coverage=MIN_COVERAGE, minimum_valid_frames=MIN_VALID_FRAMES,
        minimum_support_ratio=MIN_SUPPORT_RATIO, minimum_consecutive_support=MIN_CONSECUTIVE_SUPPORT,
        map_offsets_m=OFFSETS, coverage_valid_bins_out_of_20=MASKS, single_frame_errors_m=[0., .20]))
    print(json.dumps(dict(real_scene=handoff['summary'], controlled_cases=len(cases),
                          controlled_comparison=comparison[:3]), indent=2))


if __name__ == '__main__':
    main()
