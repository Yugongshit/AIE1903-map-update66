"""Consistency validation and independent-label evaluation (no invented GT)."""
import json
import math
import contextlib
import io
from collections import Counter
from pathlib import Path
from stage3 import ROOT, read_csv, write_csv
from stage6 import segment_distance, polygon_distance
from stage5 import main as classify_behaviors


def measure(predictions, labels, classes, abstain='uncertain'):
    labeled = [(predictions[r['id']], r['truth']) for r in labels if r['truth']]
    if not labeled:
        return dict(status='AWAITING_INDEPENDENT_LABELS', labeled=0, metrics=None)
    table = {}
    for name in classes:
        tp = sum(p == name and y == name for p, y in labeled)
        fp = sum(p == name and y != name for p, y in labeled)
        fn = sum(p != name and y == name for p, y in labeled)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        table[name] = dict(tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1)
    return dict(status='LABELED_SUBSET_ONLY', labeled=len(labeled),
                abstention_rate=sum(p == abstain for p, _ in labeled) / len(labeled),
                accuracy=sum(p == y for p, y in labeled) / len(labeled), per_class=table,
                confusion=dict(Counter(f'{y} -> {p}' for p, y in labeled)))


def main():
    output = ROOT / 'outputs/stage7'
    labels_dir = ROOT / 'evaluation_labels'
    output.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(exist_ok=True)
    scored = read_csv(ROOT / 'outputs/stage3/scored_candidates.csv')
    features = read_csv(ROOT / 'outputs/stage5/vehicle_features.csv')
    behaviors = read_csv(ROOT / 'outputs/stage5/behavior_events.csv')
    associations = read_csv(ROOT / 'outputs/stage6/associations.csv')
    frames = read_csv(ROOT / 'outputs/stage6/association_frame_evidence.csv')
    errors, checks = [], []

    def check(name, condition):
        checks.append(dict(check=name, passed=bool(condition)))
        if not condition:
            errors.append(name)

    check('all_29_source_candidates_preserved', len(scored) == 29 and len({r['candidate_id'] for r in scored}) == 29)
    check('score_bounds', all(0 <= float(r['evidence_score']) <= 1 for r in scored))
    check('behavior_samples_cover_vehicle_features', sum(int(r['samples']) for r in behaviors) == len(features))
    check('vehicle_track_frame_unique', len({(r['track_id'], r['frame']) for r in features}) == len(features))
    check('association_pairs_unique', len({(r['candidate_id'], r['behavior_id']) for r in associations}) == len(associations))
    check('distances_finite_nonnegative', all(math.isfinite(float(r['distance_m'])) and float(r['distance_m']) >= 0 for r in frames))
    check('geometry_segment_interior', abs(segment_distance((1, 2), (0, 0), (2, 0)) - 2) < 1e-9)
    check('geometry_segment_endpoint', abs(segment_distance((3, 0), (0, 0), (2, 0)) - 1) < 1e-9)
    check('geometry_degenerate_segment', abs(segment_distance((3, 4), (0, 0), (0, 0)) - 5) < 1e-9)
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    check('geometry_polygon_inside', polygon_distance((1, 1), square) == 0)
    check('geometry_polygon_outside', abs(polygon_distance((3, 1), square) - 1) < 1e-9)
    # Known trajectories verify behavior rules without treating these as real-data GT.
    with contextlib.nullcontext(output / 'synthetic_rule_checks') as tmp:
        base = Path(tmp)
        base.mkdir(exist_ok=True)
        synthetic = []
        for name in ('straight', 'stationary', 'turn', 'u_turn', 'gap'):
            for i in range(21):
                t = i * 0.5
                if name == 'stationary':
                    x, y = 0, 0
                elif name in ('turn', 'u_turn'):
                    angle = i / 20 * (math.pi / 2 if name == 'turn' else math.pi)
                    radius = 10 if name == 'turn' else 5
                    x, y = radius * math.cos(angle), radius * math.sin(angle)
                else:
                    x, y = 2 * t, 0
                if name == 'gap' and 5 <= i <= 15:
                    continue
                synthetic.append(dict(instance_token=name, category='vehicle.car', time_s=t,
                                      frame_index=i, x_global_m=x, y_global_m=y))
        write_csv(base / 'track_points.csv', synthetic)
        with contextlib.redirect_stdout(io.StringIO()):
            classify_behaviors(base, base / 'result')
        synthetic_results = read_csv(base / 'result/behavior_events.csv')
        for tid, expected in [('straight', 'straight'), ('stationary', 'stationary_presence'),
                              ('turn', 'turn'), ('u_turn', 'u_turn_candidate')]:
            rows = [r for r in synthetic_results if r['track_id'] == tid]
            check(f'synthetic_behavior_{tid}', len(rows) == 1 and rows[0]['behavior'] == expected)
        check('synthetic_track_gap_not_bridged', len([r for r in synthetic_results if r['track_id'] == 'gap']) == 2)
    score_by_id = {r['candidate_id']: r for r in scored}
    check('associated_has_sufficient_evidence', all(
        int(r['consecutive_near_samples']) >= 3 and float(r['consecutive_near_duration_s']) >= 1
        and score_by_id[r['candidate_id']]['evidence_level'] != 'INSUFFICIENT_EVIDENCE'
        and r['behavior'] not in ('uncertain', 'stationary_presence')
        for r in associations if r['association'] == 'associated'))
    check('not_associated_has_far_observations', all(float(r['minimum_distance_m']) > 5 and int(r['simultaneous_samples']) >= 3
                                                    for r in associations if r['association'] == 'not_associated'))
    specifications = {
        'candidate_evidence': ({r['candidate_id']: 'supported' if r['evidence_level'] != 'INSUFFICIENT_EVIDENCE' else 'uncertain' for r in scored},
                               ['supported', 'unsupported'],
                               'Independent sensor/map evidence adjudication; not historical map-change truth.'),
        'behavior': ({r['behavior_id']: r['behavior'] for r in behaviors},
                     ['straight', 'stop', 'turn', 'u_turn_candidate', 'detour_candidate', 'stationary_presence'],
                     'Independently label the exact event interval from continuous observations.'),
        'association': ({r['candidate_id'] + '|' + r['behavior_id']: r['association'] for r in associations},
                        ['associated', 'not_associated'], 'Independent space/time association, without a causal claim.'),
    }
    metrics = {}
    for name, (predictions, classes, definition) in specifications.items():
        path = labels_dir / f'{name}.csv'
        # Never overwrite human labels. Predictions omitted to reduce anchoring.
        if not path.exists():
            write_csv(path, [dict(id=k, truth='', annotator='', evidence_reference='', notes='') for k in predictions])
        labels = read_csv(path)
        check(f'{name}_labels_unique', len({r['id'] for r in labels}) == len(labels))
        check(f'{name}_labels_known_ids', all(r['id'] in predictions for r in labels))
        check(f'{name}_label_vocabulary', all(r['truth'] in classes + ['uncertain', ''] for r in labels))
        check(f'{name}_labels_provenance', all(not r['truth'] or (r['annotator'] and r['evidence_reference']) for r in labels))
        valid_labels = [r for r in labels if r['id'] in predictions and r['truth'] in classes and r['annotator'] and r['evidence_reference']]
        metrics[name] = measure(predictions, valid_labels, classes)
        metrics[name]['definition'] = definition
        metrics[name]['unknown_or_unadjudicated_labels'] = sum(r['truth'] in ('', 'uncertain') for r in labels)
    report = dict(stage=7, consistency_validation='PASS' if not errors else 'FAIL',
                  errors=errors, checks=checks, independent_evaluation=metrics,
                  historical_map_change_evaluation=dict(status='NOT_AVAILABLE', reason='No independently verified prior/current change GT or held-out scenes supplied.'),
                  protocol='Preset rules; scene-0980 is a development scene. Do not report these outputs as held-out accuracy.')
    (output / 'evaluation_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(consistency_validation=report['consistency_validation'], checks=len(checks), errors=errors,
                          independent_evaluation={k: v['status'] for k, v in metrics.items()}), indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
