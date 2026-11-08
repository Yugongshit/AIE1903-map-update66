"""Audit fixed-anchor threshold sensitivity without selecting a production policy.

Predictions for all declared variants are frozen before evaluation labels are read.
Only temporal and MAD requirements vary; matching, residuals, semantics and
write permission remain fixed. This is a diagnostic on a previously used scene,
not independent accuracy validation or a threshold-selection experiment.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

VARIANTS = {
    'baseline': {'minimum_blocks': 3, 'minimum_span_s': 1.0, 'maximum_mad_m': .35},
    'shorter_observation_window': {'minimum_blocks': 2, 'minimum_span_s': .5, 'maximum_mad_m': .35},
    'higher_residual_dispersion': {'minimum_blocks': 3, 'minimum_span_s': 1.0, 'maximum_mad_m': .5},
    'both_relaxed': {'minimum_blocks': 2, 'minimum_span_s': .5, 'maximum_mad_m': .5},
}


def status(event, policy):
    center, mad = event['median_residual_m'], event['mad_residual_m']
    if (event['temporal_block_count'] < policy['minimum_blocks']
            or event['span_s'] < policy['minimum_span_s'] or mad is None
            or mad > policy['maximum_mad_m']
            or event['prior_marking_type'] in ('NONE', 'UNKNOWN', None, '')):
        return 'insufficient_geometry'
    if abs(center) > .5 and event['same_sign_conflict_fraction'] >= .7:
        return 'supported_conflict'
    if abs(center) <= .5 and event['local_consistency_fraction'] >= .7:
        return 'consistent_with_map'
    return 'insufficient_geometry'


def analyze(prediction_file, result_file, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    data = json.loads(Path(prediction_file).read_text())
    events = data['events']
    variants = {}
    for name, policy in VARIANTS.items():
        decisions = {event['event_id']: status(event, policy) for event in events}
        if name == 'baseline':
            assert all(decisions[e['event_id']] == e['reliability']['status'] for e in events)
        variants[name] = {'parameters': policy, 'counts': dict(Counter(decisions.values())),
                          'decisions': decisions,
                          'promoted_to_local_conflict': [e['event_id'] for e in events
                              if decisions[e['event_id']] == 'supported_conflict'
                              and e['reliability']['status'] != 'supported_conflict']}
    frozen = {'variants': variants, 'prediction_source_sha256': sha256(Path(prediction_file).read_bytes()).hexdigest(),
              'evaluation_read': False, 'policy_selection': 'none; all variants declared in advance'}
    output.mkdir(parents=True)
    freeze_path = output/'threshold_predictions.json'
    freeze_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2)+'\n')
    freeze_hash = sha256(freeze_path.read_bytes()).hexdigest()
    # First access to the evaluation artifact occurs after predictions are saved.
    evaluation = json.loads(Path(result_file).read_text())['evaluation']['argoverse']
    diagnostics = {r['event_id']: r for r in evaluation['event_diagnostics']}
    for variant in variants.values():
        positive = [diagnostics[eid] for eid, outcome in variant['decisions'].items()
                    if outcome == 'supported_conflict' and diagnostics[eid]['in_geometry_scope']]
        tp = len({r['physical_boundary_id'] for r in positive if r['geometry_changed_truth']})
        fp = len({r['physical_boundary_id'] for r in positive if not r['geometry_changed_truth']})
        truth_positive = evaluation['new_geometry_metrics']['truth_positives']
        variant['fixed_painted_boundary_metrics'] = {'true_positives': tp, 'false_positives': fp,
            'false_negatives': truth_positive-tp, 'truth_positives': truth_positive,
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/truth_positive if truth_positive else None}
    assert sha256(freeze_path.read_bytes()).hexdigest() == freeze_hash
    report = {'schema': 'structural-threshold-audit-1.0', 'status': 'PASS',
              'source_predictions': str(prediction_file), 'prediction_freeze_sha256': freeze_hash,
              'variants': variants, 'production_policy_changed': False,
              'scope': evaluation['scope'], 'eligible_physical_boundaries': evaluation['eligible_boundaries'],
              'interpretation': 'Looser observation time may establish agreement, not a new conflict; a dispersion relaxation must be checked for added errors.',
              'limitations': ['Previously used development scene, not an independent test.',
                 'No ground-truth choice of threshold, reference or observation window.',
                 'No ground-truth positives in the fixed prior-painted geometry scope; recall is undefined.',
                 'These variants do not authorize structural edits or reinterpret NONE as a painted line.']}
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.predictions, args.result, args.out)
    print(json.dumps({name: {'counts': value['counts'], 'metrics': value['fixed_painted_boundary_metrics']}
                      for name, value in report['variants'].items()}, indent=2))
