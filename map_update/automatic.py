"""Complete a recorded-scene run without creating a pending human-review task.

Recording a measured observation and editing a stable map are separate actions.
This module materializes the former from the supplied per-frame geometry. It does
not turn the historical M3 diagnosis, a score, or missing observations into an
approved structural edit, a duration label, or a clearance claim.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from math import isfinite

from .geometry import validate_geometry


def _unique(rows, field):
    result = {}
    for row in rows:
        value = row[field]
        if value in result:
            raise ValueError('Duplicate automatic-processing identity: '+str(value))
        result[value] = row
    return result


def _finite_nonnegative(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def build_automatic_decisions(real):
    """Return separate decision, event and difference layers for finite input.

    Accept either the dashboard ``real`` object or an integrated scene. The
    source object is never modified. ``complete`` refers to processing the
    provided observations, not to proving permanence or measuring accuracy.
    ``frames`` exports contain only source-frame geometry, never interpolation.
    Last-observed timestamps describe source samples, not the availability of
    offline candidate assignments or whole-window scores for online decisions.
    """
    baseline = real.get('reference_map') or real['map']
    frames = real.get('replay', real.get('frames', []))
    if real.get('dataset_id') != 'nuscenes' or baseline.get('dataset_id') != 'nuscenes':
        raise ValueError('Automatic recorded-scene processing requires nuScenes identity')
    if real.get('maintenance', {}).get('base_map_updates'):
        raise ValueError('Structural updates require a versioned map-write result, not observation-only processing')
    candidates = _unique(real['candidates'], 'candidate_id')
    _unique(frames, 'frame_index')
    samples = {cid: [] for cid in candidates}
    frame_times = {}
    for frame in frames:
        index = frame['frame_index']
        stamp = frame['timestamp_ns']
        if type(index) is not int or index < 0 or not isinstance(stamp, str) or not stamp.isdecimal():
            raise ValueError('Frame index and exact timestamp are required')
        frame_times[index] = stamp
        frame_metadata = {'frame_index': index, 'timestamp_ns': stamp,
                          'elapsed_s': frame.get('elapsed_s')}
        in_frame = set()
        for kind, records in [('occupancy', frame['occupancy']), ('line_discrepancy', frame['observed_lines'])]:
            for record in records:
                # A nested observation cannot move geometry into another frame
                # or override the enclosing frame's validated timestamp.
                for field, value in frame_metadata.items():
                    if field in record and (record[field] != value
                                            or type(record[field]) is bool):
                        raise ValueError('Observation frame metadata conflicts with enclosing frame: '+field)
                ids = [record['candidate_id']] if kind == 'occupancy' else record['candidate_ids']
                for cid in ids:
                    if cid not in candidates:
                        raise ValueError('Observation references an unknown candidate: '+cid)
                    candidate = candidates[cid]
                    if candidate['change_type'] != kind:
                        raise ValueError('Candidate and observation type differ: '+cid)
                    first, last = candidate['frame_window']
                    if not first <= index <= last:
                        raise ValueError('Observation outside candidate window: '+cid)
                    key = (cid, record.get('line_id'))
                    if key in in_frame:
                        raise ValueError('Duplicate candidate observation in frame: '+cid)
                    in_frame.add(key)
                    if kind == 'occupancy':
                        if record.get('map_element_id') != candidate.get('map_element_id'):
                            raise ValueError('Occupancy target differs from source candidate: '+cid)
                        if not record['objects']:
                            continue
                        _unique(record['objects'], 'instance_token')
                        for obj in record['objects']:
                            if obj['geometry_city']['type'] != 'Polygon':
                                raise ValueError('Occupancy requires observed polygon footprints')
                            validate_geometry(obj['geometry_city'])
                        if not _finite_nonnegative(record['lane_overlap_area_m2']):
                            raise ValueError('Occupancy overlap must be finite and nonnegative')
                        if type(record['width_geometry_valid']) is not bool:
                            raise ValueError('Occupancy geometry validity must be explicit')
                    else:
                        validate_geometry(record['geometry_city'], line_only=True)
                    samples[cid].append({**deepcopy(record), **frame_metadata})

    decisions, events, differences = {}, [], []
    for cid, candidate in candidates.items():
        kind = candidate['change_type']
        if kind not in {'occupancy', 'line_discrepancy'}:
            raise ValueError('Unsupported observation-only candidate type: '+kind)
        observations = sorted(samples[cid], key=lambda row: (row['frame_index'], row.get('line_id', 0)))
        indices = sorted({row['frame_index'] for row in observations})
        occupancy = kind == 'occupancy'
        categories = sorted({obj['category'] for row in observations for obj in row.get('objects', [])})
        instances = sorted({obj['instance_token'] for row in observations for obj in row.get('objects', [])})
        source_files = sorted({row['source_file'] for row in observations if row.get('source_file')})
        source_m3 = candidate.get('reliability')
        reasons = ['persistence_unavailable_single_visit']
        if occupancy:
            reasons += ['occupancy_does_not_define_structural_edit', 'lane_passability_not_evaluated']
        else:
            if candidate.get('map_element_id') is None:
                reasons.append('structural_edit_target_unavailable')
            if not source_m3 or source_m3.get('effective_coverage') is None:
                reasons.append('effective_coverage_unavailable')
            if not source_m3 or source_m3.get('alignment_valid') is not True:
                reasons.append('alignment_evidence_unavailable')
            if baseline.get('map_role') not in {'verified_prior', 'controlled_synthetic_prior'}:
                reasons.append('reference_temporal_role_unverified')
        if not observations:
            reasons.insert(0, 'no_usable_observation_geometry')
        else:
            reasons.insert(0, 'recorded_occupancy_geometry' if occupancy else 'recorded_line_difference')
        evidence = {'frame_indices': indices, 'recorded_frames': len(indices),
                    'geometry_observations': len(observations),
                    'source_support_frames': candidate.get('support_frames'),
                    'source_evidence_score': candidate.get('evidence_score'),
                    'score_meaning': 'source ranking score, not a probability',
                    'source_m3_decision': source_m3.get('decision') if source_m3 else None,
                    'source_m3_scope': 'lane_marking' if source_m3 else 'not_applicable_to_occupancy' if occupancy else 'unavailable',
                    'categories': categories, 'instance_tokens': instances, 'source_files': source_files,
                    'last_observed_timestamp_ns': max((row['timestamp_ns'] for row in observations), key=int, default=None)}
        decisions[cid] = {
            'candidate_id': cid, 'processing_status': 'complete', 'manual_review_required': False,
            'action': ('event_recorded' if occupancy else 'difference_recorded') if observations else 'no_usable_observation',
            'target_layer': ('event' if occupancy else 'observation') if observations else None,
            'observation_status': ('observed_occupancy' if occupancy else 'observed_difference') if observations else 'no_usable_geometry',
            'persistence': 'unknown', 'map_action': 'unchanged', 'base_map_write_eligible': False,
            'structural_outcome': 'not_supported_by_supplied_data',
            'reasons': reasons, 'evidence': evidence,
        }
        if not observations:
            continue
        record = {'candidate_id': cid, 'map_element_id': candidate.get('map_element_id'),
                  'frame_window': deepcopy(candidate['frame_window']), 'processing_status': 'complete',
                  'manual_review_required': False, 'observations': observations}
        if occupancy:
            record.update(event_id=cid, classification='occupancy', duration_class='unknown',
                          clearance_confirmed=False, base_map_write_eligible=False,
                          planning_action='recorded_geometry_available_for_offline_planning')
            events.append(record)
        else:
            record.update(observation_id=cid, classification='line_discrepancy',
                          verified_edit_target=None, base_map_write_eligible=False)
            differences.append(record)
    layer_context = {'dataset_id': real['dataset_id'], 'scene_id': real['scene_id'],
                     'visit_id': real['visit_id'], 'map_version': baseline['version'],
                     'map_sha256': baseline['source_sha256'],
                     'coordinate_reference': deepcopy(baseline['coordinate_reference']),
                     'scope': 'Recorded frame observations; not a live obstacle feed or a structural map edit.'}
    counts = Counter(row['action'] for row in decisions.values())
    return {
        'schema': 'automatic-recorded-decisions-1.0',
        'policy': {'manual_review_required': False, 'mode': 'finite_recorded_dataset',
                   'temporal_scope': 'completed_offline_artifact_not_online_availability',
                   'unknown_handling': 'closed_data_unavailable_outcome',
                   'missing_frame': 'not_observed_not_cleared',
                   'source_diagnostics': 'preserved_without_promoting_unknown_values',
                   'processing_complete_means': 'all supplied candidates processed; not all scientific questions resolved'},
        'summary': {'processed': len(decisions), 'event_records': counts['event_recorded'],
                    'difference_records': counts['difference_recorded'],
                    'no_usable_observation': counts['no_usable_observation'],
                    'base_map_updates': 0, 'pending_manual_reviews': 0},
        'candidates': decisions,
        'event_layer': {**layer_context, 'schema': 'recorded-event-layer-1.0', 'events': events},
        'difference_layer': {**layer_context, 'schema': 'recorded-difference-layer-1.0', 'differences': differences},
        'observation_sha256': sha256(json.dumps(samples, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
    }
