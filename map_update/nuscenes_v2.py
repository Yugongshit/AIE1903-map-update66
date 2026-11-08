"""Observation-quality adapter for the supplied nuScenes replay.

Obstacle occupancy and road-marking correspondence have separate evidence paths.
This adapter uses the supplied annotation-backed footprints, not a new detector,
and does not consume duration labels, evaluate predictions or edit the map.
"""

from collections import Counter
from copy import deepcopy

from .automatic import build_automatic_decisions


def _geometry(row, occupancy):
    if not occupancy:
        return deepcopy(row['geometry_city'])
    # Preserve the individual source polygons: no inferred union or average.
    return {'type': 'GeometryCollection',
            'geometries': [deepcopy(obj['geometry_city']) for obj in row['objects']]}


def _occupancy_quality(row, target):
    reasons = []
    if not row['objects']:
        reasons.append('NO_OBSERVED_OBJECT_FOOTPRINTS')
    if any(obj.get('source') != 'nuScenes annotated oriented box' for obj in row['objects']):
        reasons.append('ANNOTATION_BACKING_UNVERIFIED')
    if target is None or target.get('layer') not in {'lane', 'lane_connector'}:
        reasons.append('MAPPED_LANE_TARGET_UNAVAILABLE')
    if row['lane_overlap_area_m2'] <= 0:
        reasons.append('NO_POSITIVE_RECORDED_LANE_OVERLAP')
    if not row['width_geometry_valid']:
        reasons.append('SOURCE_OCCUPANCY_GEOMETRY_CHECK_FAILED')
    return (0. if reasons else 1.), reasons or [
        'VALID_ANNOTATION_FOOTPRINTS', 'POSITIVE_RECORDED_LANE_OVERLAP',
        'OCCUPANCY_IS_NOT_A_ROAD_STRUCTURE_ERROR']


