"""Post-freeze evaluation of every supplied prior boundary, including NONE.

Truth is never an input to the detector. Boundary retrieval and local geometric
agreement are separate diagnostics; neither is full-map patch correctness.
"""
from collections import Counter

from .reliability_v2 import canonical_boundaries, _fixed_residual, _polyline, _sample, _distance


def metrics(predicted, truth):
    predicted, truth = set(predicted), set(truth)
    tp, fp, fn = len(predicted & truth), len(predicted - truth), len(truth - predicted)
    return {'true_positives': tp, 'false_positives': fp, 'false_negatives': fn,
            'predicted_positives': len(predicted), 'truth_positives': len(truth),
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def evaluate_all_boundaries(prior, inventory, changed_states, events, tolerance_m=.5):
    """Use a fixed all-prior scope; never exclude a boundary for paint type.

    Physical aliases must agree on geometric truth. Disagreeing aliases are
    reported as non-evaluable, never picked to obtain a true positive.
    """
    lookup = {(r['layer'], r['element_id']): r for r in inventory}
    if len(lookup) != len(inventory) or any(r['scene_id'] != prior['scene_id'] for r in inventory):
        raise ValueError('Duplicate or cross-scene truth inventory')
    current = {(r['layer'], r['element_id']): r['current_state'] for r in changed_states}
    lanes = {r['id']: r for r in prior['lane_segments']}
    boundaries = canonical_boundaries(prior)
    truth, excluded, alias_labels = {}, [], {}
    for boundary in boundaries:
        labels = []
        details = []
        for alias in boundary['aliases']:
            key = ('laneSegments', alias['map_element_id'])
            row = lookup.get(key)
            distance = row.get('differences', {}).get(alias['boundary_side'] + '_geometry_difference_m') if row else None
            label = distance > .01 if row and row['state_comparison_supported'] and distance is not None else None
            labels.append(label)
            details.append({**alias, 'geometry_changed_truth': label,
                            'object_changed_truth': row['official_changed'] if row else None,
                            'object_change_types': row.get('state_delta_types', []) if row else []})
        bid = boundary['physical_boundary_id']
        alias_labels[bid] = details
        if None in labels or len(set(labels)) != 1:
            excluded.append({'physical_boundary_id': bid, 'reason': 'GT_ALIAS_LABELS_UNRESOLVED_OR_DISAGREE',
                             'aliases': details})
        else:
            truth[bid] = labels[0]
    positives = {bid for bid, value in truth.items() if value}
    detected = [e for e in events if e.get('model_change_decision', {}).get('detected',
                     e['reliability']['status'] == 'supported_conflict')]
    predictions = {e['physical_boundary_id'] for e in detected if e['physical_boundary_id'] in truth}
    legacy_predictions = {e['physical_boundary_id'] for e in detected
                          if e['physical_boundary_id'] in truth and e['prior_marking_type'] not in ('NONE', 'UNKNOWN', None, '')}
    boundary_lookup = {b['physical_boundary_id']: b for b in boundaries}
    diagnostics, local_checks = [], []
    for event in events:
        bid = event['physical_boundary_id']
        decision = event.get('model_change_decision', {})
        positive = decision.get('detected', event['reliability']['status'] == 'supported_conflict')
        diagnostics.append({'event_id': event['event_id'], 'physical_boundary_id': bid,
                            'in_geometry_scope': bid in truth, 'geometry_changed_truth': truth.get(bid),
                            'model_detected': positive, 'reliability_status': event['reliability']['status'],
                            'aliases': alias_labels[bid],
                            'outcome': ('true_positive' if truth[bid] else 'false_positive') if positive and bid in truth
                                       else 'not_evaluable' if bid not in truth else
                                       'missed_changed_boundary' if truth[bid] else 'no_detection_on_unchanged_boundary'})
        if not positive:
            continue
        # Independent same-position checks expose a detector that chose the
        # right object but measured an unrelated local offset.
        boundary = boundary_lookup[bid]
        p, v, lengths, arc = _polyline(boundary['points'])
        s = event['fixed_anchor']['arc_length_m']
        import numpy as np
        i = min(max(int(np.searchsorted(arc, s, side='right')) - 1, 0), len(v) - 1)
        tangent = v[i] / lengths[i]
        anchor = (s, p[i] + (s - arc[i]) * tangent, tangent)
        observed_points = _sample(event['geometry_city']['coordinates'])
        checks = []
        for alias in boundary['aliases']:
            key = ('laneSegments', alias['map_element_id'])
            state = current.get(key, lanes.get(alias['map_element_id']))
            if state is None:
                checks.append({**alias, 'evaluable': False, 'reason': 'CURRENT_BOUNDARY_ABSENT'})
                continue
            line = [[q['x'], q['y']] for q in state[alias['boundary_side'] + '_lane_boundary']]
            residual = _fixed_residual(line, anchor)
            error = abs(event['median_residual_m'] - residual) if residual is not None else None
            checks.append({**alias, 'evaluable': residual is not None,
                           'truth_signed_offset_at_same_anchor_m': residual,
                           'median_offset_absolute_error_m': error,
                           'within_engineering_tolerance': error <= tolerance_m if error is not None else None,
                           'representative_mean_distance_to_current_m': float(_distance(observed_points, line).mean())})
        local_checks.append({'event_id': event['event_id'], 'physical_boundary_id': bid,
                             'observed_median_offset_m': event['median_residual_m'],
                             'representative_mean_distance_to_prior_m': float(_distance(observed_points, boundary['points']).mean()),
                             'aliases': checks})
    evaluable_local = [r for r in local_checks if r['aliases'] and all(a['evaluable'] for a in r['aliases'])]
    agreeing_local = [r for r in evaluable_local if all(a['within_engineering_tolerance'] for a in r['aliases'])]
    return {'scope': 'all_prior_physical_boundaries_including_unpainted',
            'physical_boundaries': len(boundaries), 'eligible_boundaries': len(truth),
            'truth_positives': len(positives), 'excluded_boundaries': len(excluded),
            'excluded_details': excluded,
            'prior_marking_counts': dict(Counter(b['marking_type'] for b in boundaries)),
            'metrics': metrics(predictions, positives),
            'baseline_none_veto_metrics': metrics(legacy_predictions, positives),
            'baseline_description': 'Same frozen observations and numeric policy with the former NONE/UNKNOWN veto; evaluated on the same full scope.',
            'event_diagnostics': diagnostics, 'local_geometry_checks': local_checks,
            'metric_interpretation': 'Changed-boundary identity retrieval only; a true hit may still predict incorrect local geometry.',
            'local_geometry_summary': {
                'model_detections': len(local_checks), 'evaluable_detections': len(evaluable_local),
                'within_tolerance_detections': len(agreeing_local), 'tolerance_m': tolerance_m,
                'scope': 'All aliases must have a same-anchor truth offset and absolute median-offset error within tolerance; not full-patch correctness.',
                'event_ids_within_tolerance': [r['event_id'] for r in agreeing_local]},
            'local_geometry_tolerance_m': tolerance_m,
            'local_geometry_check_scope': 'Same-anchor and representative-curve agreement, evaluated after prediction freeze; not whole-feature replacement accuracy.',
            'limitations': ['Boundary retrieval is not the 98-object all-change task.',
                           'New objects, crossing changes, marking attributes, lane use and connectivity need additional observation branches.',
                           'Unobserved boundaries remain in the fixed recall denominator.',
                           'Constructed-train development scene; not independent test accuracy.',
                           'A changed boundary identity alone does not establish that the predicted geometry or edit is correct.']}
