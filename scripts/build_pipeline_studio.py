"""Build a bilingual before/after map comparison from saved source evidence."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.map_context import build_map_contexts


def build(result_path, output_path, raw_root=None, delivery_root=None):
    result_path, output = Path(result_path), Path(output_path)
    data = json.loads(result_path.read_text())
    if data.get('schema') != 'change-pipeline-v3':
        raise ValueError('A change-pipeline-v3 structure/state result is required')
    for dataset in ['nuscenes', 'argoverse']:
        if dataset not in data.get('datasets', {}):
            raise ValueError('Missing dataset result: ' + dataset)
        for field in ['summary', 'events']:
            if field not in data['datasets'][dataset]:
                raise ValueError('Missing dataset field: ' + field)
    if any('map_layer' not in event for dataset in data['datasets'].values() for event in dataset['events']):
        raise ValueError('Every displayed event requires a map-layer classification')
    for name, dataset in data['datasets'].items():
        decisions = [event.get('final_structural_decision', {}) for event in dataset['events']]
        if any(decision.get('processing_status') != 'complete'
               or decision.get('action') not in {'update', 'no_update', 'state_event'}
               or decision.get('revisit_required') is not False
               or decision.get('manual_review_required') is not False
               or decision.get('evidence_scope') != 'supplied_recording'
               or decision.get('decision_is_ground_truth') is not False
               or (decision.get('action') == 'update' and not decision.get('material_change_supported'))
               for decision in decisions):
            raise ValueError('Every displayed event requires a completed recording-only map decision: ' + name)
        summary = dataset['summary']
        if (summary.get('final_structural_changes') != sum(d['action'] == 'update' for d in decisions)
                or summary.get('closed_without_structural_update') != sum(
                    d['action'] == 'no_update' and d.get('structural_scope', False) for d in decisions)
                or summary.get('pending_structural_decisions') != 0
                or summary.get('revisit_requests') != 0):
            raise ValueError('Final decision counts disagree with event decisions: ' + name)
    contexts = build_map_contexts(raw_root or ROOT/'AIE1903 原始资料',
                                  delivery_root or ROOT/'external/perception_delivery')
    guide_path = ROOT/'docs/perception_labels_20261108.json'
    guide = json.loads(guide_path.read_text())
    prediction_sources = {item['path']: item['sha256'] for item in data['sources']}
    for name, details in guide['datasets'].items():
        if details['scene_id'] != data['datasets'][name]['scene_id']:
            raise ValueError('Perception guide and prediction scene differ: ' + name)
        for source in details['source_files']:
            path = (ROOT/source['path']).resolve()
            if not path.is_relative_to(ROOT) or sha256(path.read_bytes()).hexdigest() != source['sha256']:
                raise ValueError('Perception guide source identity differs: ' + source['path'])
            prediction_path = source['path'].removeprefix('AIE1903 原始资料/')
            if prediction_path in prediction_sources and prediction_sources[prediction_path] != source['sha256']:
                raise ValueError('Perception guide differs from prediction inputs: ' + source['path'])
    for name, context in contexts.items():
        if context['scene_id'] != data['datasets'][name]['scene_id']:
            raise ValueError('Map context and prediction scene differ: ' + name)
        if name == 'nuscenes':
            expected_hash = data['datasets'][name]['provenance']['map_sha256']
        else:
            matches = [item['sha256'] for item in data['sources']
                       if item['role'] == 'frozen_prediction_input' and item['path'].endswith('/prior_map.json')]
            if len(matches) != 1:
                raise ValueError('A unique frozen Argoverse prior map is required')
            expected_hash = matches[0]
        if context['provenance']['map_sha256'] != expected_hash:
            raise ValueError('Map context differs from the map used for prediction: ' + name)
    audit = data.get('evaluation', {}).get('nuscenes', {}).get('status_audit')
    if audit:
        nu = data['datasets']['nuscenes']
        if audit.get('scene_id') != nu['scene_id']:
            raise ValueError('nuScenes status audit belongs to a different scene')
        roots = {'raw_root': Path(raw_root or ROOT/'AIE1903 原始资料').resolve(),
                 'delivery_root': Path(delivery_root or ROOT/'external/perception_delivery').resolve()}
        for source in audit.get('source_provenance', []):
            parent = roots.get(source.get('root'))
            if parent is None:
                raise ValueError('Unknown nuScenes audit source root')
            path = (parent/source['path']).resolve()
            if (not path.is_relative_to(parent) or not path.is_file()
                    or sha256(path.read_bytes()).hexdigest() != source['sha256']):
                raise ValueError('nuScenes status audit source identity differs')
        events = nu['events']
        def semantic_layer(event):
            layer = event['map_layer']
            return layer.get('conditional_layer_prediction', 'unresolved') if layer['predicted_layer'] == 'not_applicable' else layer['predicted_layer']
        structural = [e for e in events if semantic_layer(e) == 'base_map_structure']
        routing, quality, writes = audit['semantic_routing'], audit['structural_quality'], audit['structural_write']
        expected_routing = {'total_candidates': len(events),
                            'state_events': sum(semantic_layer(e) == 'map_state_event' for e in events),
                            'structural_candidates': len(structural),
                            'unassigned': sum(semantic_layer(e) not in {'map_state_event', 'base_map_structure'} for e in events)}
        expected_quality = {'total_candidates': len(structural),
                            'detected': sum(e.get('model_change_decision', {}).get('detected') is True for e in structural),
                            'insufficient_evidence': sum(e['reliability']['status'] == 'insufficient_geometry' for e in structural),
                            'geometrically_consistent': sum(e['reliability']['status'] == 'consistent_with_map' for e in structural)}
        expected_writes = {'total_candidates': len(structural),
                           'eligible': sum(e['decision']['write_gate']['eligible'] for e in structural),
                           'executed': sum(e['decision']['write_gate']['map_patch_executed'] for e in structural)}
        if (any(routing.get(k) != v for k, v in expected_routing.items())
                or any(quality.get(k) != v for k, v in expected_quality.items())
                or any(writes.get(k) != v for k, v in expected_writes.items())
                or {r['candidate_id'] for r in audit['candidate_diagnostics']} != {e['event_id'] for e in structural}):
            raise ValueError('nuScenes status audit denominator or decisions disagree with predictions')
        fraction = expected_quality['insufficient_evidence']/len(structural) if structural else None
        if quality.get('insufficient_fraction') != fraction:
            raise ValueError('nuScenes status audit uncertainty fraction has the wrong denominator')
    replay = data['datasets']['argoverse'].get('reference_replay')
    material_replay = data['datasets']['argoverse'].get('material_reference_replay')
    for current_replay in [replay, material_replay]:
        if not current_replay:
            continue
        checked_replay = current_replay
        if (checked_replay.get('source') != 'delivered_ground_truth'
                or checked_replay.get('confirmation_source') != 'reference'
                or checked_replay.get('scene_id') != data['datasets']['argoverse']['scene_id']):
            raise ValueError('Reference replay provenance or scene identity differs')
        changes, features = checked_replay['changes'], checked_replay['change_layer']['features']
        change_ids = {change['change_id'] for change in changes}
        feature_ids = {feature['properties']['change_id'] for feature in features}
        if (len(change_ids) != len(changes) or change_ids != feature_ids
                or len(features) != checked_replay['summary']['confirmed_changes']
                or len(changes) != checked_replay['summary']['executed_patches']
                or any(not feature['properties'].get('confirmed')
                       or feature['properties'].get('confirmation_source') != 'reference'
                       for feature in features)):
            raise ValueError('Reference replay changes, layer and execution counts disagree')
        if checked_replay['updated_map_context']['scene_id'] != checked_replay['scene_id']:
            raise ValueError('Updated reference map context has a different scene')
    if replay and not material_replay:
        raise ValueError('The main map requires a materiality-filtered reference replay')
    if material_replay:
        decisions = material_replay['materiality_decisions']
        selected_ids = {d['change_id'] for d in decisions if d['material']}
        actual_ids = {change['change_id'] for change in material_replay['changes']}
        complete_ids = {change['change_id'] for change in replay['changes']}
        summary = material_replay['summary']
        if (selected_ids != actual_ids
                or len(decisions) != len(complete_ids)
                or {d['change_id'] for d in decisions} != complete_ids
                or summary.get('reference_total_changes') != len(complete_ids)
                or summary.get('accepted_material_changes') != len(actual_ids)
                or summary.get('skipped_minor_changes') != len(complete_ids) - len(actual_ids)
                or summary.get('revisit_required') is not False
                or any(d['final_action'] != ('update_structure' if d['material'] else 'no_update') for d in decisions)):
            raise ValueError('Material reference decisions disagree with original and updated replay')
    if output.exists():
        raise ValueError('Choose a new output directory')
    vendor_root = ROOT/'map_update/vendor'
    vendor_manifest = json.loads((vendor_root/'three.manifest.json').read_text())
    for name in ['three.min.js', 'three.LICENSE']:
        entry = vendor_manifest['files'][name]
        content = (vendor_root/name).read_bytes()
        if sha256(content).hexdigest() != entry['sha256'] or len(content) != entry['bytes']:
            raise ValueError('Local Three.js distribution differs from its pinned manifest: ' + name)
    presentation_assets = ['pipeline_studio.css', 'pipeline_studio.js', 'pipeline_map_3d.js']
    if any(not (ROOT/'map_update'/name).is_file() for name in presentation_assets):
        raise ValueError('The 3D presentation renderer and 2D fallback assets must both be available')
    (output / 'assets').mkdir(parents=True)
    shutil.copy2(result_path, output / 'result.json')
    for name, dataset in data['datasets'].items():
        filename = name + '_state_layer.geojson'
        original = result_path.parent / filename
        if original.exists() and json.loads(original.read_text()) == dataset['state_event_layer']:
            shutil.copy2(original, output / filename)
        else:
            (output / filename).write_text(json.dumps(dataset['state_event_layer'], ensure_ascii=False, indent=2) + '\n')
    for prefix, current_replay in [('argoverse_reference', replay), ('argoverse_material_reference', material_replay)]:
        if not current_replay:
            continue
        exports = [
            (prefix + '_updated_map.json', current_replay['updated_map']),
            (prefix + '_rollback_map.json', current_replay['rollback_map']),
            (prefix + '_patch_log.json', current_replay['patch_log']),
            (prefix + '_changes.geojson', current_replay['change_layer']),
        ]
        if 'materiality_decisions' in current_replay:
            exports.append((prefix + '_decisions.json', current_replay['materiality_decisions']))
        for filename, value in exports:
            original = result_path.parent / filename
            if original.exists() and json.loads(original.read_text()) == value:
                shutil.copy2(original, output / filename)
            else:
                (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    for name in presentation_assets:
        shutil.copy2(ROOT/'map_update'/name, output/'assets'/name)
    (output/'assets/vendor').mkdir()
    for name in ['three.min.js', 'three.LICENSE', 'three.manifest.json', 'README.md']:
        shutil.copy2(vendor_root/name, output/'assets/vendor'/name)
    template = (ROOT / 'map_update/pipeline_studio.html').read_text()
    view = deepcopy(data)
    view['perception_guide'] = guide
    shutil.copy2(guide_path, output/'perception_guide.json')
    for name, dataset in view['datasets'].items():
        dataset['map_context'] = contexts[name]
        (output/(name+'_map_context.json')).write_text(json.dumps(contexts[name], ensure_ascii=False, indent=2)+'\n')
        dataset.pop('observation_decisions', None)
        for replay_key in ['reference_replay', 'material_reference_replay']:
            if dataset.get(replay_key):
                dataset[replay_key].pop('updated_map', None)
                dataset[replay_key].pop('rollback_map', None)
        for event in dataset['events']:
            for sample in event.get('samples', []):
                sample.pop('geometry_city', None)
    view['presentation'] = {'kind': 'derived_view', 'source_result_sha256': sha256(result_path.read_bytes()).hexdigest(),
        'omitted_fields': ['datasets.*.observation_decisions', 'datasets.*.events.*.samples.*.geometry_city', 'datasets.*.reference_replay.updated_map', 'datasets.*.reference_replay.rollback_map', 'datasets.*.material_reference_replay.updated_map', 'datasets.*.material_reference_replay.rollback_map'],
        'added_fields': ['datasets.*.map_context', 'perception_guide'],
        'map_context_source': 'Prediction contexts retain frozen priors. The reference view uses the separate map copy updated only by material supplied changes. The complete reference replay remains an audit artifact.',
        'three_dimensional_view': {
            'renderer': 'Three.js', 'version': vendor_manifest['version'],
            'dependency_source': 'local pinned bundle; no runtime CDN or network dependency',
            'source_xy_geometry_preserved': True,
            'ground_elevation': 'flat display plane; no measured terrain reconstruction',
            'vertical_extrusion': 'schematic visual aid, not measured object or building height',
            'before_after_camera': 'one shared camera with screen-space clipping',
            'views': ['perspective', 'top_down', '2d_fallback'],
            'webgl_failure_action': 'retain interactive 2D map',
            'prediction_results_modified': False},
        'comparison_contract': {
            'left_view': 'original_reference_map', 'right_view': 'updated_presentation',
            'divider_leftmost': 'full_updated_presentation',
            'divider_rightmost': 'full_original_reference_map',
            'shared_coordinate_projection': True,
            'colors': {'map_state_event': 'yellow', 'confirmed_structure': 'red',
                       'closed_no_update_diagnostic': 'gray'},
            'confirmation': 'Model view: completed final_structural_decision with update action and material_change_supported. Reference view: supplied truth passing the shared materiality policy, independent of model output.',
            'reference_replay_is_prediction': False,
            'nuscenes_source': 'Annotation-backed state events and completed material structural update decisions; raw local diagnostics remain in the evidence details',
            'nuscenes_uncertainty_denominators_separate': bool(audit),
            'argoverse_default_view': 'reference' if replay else 'model',
            'updated_view_includes_unconfirmed_candidates': False,
            'closed_no_update_has_change_overlay': False,
            'revisit_required': False,
            'reference_view_uses_material_subset': bool(material_replay),
            'source_base_map_mutated': False},
        'event_geometry_layer_classification_and_lifecycle_preserved': True}
    serialized = json.dumps(view, ensure_ascii=False, allow_nan=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    asset_hashes = {path.relative_to(output/'assets').as_posix(): sha256(path.read_bytes()).hexdigest()
                    for path in sorted((output/'assets').rglob('*')) if path.is_file()}
    revision = sha256(json.dumps(asset_hashes, sort_keys=True).encode()).hexdigest()[:12]
    for language in ['en', 'zh']:
        route = output / language
        route.mkdir()
        (route / 'index.html').write_text(template.replace('__LANG__', language).replace('__RESULT__', serialized).replace('__REVISION__', revision))
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=en/index.html"><a href="en/index.html">Map-layer pipeline</a>\n')
    (output / 'version.json').write_text(json.dumps({'schema': 'change-pipeline-studio-4.0', 'result_sha256': sha256(result_path.read_bytes()).hexdigest(), 'asset_revision': revision,
        'asset_sha256': asset_hashes,
        'three_dimensional_view': view['presentation']['three_dimensional_view'],
        'map_context_sha256': {name: sha256((output/(name+'_map_context.json')).read_bytes()).hexdigest() for name in contexts},
        'perception_guide_sha256': sha256(guide_path.read_bytes()).hexdigest()}, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--raw-root', type=Path)
    parser.add_argument('--delivery-root', type=Path)
    args = parser.parse_args()
    print(build(args.result, args.out, args.raw_root, args.delivery_root))
