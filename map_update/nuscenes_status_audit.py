"""Post-prediction nuScenes status audit with explicit, separate denominators.

This function reports supplied annotation fields and model outputs. It neither
changes a decision nor treats object labels as historical road-change truth.
Call after prediction freeze, alongside evaluation, not during inference.
"""
from collections import Counter
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path


def _fraction(numerator, denominator):
    return numerator / denominator if denominator else None


def _unique(rows, key, description):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate ' + description)
    return result


def _boolean(value):
    if value in (True, 'True', 'true'):
        return True
    if value in (False, 'False', 'false'):
        return False
    raise ValueError('Invalid source boolean')


def build_nuscenes_status_audit(raw_root, delivery_root, events):
    """Return an immutable-input audit of source, routing, quality and writes.

    ``source_files`` and ``source_provenance`` use paths relative to their
    explicit ``root`` (``raw_root`` or ``delivery_root``), with content hashes.
    Structural quality rates count candidate windows, not confirmed GT changes.
    """
    roots = {'raw_root': Path(raw_root).resolve(), 'delivery_root': Path(delivery_root).resolve()}
    sources = []

    def read(root, relative, as_csv=False):
        path = roots[root] / relative
        data = path.read_bytes()
        sources.append({'root': root, 'path': relative, 'sha256': sha256(data).hexdigest(), 'role': 'audit_only'})
        decoded = data.decode('utf-8-sig')
        return list(csv.DictReader(StringIO(decoded))) if as_csv else json.loads(decoded)

    scenes = read('raw_root', 'v1.0-trainval/scene.json')
    samples = read('raw_root', 'v1.0-trainval/sample.json')
    annotations = read('raw_root', 'v1.0-trainval/sample_annotation.json')
    categories = read('raw_root', 'v1.0-trainval/category.json')
    tracking = read('raw_root', 'processed/tracking_report.json')
    unified = read('raw_root', 'processed/scene0980_unified_candidates_v1.csv', True)
    candidates = read('raw_root', 'processed/road_perception/scene0980_discrepancy_candidates_v1.csv', True)
    scored = read('delivery_root', 'outputs/stage3/scored_candidates.csv', True)
    labels = read('delivery_root', 'evaluation_labels/candidate_evidence.csv', True)
    evaluation = read('delivery_root', 'outputs/stage7/evaluation_report.json')

    scene_id = tracking['scene']
    matches = [r for r in scenes if r['name'] == scene_id]
    if len(matches) != 1:
        raise ValueError('nuScenes audit requires one matching source scene')
    scene_token = matches[0]['token']
    sample_index = _unique(samples, 'token', 'source sample identity')
    _unique(annotations, 'token', 'source annotation identity')
    if any(r['sample_token'] not in sample_index or
           sample_index[r['sample_token']]['scene_token'] != scene_token for r in annotations):
        raise ValueError('Annotation and audit scene differ')
    source_index = _unique(unified, 'candidate_id', 'source candidate identity')
    candidate_index = _unique(candidates, 'event_id', 'structural candidate identity')
    score_index = _unique(scored, 'candidate_id', 'scored candidate identity')
    event_index = _unique(events, 'event_id', 'model event identity')
    _unique(labels, 'id', 'candidate label identity')
    if (set(source_index) != set(event_index) or set(source_index) != set(score_index)
            or {r['id'] for r in labels} != set(source_index)):
        raise ValueError('Source, model, score and label candidate identities differ')
    if any(r['scene_id'] != scene_id for r in unified + candidates):
        raise ValueError('Cross-scene source candidate')
    structural_ids = {r['candidate_id'] for r in unified if r['evidence_family'] == 'LANE_MARKING_GEOMETRY'}
    if structural_ids != set(candidate_index):
        raise ValueError('Structural candidate tables disagree')

    category_names = {r['name'] for r in categories}
    category_counts = Counter(r.get('category_name') or 'missing' for r in annotations)
    known = sum(r.get('category_name') in category_names for r in annotations)
    instances = {r['instance_token'] for r in annotations}
    motion_counts = tracking['motion_state_counts']
    if (any(type(v) is not int or v < 0 for v in motion_counts.values())
            or sum(motion_counts.values()) != tracking['unique_instances']
            or tracking['unique_instances'] != len(instances)
            or tracking['track_points'] != len(annotations)):
        raise ValueError('Motion summary does not match supplied annotation population')

    diagnostics, quality_counts = [], Counter()
    for candidate_id, row in sorted(candidate_index.items()):
        event, source, score = event_index[candidate_id], source_index[candidate_id], score_index[candidate_id]
        window = [int(row['first_frame']), int(row['last_frame'])]
        if window != [int(source['first_frame']), int(source['last_frame'])]:
            raise ValueError('Source structural candidate windows differ')
        if event.get('correspondence_policy', {}).get('window') != window:
            raise ValueError('Model and source structural candidate windows differ')
        frame_ids = {int(s['frame_id']) for s in event.get('samples', [])}
        if (not frame_ids or min(frame_ids) != window[0] or max(frame_ids) != window[1]
                or len(frame_ids) != int(row['observed_frames'])
                or len(frame_ids) != int(source['observed_frames'])
                or len(frame_ids) != int(score['observed_frames'])):
            raise ValueError('Model and source structural observation frames differ')
        if event.get('scene_id', scene_id) != scene_id:
            raise ValueError('Model event belongs to another scene')
        decision = event.get('model_change_decision')
        if decision is None:
            status = {'supported_conflict': 'detected', 'consistent_with_map': 'no_geometric_discrepancy',
                      'insufficient_geometry': 'insufficient_evidence'}[event['reliability']['status']]
            detected = status == 'detected'
            status_source = 'local_reliability_output'
        else:
            status, detected = decision['status'], decision['detected']
            if type(detected) is not bool or detected != (status == 'detected'):
                raise ValueError('Contradictory model detection status')
            status_source = 'model_change_decision'
        if status not in ('detected', 'no_geometric_discrepancy', 'insufficient_evidence'):
            raise ValueError('Unknown structural quality status')
        quality_counts[status] += 1
        diagnostics.append({'candidate_id': candidate_id, 'frame_window': window,
                            'source_observed_frames': int(row['observed_frames']),
                            'model_recorded_frames': len(frame_ids), 'track_id': row['track_id'],
                            'track_identity_verified': _boolean(row['track_identity_verified']),
                            'source_review_status': row['review_status'],
                            'source_evidence_level': score['evidence_level'],
                            'source_support_frames': int(score['support_frames']),
                            'source_map_change_status': score['map_change_status'],
                            'model_status': status, 'model_detected': detected, 'model_status_source': status_source,
                            'final_structural_decision': event.get('final_structural_decision'),
                            'reason_codes': list(event['reliability'].get('reason_codes', [])),
                            'write_eligible': event.get('decision', {}).get('base_map_write_eligible') is True,
                            'map_patch_executed': event.get('decision', {}).get('write_gate', {}).get('map_patch_executed') is True})

    routing = Counter()
    for event in events:
        layer = event.get('map_layer', {})
        value = layer.get('predicted_layer')
        # A geometrically consistent result does not erase the semantic subject.
        if value == 'not_applicable':
            value = layer.get('conditional_layer_prediction')
        routing[value if value in ('map_state_event', 'base_map_structure') else 'unassigned'] += 1
    total, structural_total = len(events), len(diagnostics)
    insufficient_fraction = _fraction(quality_counts['insufficient_evidence'], structural_total)
    historical = evaluation.get('historical_map_change_evaluation', {})
    if historical.get('status') != 'NOT_AVAILABLE':
        raise ValueError('New historical-change truth requires an explicit audit schema and source contract')
    label_count = sum(bool(r['truth']) and r['truth'] != 'uncertain' and bool(r['annotator'])
                      and bool(r['evidence_reference']) for r in labels)
    blank_count = sum(not r['truth'] for r in labels)
    final_records = [r['final_structural_decision'] for r in diagnostics]
    final_available = all(isinstance(r, dict) for r in final_records)
    final_summary = {'available': final_available,
                     'scope': 'Final action on the supplied recording, separate from diagnostic residual quality and independent truth.'}
    if final_available:
        for decision in final_records:
            if (decision.get('processing_status') != 'complete'
                    or decision.get('action') not in ('update', 'no_update')
                    or decision.get('revisit_required') is not False
                    or decision.get('manual_review_required') is not False
                    or decision.get('decision_is_ground_truth') is not False):
                raise ValueError('Invalid closed structural decision')
        final_summary.update(total_candidates=len(final_records),
                             structural_updates=sum(r['action'] == 'update' for r in final_records),
                             closed_without_update=sum(r['action'] == 'no_update' for r in final_records),
                             pending=0, revisit_requests=0, manual_review_requests=0,
                             accuracy_available=False)
    return {
        'schema': 'nuscenes-separated-status-audit-1.0', 'scene_id': scene_id,
        'source_files': sources, 'source_provenance': sources,
        'prediction_inputs_modified': False, 'labels_used_for_model_prediction': False,
        'annotation_categories': {
            'total_annotations': len(annotations), 'known_category_annotations': known,
            'unknown_category_annotations': len(annotations) - known, 'unique_instances': len(instances),
            'category_counts': dict(category_counts),
            'scope': 'Supplied object-category annotations; not road-structure change labels.'},
        'object_motion_states': {
            'total_instances': tracking['unique_instances'], 'counts': dict(motion_counts),
            'uncertain_count': motion_counts.get('uncertain', 0),
            'uncertain_fraction': _fraction(motion_counts.get('uncertain', 0), tracking['unique_instances']),
            'insufficient_observations_count': motion_counts.get('insufficient_observations', 0),
            'source_description': tracking['source'],
            'scope': 'Derived motion-state heuristics over object instances; not structural-change uncertainty.'},
        'source_candidates': {
            'total_candidates': len(unified), 'structural_candidates': structural_total,
            'source_status_counts': dict(Counter(r['source_review_status'] for r in diagnostics)),
            'structural_evidence_level_counts': dict(Counter(r['source_evidence_level'] for r in diagnostics))},
        'independent_labels': {
            'candidate_rows': len(labels), 'verified_candidate_labels': label_count,
            'blank_candidate_labels': blank_count, 'verified_structural_change_labels': None,
            'structural_change_truth_available': False,
            'scope': 'Candidate evidence-support labels cannot substitute for independently verified prior/current map changes.',
            'source_reason': historical.get('reason')},
        'semantic_routing': {
            'total_candidates': total, 'state_events': routing['map_state_event'],
            'structural_candidates': routing['base_map_structure'], 'unassigned': routing['unassigned'],
            'unassigned_fraction': _fraction(routing['unassigned'], total),
            'scope': 'Semantic layer identity over all delivered candidates; not truth of the proposed change.'},
        'structural_quality': {
            'total_candidates': structural_total, 'detected': quality_counts['detected'],
            'insufficient_evidence': quality_counts['insufficient_evidence'],
            'geometrically_consistent': quality_counts['no_geometric_discrepancy'],
            'insufficient_fraction': insufficient_fraction,
            'scope': 'Local geometric model decision over structural candidate windows, not a ground-truth uncertainty rate.'},
        'structural_write': {
            'total_candidates': structural_total, 'eligible': sum(r['write_eligible'] for r in diagnostics),
            'executed': sum(r['map_patch_executed'] for r in diagnostics),
            'scope': 'Full structural edit eligibility/execution, separate from local detection.'},
        'final_decisions': final_summary,
        'five_percent_claim': {
            'historical_diagnostic_only': True,
            'applicable_to_current_final_action': False,
            'structural_truth_rate_verified': False, 'target_fraction': .05,
            'structural_model_insufficient_fraction': insufficient_fraction,
            'model_candidate_target_met': insufficient_fraction <= .05 if insufficient_fraction is not None else None,
            'meaning': 'Historical comparison target for diagnostic insufficiency, not a dataset statistic or the current final-action contract. Closed no-update decisions do not establish classification accuracy.'},
        'candidate_diagnostics': diagnostics,
    }
