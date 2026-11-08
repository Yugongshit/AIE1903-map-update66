"""Exact-window local divider correspondence without change labels.

A displacement gate searches for a geometric reference, not a verified edit
target. Direction is measured on the clipped reference span, so an unrelated
far end of a curved map line cannot veto a locally parallel observation.
"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json

from .geometry import validate_geometry
from .reliability_v2 import ReliabilityPolicy, _anchors, _event, _fixed_residual, _sample, _distance


def divider_references(elements):
    grouped = {}
    for element in elements:
        if element.get('layer') not in {'lane_divider', 'road_divider'}:
            continue
        geometry = validate_geometry(element['geometry_city'], line_only=True)
        points = tuple(map(tuple, geometry['coordinates']))
        points = tuple(p for i, p in enumerate(points) if i == 0 or p != points[i-1])
        points = min(points, points[::-1])
        key = sha256(json.dumps(points, separators=(',', ':')).encode()).hexdigest()[:20]
        if key not in grouped:
            grouped[key] = {'boundary_id': 'source-divider:'+key, 'points': points,
                            'marking_type': 'SOURCE_DIVIDER_GEOMETRY', 'aliases': []}
        grouped[key]['aliases'].append({'map_element_id': element['element_id'], 'layer': element['layer']})
    for reference in grouped.values():
        reference['aliases'].sort(key=lambda a: a['map_element_id'])
    return sorted(grouped.values(), key=lambda r: r['boundary_id'])


def clipped_metrics(observed, reference):
    import numpy as np
    points, line = _sample(observed), np.asarray(reference, dtype=float)
    vector = np.diff(line, axis=0)
    lengths = np.linalg.norm(vector, axis=1)
    arc = np.r_[0., np.cumsum(lengths)]
    factors = np.clip(((points[:, None]-line[:-1])*vector).sum(2)/np.maximum(lengths**2, 1e-12), 0, 1)
    distances = np.linalg.norm(points[:, None]-(line[:-1]+factors[:, :, None]*vector), axis=2)
    closest = distances.argmin(1)
    along = arc[closest]+factors[np.arange(len(points)), closest]*lengths[closest]
    start, end = float(along.min()), float(along.max())
    if end-start <= 1e-8:
        return {'distance_m': None, 'local_direction_deg': None, 'overlap_m': 0.}
    positions = np.unique(np.r_[start, arc[(arc > start) & (arc < end)], end])
    clipped = np.column_stack([np.interp(positions, arc, line[:, i]) for i in (0, 1)])
    a, b = np.asarray(observed[-1])-np.asarray(observed[0]), clipped[-1]-clipped[0]
    denominator = np.linalg.norm(a)*np.linalg.norm(b)
    angle = float(np.degrees(np.arccos(np.clip(abs(a@b)/denominator, 0, 1)))) if denominator > 1e-8 else None
    distance = .5*(float(distances.min(1).mean())+float(_distance(_sample(clipped), observed).mean()))
    return {'distance_m': distance, 'local_direction_deg': angle, 'overlap_m': end-start}


def match_local_curve(geometry, references, policy=None):
    policy = policy or ReliabilityPolicy()
    observed = validate_geometry(geometry, line_only=True)['coordinates']
    options = [dict(clipped_metrics(observed, r['points']), boundary_id=r['boundary_id'],
                    map_aliases=deepcopy(r['aliases'])) for r in references]
    options.sort(key=lambda r: (r['distance_m'] if r['distance_m'] is not None else float('inf'), r['boundary_id']))
    nearby = [r for r in options if r['distance_m'] is not None and r['distance_m'] <= policy.candidate_distance_m]
    directional = [r for r in nearby if r['local_direction_deg'] is not None and r['local_direction_deg'] <= policy.direction_deg]
    eligible = [r for r in directional if r['overlap_m'] >= policy.minimum_curve_length_m]
    reason = ('NO_REFERENCE_WITHIN_GEOMETRIC_GATE' if not nearby else 'LOCAL_REFERENCE_DIRECTION_INCONSISTENT' if not directional
              else 'LOCAL_REFERENCE_OVERLAP_TOO_SHORT' if not eligible else None)
    if len(eligible) > 1 and eligible[1]['distance_m']-eligible[0]['distance_m'] < policy.ambiguity_gap_m:
        reason = 'DISTINCT_PHYSICAL_REFERENCE_AMBIGUITY'
    return {'reference_matches': options, 'selected_reference': deepcopy(eligible[0]) if eligible and reason is None else None,
            'reason_codes': [reason] if reason else ['LOCAL_GEOMETRIC_REFERENCE_ASSIGNED']}


def evaluate_marking_window(candidate, observations, elements, frames, visit_id, scene_id, policy=None):
    policy = policy or ReliabilityPolicy()
    refs = divider_references(elements)
    lookup = {r['boundary_id']: r for r in refs}
    first, last = candidate['frame_window']
    grouped, decisions = defaultdict(list), {}
    frame_lookup = {str(f['frame_index']): f for f in frames if first <= f['frame_index'] <= last}
    for row in observations:
        if not first <= row['frame_index'] <= last:
            raise ValueError('Marking observation lies outside its exact candidate window')
        key = row['frame_index'], row['line_id']
        if key in decisions:
            raise ValueError('Duplicate marking source frame/line identity')
        decision = match_local_curve(row['geometry_city'], refs, policy)
        decision.update(status='insufficient_geometry', score=1., observation='unknown', included_at_fixed_anchor=False)
        decisions[key] = decision
        selected = decision['selected_reference']
        if selected:
            grouped[selected['boundary_id']].append({'frame_id': str(row['frame_index']), 'scene_id': scene_id,
                'visit_id': visit_id, 'observation_id': row['source_file'], 'geometry': row['geometry_city']['coordinates'], 'quality': 1.})
    reasons = sorted({reason for d in decisions.values() for reason in d['reason_codes'] if reason != 'LOCAL_GEOMETRIC_REFERENCE_ASSIGNED'})
    fields = {'map_aliases': [], 'reliability': {'status': 'insufficient_geometry', 'score': 0., 'score_is_probability': False,
        'geometry_quality': 1. if observations else 0., 'calibrated': False, 'reason_codes': reasons,
        'conflict_kind': 'unverified_marking_difference', 'effective_coverage': None, 'alignment_verified': None}}
    fields['observation_evidence'] = {
        'scope': 'recorded_source_curves_and_exact_window_geometric_reference_search',
        'geometry_measurement': {'status': 'reference_unavailable', 'source_curve_count': len(observations),
                                 'measured_block_count': 0, 'median_residual_m': None},
        'temporal_support': {'status': 'unavailable_without_common_reference'},
        'local_consistency': {'status': 'unavailable'},
        'reference_semantics': {'status': 'no_unambiguous_local_reference', 'marking_type': None},
        'paint_identity': {'status': 'unverified'}, 'alignment': {'status': 'unverified'},
        'visibility': {'status': 'unverified', 'effective_coverage': None},
        'change_assessment': {'status': 'insufficient_geometry', 'confirmed_map_change': False}}
    if len(grouped) > 1:
        fields['reliability']['reason_codes'].append('REFERENCE_ASSIGNMENT_CHANGES_WITHIN_CANDIDATE_WINDOW')
    elif grouped:
        bid, rows = next(iter(grouped.items()))
        fitted = _event(lookup[bid], rows, frame_lookup, policy)
        for key in ('map_aliases', 'geometry_city', 'representative_source_id', 'fixed_anchor', 'median_residual_m',
                    'mad_residual_m', 'distinct_frame_count', 'temporal_block_count', 'span_s', 'same_sign_conflict_fraction'):
            fields[key] = deepcopy(fitted[key])
        fields['geometry_method'] = 'representative_observed_curve_at_fixed_reference_anchor_without_reconstruction'
        fields['reliability'] = dict(fitted['reliability'], geometry_quality=1., calibrated=False,
                                     conflict_kind='observed_local_marking_discrepancy')
        fields['observation_evidence'] = deepcopy(fitted['observation_evidence'])
        fields['observation_evidence']['geometry_measurement']['comparison_reference'] = 'source_geometric_divider_not_verified_edit_target'
        fields['observation_evidence']['reference_semantics'] = {
            'status': 'divider_geometry_only_paint_attributes_unverified', 'marking_type': None,
            'reference_layers': sorted({a['layer'] for a in lookup[bid]['aliases']}),
            'candidate_target_property': 'paint_presence_or_boundary_geometry'}
        emitted = {source: sample for sample in fitted['samples'] for source in sample['source_ids']}
        for row in observations:
            d = decisions[(row['frame_index'], row['line_id'])]
            if row['source_file'] in emitted:
                sample = emitted[row['source_file']]
                d.update(status=fitted['reliability']['status'], observation=sample['observation'],
                         included_at_fixed_anchor=True, residual_m=sample['residual_m'],
                         measured_local_relation=sample['measured_local_relation'])
                d['reason_codes'] = list(fitted['reliability']['reason_codes'])
            elif d['selected_reference']:
                d['reason_codes'].append('NO_SELECTED_FIXED_ANCHOR_INTERSECTION')
        anchor = next(a for a in _anchors(lookup[bid]['points'], policy.anchor_spacing_m)
                      if abs(a[0]-fitted['fixed_anchor']['arc_length_m']) < 1e-9)
        concurrent = []
        for frame in sorted(frame_lookup.values(), key=lambda f: f['frame_index']):
            for line in frame['observed_lines']:
                if candidate['candidate_id'] in line['candidate_ids']:
                    continue
                geometry = validate_geometry(line['geometry_city'], line_only=True)
                metrics = clipped_metrics(geometry['coordinates'], lookup[bid]['points'])
                if (metrics['distance_m'] is None or metrics['distance_m'] > policy.candidate_distance_m
                        or metrics['local_direction_deg'] is None or metrics['local_direction_deg'] > policy.direction_deg
                        or metrics['overlap_m'] < policy.minimum_curve_length_m):
                    continue
                residual = _fixed_residual(geometry['coordinates'], anchor)
                if residual is not None and abs(residual) <= policy.residual_tolerance_m:
                    concurrent.append({'frame_id': str(frame['frame_index']), 'timestamp_ns': frame['timestamp_ns'],
                        'line_id': line['line_id'], 'track_id': line.get('track_id'), 'residual_m': residual,
                        'source_ids': [line['source_file']], 'local_reference_metrics': metrics})
        fields['same_window_reference_observations'] = concurrent
        if concurrent:
            fields['reliability']['reason_codes'].append('REFERENCE_LINE_ALSO_OBSERVED_IN_SAME_WINDOW')
    fields['reliability']['reason_codes'] += ['PHYSICAL_ALIGNMENT_UNVERIFIED', 'GEOMETRIC_REFERENCE_NOT_VERIFIED_EDIT_TARGET',
                                              'DIVIDER_GEOMETRY_DOES_NOT_VERIFY_PAINT_ATTRIBUTES']
    fields.update(change_type='line_discrepancy', structural_hypothesis={
        'target_property': 'paint_presence_or_boundary_geometry', 'operation': 'unresolved_parallel_marking_or_insertion',
        'confirmed': False, 'scope': 'observed_local_segment'}, base_map_write_eligible=False,
        correspondence_policy={'candidate_distance_m': policy.candidate_distance_m, 'local_direction_deg': policy.direction_deg,
            'minimum_overlap_m': policy.minimum_curve_length_m, 'ambiguity_gap_m': policy.ambiguity_gap_m,
            'reference_role': 'geometric_comparison_reference_not_verified_edit_target', 'window': [first, last]})
    return {'event_fields': fields, 'observation_decisions': decisions}
