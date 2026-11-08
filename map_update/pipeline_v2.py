"""Run observation reliability, semantic map-layer routing and write gates.

Labels enter only after predictions have been written and hashed. The supplied
reference map and frozen perception observations are never modified.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json

from . import argoverse as av
from .engine import Policy, independent_visits
from .nuscenes_v2 import evaluate_nuscenes
from .map_layers import classify_map_layer
from .state_events import build_state_event_layer
from .reliability import integrate_m3
from .reliability_v2 import ReliabilityPolicy, canonical_boundaries, evaluate_argoverse
from .structure_evaluation import evaluate_all_boundaries


def write_gate(event, map_layer, policy=None):
    """Check a structural edit contract; duration never selects the map layer.

    The persistence module's frame helpers are used only for input validation,
    causal slicing and duplicate collapse. No duration model is executed here.
    Visit counts and span are diagnostics, never prerequisites for structure.
    Geometry edits, attribute edits and deletion have distinct evidence contracts.
    Eligibility is not execution; synchronized edits of aliases are not implemented.
    """
    from .geometry import validate_geometry
    from .persistence import _frames, _timestamp

    policy = policy or Policy()
    reasons = []
    cutoff = _timestamp(event['as_of_timestamp_ns']) if event.get('as_of_timestamp_ns') is not None else None
    observed = _frames(event.get('samples', []), cutoff)
    recoveries = [int(s['timestamp_ns']) for s in observed
                  if s['observation'] == 'supports_prior' and s.get('fully_observed') is True
                  and s.get('quality', 0) > 0]
    latest_recovery = max(recoveries, default=None)
    positive = [s for s in observed if s['observation'] == 'supports_change'
                and s.get('quality', 0) > 0
                and (latest_recovery is None or int(s['timestamp_ns']) > latest_recovery)]
    if latest_recovery is not None and not positive:
        reasons.append('EXPLICIT_RECOVERY_AFTER_LAST_SUPPORT')
    if not positive:
        reasons.append('NO_CURRENT_POSITIVE_OBSERVATION')
    rows = [dict(s, visit_id=s.get('visit_id', 'unidentified-single-visit')) for s in positive]
    visits = independent_visits(rows, policy)
    span = (int(positive[-1]['timestamp_ns'])-int(positive[0]['timestamp_ns']))/1e9 if positive else 0.
    if event.get('reliability', {}).get('status') != 'supported_conflict':
        reasons.append('OBSERVED_CONFLICT_NOT_SUPPORTED')
    if map_layer.get('predicted_layer') != 'base_map_structure':
        reasons.append('NO_STRUCTURAL_LAYER_PREDICTION')
    operation = event.get('change_type')
    if operation not in {'geometry_change', 'marking_change', 'add', 'delete'}:
        reasons.append('NO_STRUCTURAL_EDIT_OPERATION')
    edit_scope = {'geometry_change': 'whole_feature_geometry_replacement',
                  'add': 'new_feature_geometry', 'marking_change': 'marking_attribute_only',
                  'delete': 'existing_feature_removal'}.get(operation, 'unsupported_operation')
    requirements = ['supported_conflict', 'causal_positive_observation', 'base_map_structure',
                    'physical_alignment_verified', 'effective_visibility_verified']
    aliases = event.get('map_aliases', [])
    def valid_identity(value):
        return (type(value) is int or isinstance(value, str) and bool(value.strip()))
    valid_aliases = (isinstance(aliases, list) and bool(aliases)
                     and all(isinstance(alias, dict) and valid_identity(alias.get('map_element_id'))
                             for alias in aliases))
    physical_id = event.get('canonical_boundary_id', event.get('physical_boundary_id'))
    shared_physical_target = bool(valid_aliases and len(aliases) > 1 and valid_identity(physical_id)
        and event.get('physical_boundary_id', physical_id) == physical_id
        and all(alias.get('physical_boundary_id') == physical_id for alias in aliases))
    target_identity = {'map_reference_count': len(aliases) if isinstance(aliases, list) else None,
                       'single_map_reference': bool(valid_aliases and len(aliases) == 1),
                       'shared_canonical_physical_boundary': shared_physical_target,
                       'physical_boundary_id': physical_id if shared_physical_target else None,
                       'synchronized_alias_edit_verified': False}
    if operation == 'add':
        requirements += ['new_element_id', 'novelty_verified', 'no_existing_edit_target']
        if not valid_identity(event.get('new_element_id')):
            reasons.append('NEW_ELEMENT_IDENTITY_UNRESOLVED')
        if event.get('novelty_verified') is not True:
            reasons.append('ADDITION_NOVELTY_UNVERIFIED')
        if aliases:
            reasons.append('ADDITION_REFERENCES_EXISTING_EDIT_TARGET')
    else:
        requirements.append('single_edit_target_or_verified_synchronized_alias_edit')
        if shared_physical_target:
            # A known physical boundary may be stored in several lane sides.
            # This identifies the missing atomic update capability, not a new
            # geometric ambiguity. A caller-supplied flag is not that capability.
            reasons.append('SYNCHRONIZED_ALIAS_EDIT_UNVERIFIED')
        elif not valid_aliases or len(aliases) != 1:
            reasons.append('EXACT_EDIT_OBJECT_UNRESOLVED')
    for key, reason in [('physical_alignment_verified', 'PHYSICAL_ALIGNMENT_UNVERIFIED'),
                        ('effective_visibility_verified', 'EFFECTIVE_VISIBILITY_UNVERIFIED')]:
        if event.get(key) is not True:
            reasons.append(reason)
    if operation in {'geometry_change', 'add'}:
        requirements += ['replacement_geometry_verified', 'valid_complete_edit_geometry']
        if event.get('replacement_geometry_verified') is not True:
            reasons.append('FULL_EDIT_GEOMETRY_UNVERIFIED')
        try:
            validate_geometry(event.get('geometry_city'))
        except (ValueError, TypeError, KeyError, IndexError):
            reasons.append('INVALID_OR_MISSING_EDIT_GEOMETRY')
    if operation == 'marking_change':
        requirements += ['defined_marking_type', 'marking_property_verified']
        if (not isinstance(event.get('marking_type'), str)
                or event['marking_type'].strip().lower() in {'', 'unknown', 'none'}):
            reasons.append('VERIFIED_MARKING_PROPERTY_UNAVAILABLE')
        if event.get('marking_property_verified') is not True:
            reasons.append('MARKING_PROPERTY_EVIDENCE_UNVERIFIED')
    fully_observed_delete_frames = 0
    if operation == 'delete':
        requirements.append('fully_observed_same_target_causal_deletion_support')
        positive_times = {int(row['timestamp_ns']) for row in positive}
        full_times = set()
        # _frames deliberately reserves its fully_observed output for recovery.
        # Rejoin source deletion support to the validated, causal, nonconflicting
        # positive timestamps; never reuse pre-recovery or future observations.
        for row in event.get('samples', []):
            stamp = _timestamp(row['timestamp_ns'])
            if (stamp not in positive_times or row.get('observation') != 'supports_change'
                    or row.get('fully_observed') is not True or row.get('quality', 0) <= 0):
                continue
            if row.get('event_id', event.get('event_id')) != event.get('event_id'):
                continue
            if not valid_aliases or not any(
                row.get('map_element_id', alias['map_element_id']) == alias['map_element_id']
                and ('boundary_side' not in row or row['boundary_side'] == alias.get('boundary_side'))
                and ('physical_boundary_id' not in row or row['physical_boundary_id'] == alias.get('physical_boundary_id'))
                for alias in aliases
            ):
                continue
            full_times.add(stamp)
        fully_observed_delete_frames = len(full_times)
        if not full_times:
            reasons.append('DELETION_REQUIRES_FULLY_OBSERVED_SAME_TARGET_SUPPORT')
    return {'eligible': not reasons, 'reasons': reasons, 'independent_visits': len(visits),
            'positive_observation_frames': len(positive), 'span_s': span,
            'visit_counting_gap_s': policy.min_visit_gap_s, 'visits_and_span_are_diagnostics_only': True,
            'duration_used': False, 'policy_source': 'operation_specific_structural_edit_contract_v3',
            'edit_scope': edit_scope, 'requirements': requirements, 'target_identity': target_identity,
            'fully_observed_delete_frames': fully_observed_delete_frames,
            'as_of_timestamp_ns': str(cutoff) if cutoff is not None else None,
            'map_patch_executed': False}


def classify_events(data):
    result = deepcopy(data)
    for event in result['events']:
        layer = classify_map_layer(event)
        status = event['reliability']['status']
        if status == 'consistent_with_map':
            layer['conditional_layer_prediction'] = layer['predicted_layer']
            layer['predicted_layer'] = 'not_applicable'
        gate = write_gate(event, layer)
        applied = status == 'supported_conflict' and layer['predicted_layer'] in {'map_state_event', 'base_map_structure'}
        action = ('state_event' if layer['predicted_layer'] == 'map_state_event' else 'structural_change_candidate') if applied else (
            'no_discrepancy_in_observed_segment' if status == 'consistent_with_map' else 'record_only')
        event['map_layer'] = layer
        event['decision'] = {'action': action, 'classification_applied': applied,
                             'base_map_write_eligible': gate['eligible'], 'write_gate': gate,
                             'map_action': 'unchanged', 'processing_status': 'complete',
                             'manual_review_required': False}
        if 'model_change_decision' in event:
            event['decision']['model_detected_local_change'] = event['model_change_decision']['detected']
            event['decision']['confirmation_scope'] = event['model_change_decision']['scope']
            event['decision']['write_status'] = 'not_executed'
    events = result['events']
    result['state_event_layer'] = build_state_event_layer(events)
    result['source_summary'] = result.get('summary', {})
    result['summary'] = {
        'events': len(events),
        'reliability_counts': dict(Counter(e['reliability']['status'] for e in events)),
        'layer_counts': dict(Counter(e['map_layer']['predicted_layer'] for e in events)),
        'accepted_layer_counts': dict(Counter(e['map_layer']['predicted_layer'] for e in events if e['decision']['classification_applied'])),
        'stored_state_events': len(result['state_event_layer']['features']),
        'base_map_updates': 0, 'pending_manual_reviews': 0,
        'model_detected_local_structure': sum(e.get('model_change_decision', {}).get('detected', False)
                                              and e['map_layer']['predicted_layer'] == 'base_map_structure' for e in events),
    }
    return result


def _metrics(positive, truth):
    positive, truth = set(positive), set(truth)
    tp, fp, fn = len(positive & truth), len(positive-truth), len(truth-positive)
    return {'true_positives': tp, 'false_positives': fp, 'false_negatives': fn,
            'predicted_positives': len(positive), 'truth_positives': len(truth),
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def evaluate_nuscenes_labels(events):
    """Report source-backed event routing without inventing semantic GT labels."""
    rows = [{'event_id': e['event_id'], 'predicted_layer': e['map_layer']['predicted_layer'],
             'classification_applied': e['decision']['classification_applied'],
             'reliability_status': e['reliability']['status']} for e in events]
    return {'scope': 'supplied_occupancy_and_marking_candidate_semantics',
            'label_source': 'Source candidate types and annotation-backed occupancy; no independent structure-versus-state labels',
            'sample_count': len(rows),
            'supported_state_events': sum(r['classification_applied'] and r['predicted_layer'] == 'map_state_event' for r in rows),
            'unverified_structure_hypotheses': sum(not r['classification_applied'] and r['predicted_layer'] == 'base_map_structure' for r in rows),
            'original_duration_labels_reused': False,
            'independent_layer_accuracy': None, 'rows': rows,
            'limitations': ['Source occupancy uses annotation-backed footprints, not rerun camera detection.',
                           'Semantic routing is not proof that the candidate change is real.',
                           'Old temporary/permanent labels are not structure/state ground truth.',
                           'No scene identity or event lifetime is used to choose a map layer.']}


def evaluate_argoverse_labels(raw, prior, events, sources):
    """Evaluate unique physical boundaries, avoiding duplicated alias true hits."""
    package = av._package(raw, 'M8')
    names = ('gt_object_inventory.jsonl', 'gt_changed_objects.jsonl', 'prior_map_reference.json', 'provenance.json')
    av._checked(raw, package, names, 'evaluation_only_after_prediction_freeze', sources)
    if av._read(package/'prior_map_reference.json') != prior:
        raise ValueError('Argoverse truth prior differs from prediction prior')
    inventory = av._rows(package/'gt_object_inventory.jsonl')
    changed_states = av._rows(package/'gt_changed_objects.jsonl')
    if any(r['scene_id'] != prior['scene_id'] for r in inventory):
        raise ValueError('Cross-scene evaluation truth')
    lookup = {(r['layer'], r['element_id']): r for r in inventory}
    if len(lookup) != len(inventory):
        raise ValueError('Duplicate truth object identity')
    boundary_truth, excluded, correspondence = {}, [], {}
    # Scope is chosen using prior geometry/paint and supplied GT identities,
    # before any prediction is matched. Never select a favorable alias by GT.
    for boundary in canonical_boundaries(prior):
        bid, aliases = boundary['physical_boundary_id'], boundary['aliases']
        correspondence[bid] = aliases
        labels = []
        for alias in aliases:
            row = lookup.get(('laneSegments', alias['map_element_id']))
            delta = row.get('differences', {}).get(alias['boundary_side']+'_geometry_difference_m') if row else None
            labels.append(delta > .01 if delta is not None and row['state_comparison_supported'] else None)
        reason = 'PRIOR_NOT_KNOWN_PAINTED_MARKING' if boundary['marking_type'] in {'NONE', 'UNKNOWN', None} else (
            'GT_ALIAS_LABELS_UNRESOLVED_OR_DISAGREE' if None in labels or len(set(labels)) != 1 else None)
        if reason:
            excluded.append({'physical_boundary_id': bid, 'reason': reason})
        else:
            boundary_truth[bid] = labels[0]
    positive_truth = {bid for bid, label in boundary_truth.items() if label}
    supported = [e for e in events if e['reliability']['status'] == 'supported_conflict']
    predicted = {e['physical_boundary_id'] for e in supported if e['physical_boundary_id'] in boundary_truth}
    metrics = _metrics(predicted, positive_truth)
    eligible_events = [e for e in supported if e['physical_boundary_id'] in positive_truth]
    structure_correct = {e['physical_boundary_id'] for e in eligible_events if e['map_layer']['predicted_layer'] == 'base_map_structure'}
    diagnostics = []
    for event in events:
        bid = event['physical_boundary_id']
        diagnostics.append({'event_id': event['event_id'], 'physical_boundary_id': bid,
                            'in_geometry_scope': bid in boundary_truth,
                            'geometry_changed_truth': boundary_truth.get(bid),
                            'aliases': correspondence[bid],
                            'reliability_status': event['reliability']['status'],
                            'layer_prediction': event['map_layer']['predicted_layer'],
                            'layer_truth': 'base_map_structure' if boundary_truth.get(bid) is True else None})
    object_truth = {r['element_id'] for r in inventory if r['official_changed'] and r['state_comparison_supported']}
    # Exact-object metric intentionally excludes multi-reference predictions.
    exact_objects = {e['map_aliases'][0]['map_element_id'] for e in supported if len(e['map_aliases']) == 1}
    baseline_m4 = av._package(raw, 'M4')
    av._checked(raw, baseline_m4, ('change_records.jsonl',), 'baseline_prediction_after_freeze', sources)
    baseline_rows = av._rows(baseline_m4/'change_records.jsonl')
    alias_to_boundary = {(a['map_element_id'], a['boundary_side']): bid for bid, aliases in correspondence.items() for a in aliases}
    baseline_positive = {alias_to_boundary[(r['map_element_id'], r['boundary_side'])] for r in baseline_rows if r['status'] == 'confirmed'}
    changed_geometry_refs = []
    for row in changed_states:
        if row['layer'] != 'laneSegments' or not row['prior_state'] or not row['current_state']:
            continue
        for side in ('left', 'right'):
            if row['differences'].get(side+'_geometry_difference_m', 0) > .01:
                changed_geometry_refs.append({'element_id': row['element_id'], 'side': side,
                    'prior_marking': row['prior_state'][side+'_lane_mark_type'],
                    'current_marking': row['current_state'][side+'_lane_mark_type'],
                    'intersection': row['prior_state']['is_intersection']})
    report = {'scope': 'fixed_prior_painted_physical_boundary_geometry',
            'physical_boundaries': len(correspondence), 'eligible_boundaries': len(boundary_truth),
            'excluded_boundaries': dict(Counter(r['reason'] for r in excluded)),
            'prediction_unit': 'one exact physical boundary; aliases never expand into multiple true positives',
            'new_geometry_metrics': metrics, 'baseline_geometry_metrics': _metrics(baseline_positive & boundary_truth.keys(), positive_truth),
            'whole_supported_map_objects': sum(r['state_comparison_supported'] for r in inventory),
            'whole_map_changed_objects': len(object_truth),
            'coarse_object_change_retrieval': _metrics(exact_objects, object_truth),
            'exact_object_scope_note': 'Coarse any-change object diagnostic on single-reference predictions only, not boundary/type accuracy. Multi-alias output is excluded; main geometry metrics above check the actual claimed boundary.',
            'task_support_breakdown': {'changed_lane_geometry_references': len(changed_geometry_refs),
                'prior_marking_counts': dict(Counter(r['prior_marking'] for r in changed_geometry_refs)),
                'current_marking_counts': dict(Counter(r['current_marking'] for r in changed_geometry_refs)),
                'intersection_references': sum(r['intersection'] for r in changed_geometry_refs),
                'note': 'Reference records can share physical geometry. NONE is unpainted or logical lane geometry, not a missing painted-line measurement; additions and semantic/topology changes require different observations.'},
            'layer_on_true_geometry_hits': {'predictions': len({e['physical_boundary_id'] for e in eligible_events}),
                'correct_structure': len(structure_correct),
                'accuracy': len(structure_correct)/len({e['physical_boundary_id'] for e in eligible_events}) if eligible_events else None,
                'end_to_end_recall': len(structure_correct)/len(positive_truth) if positive_truth else None,
                'label_source': 'Supplied prior/current lane geometry; no lifetime assumption.'},
            'event_diagnostics': diagnostics, 'excluded_details': excluded,
            'visibility_conditioned_accuracy': None, 'independent_test_accuracy': None,
            'limitations': ['Constructed-train development scene; parameters not fitted to these labels.',
                'Whole supplied geometry scope includes unobserved areas; not visibility-conditioned accuracy.',
                'Geometry-only scope excludes additions, topology, lane-use, color and unknown paint references.',
                'Correct layer identity does not cancel a wrong boundary, side, geometry or operation.']}
    report['legacy_painted_scope_is_primary_evaluation'] = False
    report['full_boundary_geometry'] = evaluate_all_boundaries(prior, inventory, changed_states, events)
    return report


def run_pipeline(raw_root, delivery_root, output):
    raw, delivery, out = Path(raw_root).resolve(), Path(delivery_root).resolve(), Path(output).resolve()
    if out.exists():
        raise FileExistsError(out)
    if out.is_relative_to(raw) or out.is_relative_to(delivery):
        raise ValueError('Output must not be inside supplied input directories')
    sources = []
    m2 = av._package(raw, 'M2')
    av._checked(raw, m2, av.M2_FILES, 'frozen_prediction_input', sources)
    scene_id, tables = av._identity(m2)
    prior = av._read(m2/'prior_map.json')
    reliability_policy = ReliabilityPolicy()
    out.mkdir(parents=True)
    method = {'reliability': asdict(reliability_policy),
              'layer_routing': {'algorithm': 'semantic_ontology_and_change_operation',
                  'classes': ['base_map_structure', 'map_state_event'],
                  'duration_used_for_classification': False,
                  'dataset_name_used_for_classification': False,
                  'mixed_changes': 'split structural and operational claims'},
              'write_policy': {'requires': ['supported conflict', 'causal positive observation',
                  'exact target or verified novel identity', 'verified alignment',
                  'verified visibility', 'operation-specific edit evidence'],
                  'operation_requirements': {
                      'geometry_change': 'verified complete replacement geometry',
                      'add': 'verified novel identity and complete geometry',
                      'marking_change': 'defined marking value and independently verified property evidence; no replacement geometry',
                      'delete': 'fully observed causal support for absence on the same target; no replacement geometry'},
                  'shared_boundary_aliases': 'physical identity may be known; synchronized edits of all references remain unverified',
                  'minimum_duration_s': None, 'minimum_independent_visits': None},
              'structural_evidence_revision': 'geometry_detection_without_paint_veto_and_separate_reference_replay_20261108',
              'nuscenes_confirmation_revision': 'computed_local_model_decision_passthrough_and_separate_status_denominators_20261108',
              'confirmation_contract': {
                  'model_detection': 'Computed local geometric decision; independent of map-write eligibility.',
                  'reference_confirmation': 'Delivered M8 labels and current states, read only after prediction freeze.',
                  'execution': 'Known reference changes replay into an isolated map copy; model geometry is not promoted to a full-map patch.'},
              'policy_selection': 'Semantic target and change operation; geometric quality defaults fixed before evaluation',
              'parameters_learned': False, 'calibrated_probabilities': False,
              'scope': 'Frozen perception inputs; structure/state routing, not a duration classifier'}
    av._write(out/'method.json', method)
    nu_scene = integrate_m3(raw, delivery, raw/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
    nu_input = evaluate_nuscenes(nu_scene)
    for event in nu_input['events']:
        event['coordinate_reference'] = nu_scene['coordinate_reference']
    argo_input = evaluate_argoverse(prior, tables['observations'], tables['frames'], reliability_policy)
    for event in argo_input['events']:
        event['coordinate_reference'] = {'frame': 'Argoverse supplied city XY', 'scene_id': scene_id, 'units': 'm'}
    nu, argo = classify_events(nu_input), classify_events(argo_input)
    nu['scene_id'], argo['scene_id'] = nu_scene['scene_id'], scene_id
    frozen_names = ['method.json']
    for dataset, data in [('nuscenes', nu), ('argoverse', argo)]:
        for name, value in [(dataset+'_predictions.json', data),
                            (dataset+'_state_layer.geojson', data['state_event_layer'])]:
            av._write(out/name, value)
            frozen_names.append(name)
    for name in ('pipeline_v2.py', 'nuscenes_v2.py', 'local_correspondence.py', 'reliability_v2.py', 'map_layers.py',
                 'structure_evaluation.py', 'reference_replay.py', 'nuscenes_status_audit.py',
                 'state_events.py', 'persistence.py', 'engine.py', 'reliability.py',
                 'scene.py', 'delivery.py', 'automatic.py', 'argoverse.py'):
        path = Path(__file__).with_name(name)
        sources.append({'path': 'map_update/'+name, 'sha256': av._hash(path), 'role': 'executed_code'})
    sources.extend({'path': r.get('path', r.get('file', '')), 'sha256': r['sha256'], 'role': 'nuscenes_prediction_input'}
                   for r in nu_scene.get('source_files', []) if 'sha256' in r)
    handoff = raw/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json'
    sources.append({'path': handoff.relative_to(raw).as_posix(), 'sha256': av._hash(handoff),
                    'role': 'nuscenes_legacy_handoff'})
    for name in ('manifest.json', 'outputs/stage3/input_hashes.json'):
        sources.append({'path': 'external/perception_delivery/'+name,
                        'sha256': av._hash(delivery/name), 'role': 'nuscenes_checked_delivery_manifest'})
    freeze = {'gt_read': False, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'files_sha256': {name: av._hash(out/name) for name in frozen_names}}
    av._write(out/'prediction_freeze.json', freeze)
    evaluation = {'nuscenes': evaluate_nuscenes_labels(nu['events']),
                  'argoverse': evaluate_argoverse_labels(raw, prior, argo['events'], sources)}
    from .nuscenes_status_audit import build_nuscenes_status_audit
    nu_audit = build_nuscenes_status_audit(raw, delivery, nu['events'])
    evaluation['nuscenes']['status_audit'] = nu_audit
    for source in nu_audit['source_files']:
        prefix = 'external/perception_delivery/' if source['root'] == 'delivery_root' else ''
        sources.append({'path': prefix + source['path'], 'sha256': source['sha256'],
                        'role': 'nuscenes_status_audit_only_after_prediction_freeze'})
    if any(av._hash(out/name) != digest for name, digest in freeze['files_sha256'].items()):
        raise ValueError('Prediction outputs changed during evaluation')
    # Trusted reference import is an independent post-freeze replay, never a
    # detector input or a replacement for the frozen model prediction files.
    from .reference_replay import build_reference_replay
    m8 = av._package(raw, 'M8')
    replay = build_reference_replay(prior, av._rows(m8/'gt_changed_objects.jsonl'),
                                   av._rows(m8/'gt_object_inventory.jsonl'),
                                   provenance={'source_files': [s for s in sources if '_M8_' in s['path']],
                                               'prediction_freeze_sha256': av._hash(out/'prediction_freeze.json'),
                                               'used_for_model_prediction': False})
    argo['reference_replay'] = replay
    for filename, value in [('argoverse_reference_updated_map.json', replay['updated_map']),
                            ('argoverse_reference_rollback_map.json', replay['rollback_map']),
                            ('argoverse_reference_patch_log.json', replay['patch_log']),
                            ('argoverse_reference_changes.geojson', replay['change_layer'])]:
        av._write(out/filename, value)
    if any(av._hash(out/name) != digest for name, digest in freeze['files_sha256'].items()):
        raise ValueError('Prediction outputs changed during reference replay')
    comparisons = [
        {'dataset_id': 'nuscenes', 'baseline': {'explicit_structure_state_classifier_run': False,
            'recorded_occupancy_events': 23}, 'new': nu['summary'], 'unit': 'same 29 delivered source candidates'},
        {'dataset_id': 'argoverse', 'baseline': {'single_frame_candidates': 4, 'fused_uncertain_groups': 1,
            'confirmed_changes': 0, 'explicit_structure_state_classifier_run': False}, 'new': argo['summary'],
         'unit': 'old M3 candidate groups versus new physical-boundary windows; use fixed-scope metrics for accuracy comparison'},
    ]
    result = {'schema': 'change-pipeline-v3', 'run_id': 'robust_geometry_structure_state_v3',
              'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'method': method,
              'datasets': {'nuscenes': nu, 'argoverse': argo}, 'evaluation': evaluation,
              'comparison': comparisons, 'sources': sources, 'prediction_freeze': freeze,
              'limitations': ['Map layer is classified by target semantics, not observed lifetime.',
                  'Routing is an explicit semantic classifier, not a trained or calibrated ML model.',
                  'Reliability describes observed-segment conflict, not correctness of the full map.',
                  'A structural candidate needs a verified full edit before base-map mutation.',
                  'Missing observations and TTL expiry are not observed recovery.']}
    av._write(out/'result.json', result)
    return out