def evaluate_nuscenes(real_or_integrated):
    """Return dataset-neutral events for the independent duration classifier.

    Accept the integrated scene or dashboard ``real`` representation. Source
    annotation categories are observation inputs; external duration labels are
    neither accessed nor copied. Unknown or missing observations never clear an
    event. A marking's conditional duration can be predicted independently of
    its unresolved map correspondence.
    """
    real = real_or_integrated
    baseline = real.get('reference_map') or real['map']
    # Reuse the existing identity, timestamp, window and geometry validation.
    recorded = build_automatic_decisions(real)
    records = {r['candidate_id']: r for r in
               recorded['event_layer']['events']+recorded['difference_layer']['differences']}
    elements = {r['element_id']: r for r in baseline['elements']}
    if len(elements) != len(baseline['elements']):
        raise ValueError('Duplicate map element identity')
    events, decisions = [], []
    for candidate in real['candidates']:
        cid, occupancy = candidate['candidate_id'], candidate['change_type'] == 'occupancy'
        target_id = candidate.get('map_element_id')
        target = elements.get(target_id)
        observations = records.get(cid, {}).get('observations', [])
        samples, row_scores, row_reasons = [], [], set()
        categories = sorted({obj['category'] for row in observations for obj in row.get('objects', [])})
        semantic_kind = ('movable_occupancy' if categories and all(c.startswith('movable_object.') for c in categories)
                         else 'unknown') if occupancy else 'structural_marking'
        for row in observations:
            if occupancy:
                score, reasons = _occupancy_quality(row, target)
                observation = 'supports_change' if score else 'unknown'
                status = 'supported_conflict' if score else 'insufficient_geometry'
                source_ids = [cid, 'frame:'+str(row['frame_index'])]+['instance:'+o['instance_token'] for o in row['objects']]
                source_files = ['processed/objects.csv', 'processed/lane_impact_frames_v2.csv']
            else:
                # A valid observed curve is not a verified correspondence to a
                # prior-map line. In particular, nearest tokens and later track
                # matches must not become this candidate's target or recovery.
                score, observation, status = 0., 'unknown', 'insufficient_geometry'
                reasons = ['VALID_OBSERVED_MARKING_GEOMETRY', 'VERIFIED_MAP_CORRESPONDENCE_UNAVAILABLE',
                           'PHYSICAL_ALIGNMENT_UNVERIFIED', 'DURATION_CONDITIONAL_ON_A_REAL_CHANGE']
                source_ids = [cid, 'frame:'+str(row['frame_index']), 'line:'+str(row['line_id'])]
                source_files = [row['source_file']] if row.get('source_file') else []
            source_id = cid+':'+str(row['frame_index'])+(':'+str(row['line_id']) if not occupancy else '')
            sample = {'timestamp_ns': row['timestamp_ns'], 'frame_id': str(row['frame_index']),
                      'visit_id': real['visit_id'], 'observation': observation,
                      'quality': score if occupancy else 1., 'fully_observed': False,
                      'source_ids': source_ids, 'source_files': source_files,
                      'geometry_city': _geometry(row, occupancy),
                      'quality_scope': 'occupancy_support_checks' if occupancy else 'observed_curve_geometry_only_not_map_correspondence'}
            if occupancy:
                sample.update(lane_overlap_area_m2=row['lane_overlap_area_m2'],
                              occupied_width_fraction=row.get('occupied_width_fraction'),
                              instance_tokens=[o['instance_token'] for o in row['objects']])
            samples.append(sample)
            decisions.append({'event_id': cid, 'observation_id': source_id, 'frame_id': sample['frame_id'],
                              'timestamp_ns': sample['timestamp_ns'], 'status': status, 'score': score,
                              'reason_codes': reasons, 'observation': observation, 'geometry_quality': 1.,
                              'source_ids': source_ids})
            row_scores.append(score)
            row_reasons.update(reasons)
        supported = sum(sample['observation'] == 'supports_change' for sample in samples)
        score = sum(row_scores)/len(row_scores) if row_scores else 0.
        if not samples:
            row_reasons.add('NO_SUPPLIED_OBSERVATIONS')
        legacy = candidate.get('reliability')
        event = {'event_id': cid, 'change_type': candidate['change_type'], 'semantic_kind': semantic_kind,
                 'map_aliases': [{'map_element_id': target_id}] if occupancy and target is not None else [],
                 'geometry_city': deepcopy(samples[0]['geometry_city']) if samples else None,
                 'geometry_method': 'first_observed_frame_geometry_without_reconstruction',
                 'reliability': {'status': 'supported_conflict' if supported else 'insufficient_geometry',
                                 'score': score, 'score_is_probability': False, 'calibrated': False,
                                 'geometry_quality': 1. if samples else 0.,
                                 'reason_codes': sorted(row_reasons),
                                 'conflict_kind': 'obstacle_occupies_map_space' if occupancy else 'unverified_marking_difference'},
                 'samples': samples, 'conditional_on_change': True, 'base_map_write_eligible': False,
                 'evidence': {'source_candidate_id': cid, 'categories': categories,
                              'recorded_observations': len(samples), 'supported_observations': supported,
                              'last_observed_timestamp_ns': max((s['timestamp_ns'] for s in samples), key=int, default=None),
                              'source_kind': 'annotated_object_footprints' if occupancy else 'supplied_camera_bev_curves',
                              'legacy_m3_decision': legacy.get('decision') if legacy else None,
                              'legacy_m3_scope': 'lane_marking' if legacy else 'outside_lane_marking_scope' if occupancy else 'unavailable',
                              'clearance_observed': False}}
        events.append(event)
    counts = Counter(e['reliability']['status'] for e in events)
    legacy_units = real.get('m3', {}).get('review_units', [])
    return {'schema': 'nuscenes-reliability-v2',
            'policy': {'version': 'source_geometry_quality_v2', 'duration_classifier': 'separate_dataset_neutral_consumer',
                       'score_definition': 'Fraction of supplied candidate rows passing occupancy source/target/overlap checks; marking conflict score stays zero without a verified correspondence.',
                       'score_is_probability': False, 'calibrated': False, 'labels_used': False,
                       'sensor_inference_rerun': False, 'occupancy_source': 'supplied_nuscenes_annotations',
                       'missing_observation': 'unknown_not_clearance', 'manual_review_required': False,
                       'temporal_scope': 'completed_offline_recording_not_live_feed',
                       'structural_duration_scope': 'conditional_prediction_does_not_verify_a_map_change'},
            'summary': {'events': len(events), 'occupancy_events': sum(e['change_type'] == 'occupancy' for e in events),
                        'marking_events': sum(e['change_type'] == 'line_discrepancy' for e in events),
                        'observation_decisions': len(decisions), 'reliability_statuses': dict(counts),
                        'supported_conflict': counts['supported_conflict'], 'insufficient_geometry': counts['insufficient_geometry'],
                        'base_map_updates': 0},
            'events': events, 'observation_decisions': decisions,
            'diagnostics': {'legacy_m3_units': len(legacy_units),
                            'legacy_m3_statuses': dict(Counter(r['decision'] for r in legacy_units)),
                            'occupancy_uses_marking_m3_gate': False,
                            'new_detector_accuracy_measured': False,
                            'marking_map_targets_verified': 0},
            'provenance': {'dataset_id': real['dataset_id'], 'scene_id': real['scene_id'], 'visit_id': real['visit_id'],
                           'source_files': deepcopy(real.get('source_files', [])),
                           'source_m3_handoff': deepcopy(real.get('m3', {}).get('source_handoff')),
                           'map_sha256': baseline['source_sha256'], 'map_version': baseline['version'],
                           'coordinate_reference': deepcopy(baseline['coordinate_reference']),
                           'observation_sha256': recorded['observation_sha256']}}
