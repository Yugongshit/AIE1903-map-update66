"""Join the delivered scene-0980 M3 supplement without manufacturing write evidence.

This adapter covers the supplied v1 supplement, whose true visibility coverage
is unknown. A future measured-coverage delivery requires an explicit contract
and re-evaluation; editing a null in its JSON cannot open the map-writing gate.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from math import isclose, isfinite
from pathlib import Path

from .delivery import read_json
from .scene import index_unique, inspect_scene


LABELS = {
    'INSUFFICIENT_EVIDENCE': 'Insufficient Evidence',
    'NO_SIGNIFICANT_MISMATCH': 'No Significant Mismatch',
    'SUSPECTED_MAP_CHANGE': 'Suspected Map Change',
    'NEEDS_FURTHER_VERIFICATION': 'Needs Further Verification',
}


def attach_m3(scene, handoff):
    """Validate identity/window joins and return a new scene plus maintenance layers."""
    data = deepcopy(scene)
    checks = []

    def require(name, valid):
        checks.append({'check': name, 'passed': bool(valid)})
        if not valid:
            raise ValueError('M3 integration failed: '+name)

    def integer(value):
        return type(value) is int and value >= 0

    def finite(value):
        return type(value) in (int, float) and isfinite(value)

    require('dataset_identity', data.get('dataset_id') == 'nuscenes'
            and data['reference_map'].get('dataset_id') == 'nuscenes'
            and handoff.get('dataset_id', 'nuscenes') == 'nuscenes')
    require('scene_identity', handoff['scene'] == data['scene_id'])
    require('real_lane_marking_scope', handoff['scope'] == 'lane-marking synthesis from one drive')
    require('decision_labels', handoff['decision_labels'] == LABELS)
    baseline = data['reference_map']
    source = handoff['source_map']
    require('source_map_file', source['file'] == baseline['map_id']+'.json')
    require('source_map_version', source['version'] == baseline['source_version'])
    require('source_map_sha256', source['sha256'] == baseline['source_sha256'])
    frames = index_unique(data['replay'], lambda r: r['frame_index'])
    candidates = index_unique(data['candidates'], lambda r: r['candidate_id'])
    cases = index_unique(handoff['cases'], lambda r: r['record_id'])
    elements = index_unique(baseline['elements'], lambda r: r['element_id'])
    require('nonempty_real_cases', bool(cases))
    require('no_controlled_fields_in_real_cases', all(
        not any(k.startswith(('scripted_', 'controlled_')) for k in row) for row in cases.values()))
    matched_rows = index_unique(handoff['matched_frame_evidence'],
                                lambda r: (r['frame'], r['line_id'], r['track']))
    units = []
    consumed = set()
    for rid, row in cases.items():
        require(rid+':record_type', row['record_type'] in {'MATCHED_WINDOW', 'UNMATCHED_LANE_CANDIDATE'})
        require(rid+':map_identity', row['map_version'] == source['version'] and row['map_sha256'] == source['sha256'])
        first, last = row['first_frame'], row['last_frame']
        require(rid+':frame_range', integer(first) and integer(last) and first <= last and first in frames and last in frames)
        for end, idx in [('first', first), ('last', last)]:
            stamp = row[end+'_timestamp_us']
            require(rid+':'+end+'_absolute_time', integer(stamp) and str(stamp*1000) == frames[idx]['timestamp_ns'])
            require(rid+':'+end+'_relative_time', finite(row[end+'_time_s']) and
                    abs(row[end+'_time_s']-frames[idx]['elapsed_s']) <= 0.0001)
        # The delivered synthesis explicitly expects unknown coverage. Measured
        # values must be joined with their source and re-evaluated upstream first.
        require(rid+':supported_unknown_coverage_contract', row['effective_coverage'] is None
                and row['valid_visibility_frames'] is None and row['coverage_gate'] == 'UNKNOWN')
        require(rid+':decision_respects_unknown_coverage', row['final_decision'] == 'INSUFFICIENT_EVIDENCE')
        require(rid+':review_priority', row['review_priority'] in {'STANDARD', 'PRIORITY'})
        require(rid+':observed_frames', integer(row['observed_frames']) and row['observed_frames'] > 0)
        candidate_id = target = None
        blockers = ['effective_coverage_unknown', 'physical_alignment_assessment_unavailable',
                    'independent_revisits_unavailable', 'reference_map_temporal_role_unverified']
        if row['record_type'] == 'UNMATCHED_LANE_CANDIDATE':
            c = candidates.get(rid)
            require(rid+':candidate_identity', c is not None and c['change_type'] == 'line_discrepancy')
            require(rid+':exact_candidate_window', c['frame_window'] == [first, last]
                    and c['source_context']['track_id'] == row['track'] and c['observed_frames'] == row['observed_frames'])
            require(rid+':no_unverified_map_target', row['map_token'] is None)
            require(rid+':upstream_support_count', row['upstream_support_frames'] == c['support_frames'])
            require(rid+':upstream_score', finite(row['upstream_evidence_score']) and
                    isclose(row['upstream_evidence_score'], c['evidence_score'], abs_tol=1e-8))
            require(rid+':upstream_evidence_level', row['upstream_evidence_level'] == c['raw']['evidence_level'])
            candidate_id = rid
            blockers += ['verified_edit_target_unavailable', 'structural_change_type_and_replacement_geometry_unverified']
        else:
            require(rid+':distinct_window_id', rid not in candidates)
            matches = [e for e in elements.values() if e['map_token'] == row['map_token']
                       and e['layer'] in {'lane_divider', 'road_divider'}]
            require(rid+':reference_divider', len(matches) == 1 and row['map_part'] == 0)
            target = matches[0]['element_id']
            selected = [(key, r) for key, r in matched_rows.items()
                        if r['track'] == row['track'] and first <= r['frame'] <= last]
            require(rid+':matched_frame_count', len(selected) == row['observed_frames'] == last-first+1)
            require(rid+':one_observation_per_frame', {r['frame'] for _, r in selected} == set(range(first, last+1)))
            for key, evidence in selected:
                require(rid+':unambiguous_frame_assignment:'+str(key), key not in consumed)
                consumed.add(key)
                frame = frames[evidence['frame']]
                curves = [line for line in frame['observed_lines'] if line['line_id'] == evidence['line_id']
                          and line['track_id'] == evidence['track']]
                require(rid+':source_curve:'+str(key), len(curves) == 1 and
                        curves[0]['best_map_token'] == row['map_token'])
                require(rid+':frame_map_identity:'+str(key), evidence['map_token'] == row['map_token']
                        and evidence['map_part'] == row['map_part'] and evidence['map_sha256'] == source['sha256']
                        and evidence['map_version'] == source['version'])
                require(rid+':frame_time:'+str(key), integer(evidence['timestamp_us'])
                        and str(evidence['timestamp_us']*1000) == frame['timestamp_ns']
                        and finite(evidence['time_s']) and abs(evidence['time_s']-frame['elapsed_s']) <= 1e-6)
                score, threshold = evidence['anomaly_score'], evidence['anomaly_threshold']
                require(rid+':finite_anomaly:'+str(key), finite(score) and score >= 0 and finite(threshold) and threshold > 0)
                require(rid+':anomaly_flag:'+str(key), type(evidence['above_reference']) is bool
                        and evidence['above_reference'] == (score > threshold))
                require(rid+':unknown_frame_coverage:'+str(key), evidence['effective_coverage'] is None)
                require(rid+':no_calibrated_p_value:'+str(key), evidence['p_value'] is None)
                curves[0]['m3_record_id'] = rid
            require(rid+':above_reference_count', sum(r['above_reference'] for _, r in selected) == row['frames_above_reference'])
            blockers.append('local_match_diagnostic_is_not_a_change_or_clearance_decision')
        unit = {'record_id': rid, 'candidate_id': candidate_id, 'record_type': row['record_type'],
                'track_id': row['track'], 'reference_element_id': target,
                'frame_window': [first, last], 'timestamp_window_ns': [str(row['first_timestamp_us']*1000), str(row['last_timestamp_us']*1000)],
                'decision': LABELS[row['final_decision']], 'effective_coverage': None,
                'alignment_valid': None, 'review_priority': row['review_priority'],
                'write_eligible': False, 'blockers': blockers, 'source_case': deepcopy(row)}
        units.append(unit)
        if candidate_id:
            c['reliability'] = deepcopy(unit)
            c['reasons'] = blockers
            c['review_priority'] = row['review_priority']
    require('no_orphan_matched_frames', consumed == set(matched_rows))
    ld_ids = {c['candidate_id'] for c in candidates.values() if c['change_type'] == 'line_discrepancy'}
    require('all_lane_candidates_accounted_for', {u['candidate_id'] for u in units if u['candidate_id']} == ld_ids)
    counts = Counter(u['record_type'] for u in units)
    occupancy = [c for c in candidates.values() if c['change_type'] == 'occupancy']
    expected = {'matched_windows': counts['MATCHED_WINDOW'], 'unmatched_lane_candidates': counts['UNMATCHED_LANE_CANDIDATE'],
                'matched_test_frames': len(matched_rows),
                'matched_test_frames_above_reference': sum(r['above_reference'] for r in matched_rows.values()),
                'insufficient_evidence_cases': len(units), 'priority_review_cases': sum(u['review_priority'] == 'PRIORITY' for u in units),
                'occupancy_candidates_outside_scope': len(occupancy)}
    require('summary_matches_records', handoff['summary'] == expected)
    require('decision_coverage_matches_records', handoff['evaluable_coverage']['units'] == len(units)
            and handoff['evaluable_coverage']['resolved_units'] == 0)

    events = []
    for c in occupancy:
        c['reasons'] = [r for r in c['reasons'] if r != 'M3_reliability_not_supplied'] + ['outside_lane_marking_M3_scope']
        c['storage_layer'] = 'temporary_event_candidates'
        samples = []
        for frame in data['replay']:
            for observation in frame['occupancy']:
                if observation['candidate_id'] == c['candidate_id']:
                    samples.append({'frame_index': frame['frame_index'], 'timestamp_ns': frame['timestamp_ns'],
                                    **deepcopy(observation)})
        events.append({'event_id': c['candidate_id'], 'map_element_id': c['map_element_id'],
                       'classification': 'occupancy', 'handling': 'temporary_event_candidate',
                       'duration_class': 'unknown', 'clearance_confirmed': False, 'write_eligible': False,
                       'planning_action': 'independent_passability_review_required',
                       'frame_window': c['frame_window'], 'observations': samples})
    data['schema_version'] = 'scene-m3-maintenance-1.0'
    data['m3'] = {'dataset_id': data['dataset_id'], 'adapter': 'scene0980-supplement-v1-unknown-coverage', 'summary': expected,
                  'review_units': units, 'matched_frame_evidence': deepcopy(handoff['matched_frame_evidence']),
                  'source_map': deepcopy(source), 'statistical_scope': deepcopy(handoff['statistical_scope']),
                  'limitations': deepcopy(handoff['limitations']), 'validation': {'status': 'PASS', 'checks': checks}}
    data['event_layer'] = {'schema_version': 'event-candidates-1.0', 'dataset_id': data['dataset_id'], 'scene_id': data['scene_id'],
                           'map_sha256': baseline['source_sha256'], 'map_version': baseline['version'],
                           'coordinate_reference': baseline['coordinate_reference'], 'visit_id': data['visit_id'],
                           'scope': 'Retrospective observations from one recorded visit, not current live obstacles.',
                           'events': events}
    data['maintenance'] = {'mode': 'reference_and_event_layers', 'base_map_updates': [],
                           'reference_map_modified': False, 'planning_required_for_map_maintenance': False,
                           'planning_module_executed': False, 'long_term_changes_confirmed': 0,
                           'review_units': len(units), 'resolved_review_units': 0,
                           'decision_coverage': 0.0, 'temporary_event_candidates': len(events),
                           'priority_review_ids': [u['record_id'] for u in units if u['review_priority'] == 'PRIORITY']}
    data['missing_requirements'] = ['Measured effective coverage and physical alignment evidence',
                                    'Independent revisit evidence for long-term claims',
                                    'Verified temporal role of the reference map',
                                    'Verified structural edit target/type and replacement geometry for LD candidates',
                                    'Independent calibration/test labels for accuracy evaluation']
    data['limitations'] = ['M3 was supplied and joined; all seven real review units remain Insufficient Evidence.',
                           'T004 frames 26–40 do not clear LD003 at frames 18–25; no track-wide decision propagation.',
                           'Unknown coverage is not zero or full visibility. Within-reference diagnostics are not clearance.',
                           'Occupancy is retained in an event-candidate layer, without inferring duration or lane closure.',
                           'The reference map is unchanged; temporal role and independent revisit evidence remain unverified.',
                           'Controlled perturbations are excluded from these real-scene layers. No real accuracy claimed.']
    data['summary'].update(m3_review_units=len(units), m3_resolved_units=0, m3_priority_review_cases=expected['priority_review_cases'],
                           temporary_event_candidates=len(events), base_map_updates=0)
    return data


def integrate_m3(raw_root, delivery_root, handoff_path):
    scene = inspect_scene(raw_root, delivery_root)
    path = Path(handoff_path)
    data = attach_m3(scene, read_json(path))
    data['m3']['source_handoff'] = {'file': path.name, 'sha256': sha256(path.read_bytes()).hexdigest()}
    return data
