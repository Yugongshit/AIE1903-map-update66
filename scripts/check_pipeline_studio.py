"""Browser checks of structure/state classification, lifecycle and evaluation scope."""
import argparse
from functools import partial
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
root = parser.parse_args().directory.resolve()
checks, errors, failures = [], [], []
source = json.loads((root / 'result.json').read_text())


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check(name, result):
    if not result:
        raise AssertionError(name)
    checks.append(name)
    print('PASS ' + name, flush=True)


def download(page, selector):
    with page.expect_download() as event:
        page.locator(selector).click()
    return Path(event.value.path()).read_bytes()


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}/'
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1500, 'height': 1150})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: failures.append([response.url, response.status]) if response.status >= 400 else None)
        page.goto(base + 'en/index.html?view=2d')
        page.wait_for_function('window.PIPELINE_STUDIO')
        view = page.evaluate('PIPELINE_STUDIO.data')
        baseline = page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)')
        check('technical evidence starts collapsed behind the map comparison', page.locator('#advanced-analysis').get_attribute('open') is None)
        page.locator('#map-view-evidence').click()
        check('the selected-map evidence action opens the actual analysis panels', page.locator('#advanced-analysis').get_attribute('open') is not None and page.locator('#selected-id').is_visible())
        check('v3 presentation identifies its source hash and derived-view omissions', view['schema'] == 'change-pipeline-v3' and view['presentation']['source_result_sha256'] == sha256((root / 'result.json').read_bytes()).hexdigest() and view['presentation']['event_geometry_layer_classification_and_lifecycle_preserved'] is True and all('observation_decisions' not in value for value in view['datasets'].values()))
        check('all event geometry, typed hypotheses, classifications, decisions and evidence are preserved', all(
            event == {**original, 'samples': [{key: value for key, value in sample.items() if key != 'geometry_city'} for sample in original['samples']]}
            for name in source['datasets'] for event, original in zip(view['datasets'][name]['events'], source['datasets'][name]['events'])
        ) and all(len(view['datasets'][name]['events']) == len(source['datasets'][name]['events']) for name in source['datasets']))
        check('actual state layers, evaluation and method are preserved unchanged', all(view['datasets'][name]['state_event_layer'] == source['datasets'][name]['state_event_layer'] for name in source['datasets']) and view['evaluation'] == source['evaluation'] and view['method'] == source['method'])
        check('all supplied candidates have a complete action with no revisit or manual-review queue',
              all(event['final_structural_decision']['processing_status'] == 'complete'
                  and event['final_structural_decision']['action'] in {'update', 'no_update', 'state_event'}
                  and not event['final_structural_decision']['revisit_required']
                  and not event['final_structural_decision']['manual_review_required']
                  and not event['final_structural_decision']['decision_is_ground_truth']
                  for dataset in source['datasets'].values() for event in dataset['events'])
              and all(dataset['summary']['pending_structural_decisions'] == 0
                      and dataset['summary']['revisit_requests'] == 0 for dataset in source['datasets'].values()))
        check('nuScenes keeps 29 source events while displaying 23 state events and no material structural updates', page.locator('#events [data-event]').count() == 29 and page.locator('#map-count-state').inner_text() == '23' and page.locator('#map-count-confirmed').inner_text() == '0' and source['datasets']['nuscenes']['summary']['reliability_counts'] == {'supported_conflict': 24, 'insufficient_geometry': 5})
        audit = source['evaluation']['nuscenes']['status_audit']
        check('nuScenes status audit keeps known object annotations separate from structural evidence',
              page.locator('#nuscenes-status-audit').is_visible()
              and audit['annotation_categories']['total_annotations'] == 2278
              and audit['annotation_categories']['known_category_annotations'] == 2278
              and audit['annotation_categories']['unknown_category_annotations'] == 0
              and audit['annotation_categories']['unique_instances'] == 154
              and page.locator('#nuscenes-annotation').inner_text() == '2,278 / 2,278')
        check('every delivered candidate has a semantic map layer without being declared a verified change',
              audit['semantic_routing']['total_candidates'] == 29
              and audit['semantic_routing']['state_events'] == 23
              and audit['semantic_routing']['structural_candidates'] == 6
              and audit['semantic_routing']['unassigned'] == 0
              and '23' in page.locator('#nuscenes-routing').inner_text()
              and '6' in page.locator('#nuscenes-routing').inner_text()
              and '0 unassigned / 29' in page.locator('#nuscenes-routing').locator('..').inner_text())
        structural = [event for event in source['datasets']['nuscenes']['events'] if event['map_layer']['predicted_layer'] == 'base_map_structure']
        check('all six nuScenes structural candidates close without an edit or another observation request',
              len(structural) == 6
              and all(event['final_structural_decision']['processing_status'] == 'complete'
                      and event['final_structural_decision']['action'] == 'no_update'
                      and not event['final_structural_decision']['material_change_supported']
                      and not event['final_structural_decision']['revisit_required']
                      and not event['final_structural_decision']['manual_review_required']
                      and event['final_structural_decision']['evidence_scope'] == 'supplied_recording'
                      and not event['final_structural_decision']['decision_is_ground_truth'] for event in structural)
              and '6 / 6' in page.locator('#nuscenes-structure-abstention').inner_text()
              and page.locator('#map-count-closed').inner_text() == '6')
        check('raw local diagnostics remain separate from the completed structural decision',
              audit['structural_quality']['detected'] == 1
              and audit['structural_quality']['insufficient_evidence'] == 5
              and audit['structural_quality']['total_candidates'] == 6
              and source['datasets']['nuscenes']['summary']['pending_structural_decisions'] == 0
              and source['datasets']['nuscenes']['summary']['revisit_requests'] == 0)
        page.locator('#nuscenes-candidate-audit > summary').click()
        check('object motion uncertainty retains its own 154-instance denominator and extra insufficient-observation category',
              audit['object_motion_states']['total_instances'] == 154
              and audit['object_motion_states']['uncertain_count'] == 3
              and audit['object_motion_states']['insufficient_observations_count'] == 10
              and abs(audit['object_motion_states']['uncertain_fraction'] - 3 / 154) < 1e-12
              and '3/154' in page.locator('#nuscenes-motion-note').inner_text()
              and '1.95%' in page.locator('#nuscenes-motion-note').inner_text()
              and '10' in page.locator('#nuscenes-motion-note').inner_text())
        check('local structure detection does not turn into a fabricated executed map patch',
              audit['structural_write']['total_candidates'] == 6
              and audit['structural_write']['eligible'] == audit['structural_write']['executed'] == 0
              and '0 / 6' in page.locator('#nuscenes-write').inner_text()
              and audit['labels_used_for_model_prediction'] is False)
        page.locator('#nuscenes-candidate-audit > summary').click()
        check('main classifier uses structure/state semantics and makes no trained-probability claim', 'Duration does not determine the map layer' in page.locator('#model-boundary').inner_text() and 'explicit semantic evidence' in page.locator('#model-boundary').inner_text() and source['method']['parameters_learned'] is False)
        check('primary UI contains no duration forecast, posterior display or minimum revisit threshold', page.locator('#trace-chart').count() == 0 and page.locator('#sensitivity').count() == 0 and 'Temporary forecast' not in page.locator('main').inner_text() and 'Long-term forecast' not in page.locator('main').inner_text() and '24 h' not in page.locator('main').inner_text() and source['method']['write_policy']['minimum_duration_s'] is None and source['method']['write_policy']['minimum_independent_visits'] is None)
        check('occupancy reliability preserves annotation provenance instead of claiming new detection', 'supplied annotation' in page.locator('#reliability-note').inner_text() and 'No new camera detector' in page.locator('#reliability-note').inner_text() and '1.00' in page.locator('#reliability-values').inner_text())
        check('barrier is classified as a state event and its real layer record is stored', page.locator('#layer-status').inner_text() == 'Map-state event' and page.locator('#write-status').inner_text() == 'State-layer record stored' and page.evaluate('PIPELINE_STUDIO.stateRecord(PIPELINE_STUDIO.current()).properties.event_id') == page.evaluate('PIPELINE_STUDIO.state.eventId'))
        check('routing highlight matches the actual semantic classifier output', page.locator('#routing-path article.active').get_attribute('data-layer') == 'map_state_event')
        page.locator('.matched-evidence summary').click()
        check('semantic evidence retains the source barrier category and matched input fields', 'movable_object.barrier' in page.locator('#matched-evidence').inner_text() and 'evidence.categories' in page.locator('#matched-evidence').inner_text() and 'semantic_kind' in page.locator('#matched-evidence').inner_text())
        check('numerical class confidence remains unavailable instead of invented probability', 'Not supplied' in page.locator('#layer-values').inner_text() and page.evaluate('PIPELINE_STUDIO.current().map_layer.confidence.value') is None)
        check('missing clip-end observation does not erase state classification or confirm clearance', 'State unobserved at clip end' in page.locator('#lifecycle').inner_text() and 'Map-state event' in page.locator('#layer-status').inner_text() and page.evaluate('PIPELINE_STUDIO.stateRecord(PIPELINE_STUDIO.current()).properties.clearance_observed') is False)
        check('lifecycle uses actual timestamps without inventing a default TTL', 'None supplied' in page.locator('#lifecycle').inner_text() and '1537845359197055000' in page.locator('#lifecycle').inner_text() and page.evaluate('PIPELINE_STUDIO.stateRecord(PIPELINE_STUDIO.current()).properties.expires_at_timestamp_ns') is None)
        check('state geometry comes from the saved last-observed snapshot', page.locator('#geometry polyline').count() == page.evaluate('PIPELINE_STUDIO.geometryLines(PIPELINE_STUDIO.stateRecord(PIPELINE_STUDIO.current()).geometry).length') and 'current extent is not verified' in page.locator('#geometry-box').inner_text())
        state_export = json.loads(download(page, '#state-download'))
        check('downloaded state layer is the actual 23-feature GeoJSON with unchanged base-map flags', state_export == source['datasets']['nuscenes']['state_event_layer'] and len(state_export['features']) == 23 and all(feature['properties']['base_map_modified'] is False for feature in state_export['features']))
        page.locator('#filter').select_option('map_state_event')
        check('state filter includes all 23 occupancy events', page.locator('#events [data-event]').count() == 23)
        page.locator('#filter').select_option('base_map_structure')
        check('six marking hypotheses remain visible without being accepted as verified edits', page.locator('#events [data-event]').count() == 6)
        page.locator('#events [data-event]').first.click()
        check('closed structural diagnostics expose a final no-update decision without an active map overlay', page.locator('#layer-status').inner_text() == 'Base-map structure' and page.locator('#final-decision-title').is_visible() and page.locator('#final-decision-note').inner_text().strip() and page.evaluate('PIPELINE_STUDIO.current().final_structural_decision.action') == 'no_update' and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False and page.evaluate('PIPELINE_STUDIO.map.features(false).filter(f => f.layer === "structure").length') == 0)
        check('structural hypothesis does not create a state record or a base patch', not page.locator('#lifecycle').is_visible() and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.map_patch_executed') is False)
        if page.locator('#nuscenes-candidate-audit').get_attribute('open') is None:
            page.locator('#nuscenes-candidate-audit > summary').click()
        check('all six structural candidate diagnostics preserve their source windows and separate calculated outcomes',
              page.locator('[data-nuscenes-candidate]').count() == len(audit['candidate_diagnostics']) == 6
              and {button.get_attribute('data-nuscenes-candidate') for button in page.locator('[data-nuscenes-candidate]').all()} == {row['candidate_id'] for row in audit['candidate_diagnostics']}
              and all(row['source_observed_frames'] == row['model_recorded_frames'] for row in audit['candidate_diagnostics'])
              and sum(row['model_detected'] for row in audit['candidate_diagnostics']) == 1)
        page.locator('[data-nuscenes-candidate="S0980-LD-004"]').click()
        check('the audit candidate control selects the same source event on the map and in technical evidence',
              page.evaluate('PIPELINE_STUDIO.state.eventId') == 'S0980-LD-004'
              and page.locator('#selected-id').inner_text() == 'S0980-LD-004')
        ld004 = page.evaluate('PIPELINE_STUDIO.current()')
        check('LD004 preserves its raw local detection but closes without a material structural edit',
              page.locator('#reliability-status').inner_text() == 'Local conflict supported'
              and ld004['reliability']['status'] == 'supported_conflict'
              and ld004['change_type'] == 'line_discrepancy'
              and ld004['structural_hypothesis']['operation'] == 'unresolved_parallel_marking_or_insertion'
              and ld004['structural_hypothesis']['confirmed'] is False
              and ld004['final_structural_decision']['action'] == 'no_update'
              and not ld004['final_structural_decision']['material_change_supported']
              and ld004['structural_hypothesis']['operation_resolved'] is False
              and ld004['model_change_decision']['detected'] is True
              and ld004['model_change_decision']['scope'] == 'local_boundary_correspondence'
              and ld004['decision']['classification_applied'] is False
              and ld004['decision']['base_map_write_eligible'] is False
              and ld004['decision']['write_gate']['map_patch_executed'] is False)
        check('LD004 uses its exact 27–33 source window and six fixed-anchor supporting frames',
              ld004['correspondence_policy']['window'] == [27, 33]
              and [int(sample['frame_id']) for sample in ld004['samples']] == list(range(27, 34))
              and ld004['distinct_frame_count'] == 6
              and [int(sample['frame_id']) for sample in ld004['samples'] if sample.get('measured_local_relation') == 'offset_negative'] == list(range(28, 34)))
        concurrent = ld004['same_window_reference_observations']
        check('same-window reference observations are retained as counterevidence to an old-line movement claim',
              [int(row['frame_id']) for row in concurrent] == list(range(28, 34))
              and all(abs(row['residual_m']) < .25 and row['source_ids'] for row in concurrent)
              and page.locator('#same-window-evidence').is_visible()
              and [int(cell.inner_text()) for cell in page.locator('#same-window-evidence tbody tr td:first-child').all()] == list(range(28, 34))
              and 'does not support a claim that the old marking disappeared or moved' in page.locator('#same-window-evidence').inner_text())
        check('LD004 details separate local geometric support from paint identity, alignment and structural truth',
              page.locator('#final-decision-title').is_visible()
              and page.locator('#final-decision-reasons').inner_text().strip()
              and page.evaluate('PIPELINE_STUDIO.map.isConfirmedStructure(PIPELINE_STUDIO.current())') is False
              and 'Paint identity' in page.locator('#observation-dimensions').inner_text()
              and 'Physical alignment' in page.locator('#observation-dimensions').inner_text()
              and ld004['observation_evidence']['change_assessment']['map_edit_verified'] is False
              and ld004['observation_evidence']['change_assessment']['model_detected_local_change'] is True
              and 'no independent structural truth label' in page.locator('#selected-evaluation').inner_text()
              and source['prediction_freeze']['gt_read'] is False)
        page.locator('#local-measurements > summary').click()
        check('LD004 exposes its six measured residuals separately from downstream decisions',
              page.locator('#local-measurement-rows tbody tr').count() == 6
              and 'Negative signed offset' in page.locator('#local-measurement-rows').inner_text())
        check('unresolved local discrepancy has no executable operation and no fabricated complete-edit geometry',
              ld004['decision']['write_gate']['edit_scope'] == 'unsupported_operation'
              and 'NO_STRUCTURAL_EDIT_OPERATION' in ld004['decision']['write_gate']['reasons']
              and 'No executable structural operation' in page.locator('#edit-contract-values').inner_text()
              and 'No structural patch is generated' in page.locator('#edit-contract-note').inner_text())
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_ld004_evidence_en.png'))
        page.locator('#filter').select_option('unresolved')
        check('empty taxonomy filter is explicit', 'No events match' in page.locator('#events').inner_text())
        page.locator('#filter').select_option('all')
        page.locator('#search').fill('no-such-source-id')
        check('empty search is explicit', 'No events match' in page.locator('#events').inner_text())
        page.locator('#search').fill('')
        page.locator('#evaluation-panel summary').click()
        check('nuScenes evaluation distinguishes 12 clip-end observations from 11 unobserved states', '12 / 11' in page.locator('#evaluation').inner_text() and 'Observed / state unobserved at clip end' in page.locator('#evaluation').inner_text())
        check('old duration labels are not recycled into structure/state accuracy', source['evaluation']['nuscenes']['original_duration_labels_reused'] is False and source['evaluation']['nuscenes']['independent_layer_accuracy'] is None and 'not reused as layer ground truth' in page.locator('#evaluation').inner_text() and '23 / 29' not in page.locator('#comparison').inner_text())
        page.screenshot(path=str(root / 'pipeline_nuscenes_en.png'), full_page=True)
        page.locator('#dataset').select_option('argoverse')
        argo = source['datasets']['argoverse']
        full_replay = argo['reference_replay']
        replay = argo['material_reference_replay']
        material_count = len(replay['changes'])
        check('nuScenes repair preserves the independently evaluated Argoverse result counts',
              argo['summary']['reliability_counts'] == {'supported_conflict': 14, 'insufficient_geometry': 40, 'consistent_with_map': 22}
              and len(full_replay['changes']) == 98
              and source['evaluation']['argoverse']['full_boundary_geometry']['metrics']['true_positives'] == 9
              and source['evaluation']['argoverse']['full_boundary_geometry']['local_geometry_summary']['within_tolerance_detections'] == 3)
        detected = [event for event in argo['events'] if event['model_change_decision']['detected']]
        unresolved = [event for event in argo['events'] if event['reliability']['status'] == 'insufficient_geometry']
        consistent = [event for event in argo['events'] if event['reliability']['status'] == 'consistent_with_map']
        check('Argoverse defaults to the explicitly separate data-confirmed reference replay',
              page.evaluate('PIPELINE_STUDIO.state.mapSource') == 'reference'
              and page.locator('#map-source').input_value() == 'reference'
              and page.locator('#reference-detail').is_visible()
              and page.locator('#reference-change-select option').count() == material_count
              and 0 < material_count < len(full_replay['changes']))
        check('reference confirmation and applied patches are reported independently of model detections',
              replay['summary']['confirmed_changes'] == replay['summary']['executed_patches'] == material_count
              and argo['summary']['base_map_updates'] == 0
              and 0 < len(detected) < 98
              and page.locator('#map-count-confirmed').inner_text() == str(material_count)
              and 'reference' in page.locator('#reference-detail').inner_text().lower())
        first_reference = replay['changes'][0]
        page.locator('#reference-change-select').select_option(first_reference['change_id'])
        check('reference selector displays the chosen truth object without modifying model predictions',
              page.evaluate('PIPELINE_STUDIO.state.referenceId') == first_reference['change_id']
              and first_reference['element_id'] in page.locator('#reference-detail').inner_text()
              and source['prediction_freeze']['gt_read'] is False)
        check('reference map downloads are separate from model results and preserve exact exported copies',
              all((root / filename).exists() for filename in [
                  'argoverse_reference_updated_map.json', 'argoverse_reference_rollback_map.json',
                  'argoverse_reference_patch_log.json', 'argoverse_reference_changes.geojson'])
              and json.loads((root / 'argoverse_reference_changes.geojson').read_text()) == full_replay['change_layer'])
        check('material replay downloads match the filtered updated map, rollback, change layer and decision audit',
              all(json.loads((root / ('argoverse_material_reference_' + filename)).read_text()) == replay[key]
                  for filename, key in [('updated_map.json', 'updated_map'), ('rollback_map.json', 'rollback_map'),
                                        ('patch_log.json', 'patch_log'), ('changes.geojson', 'change_layer'),
                                        ('decisions.json', 'materiality_decisions')]))
        page.locator('#map-source').select_option('model')
        check('model view retains every event and reports computed detections independently of patch eligibility',
              page.evaluate('PIPELINE_STUDIO.state.mapSource') == 'model'
              and page.locator('#events [data-event]').count() == len(argo['events']) == 76
              and '76 events' in page.locator('#map-source-status').inner_text()
              and argo['summary']['stored_state_events'] == 0
              and all(not e['decision']['write_gate']['map_patch_executed'] for e in argo['events']))
        false_positive = next(event for event in argo['events'] if event['event_id'] == 'argo-local-5d7fb99ef6812741')
        page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', false_positive['event_id'])
        check('raw false-positive diagnostic remains inspectable without a red structural confirmation',
              'false positive' in page.locator('#selected-evaluation').inner_text().lower()
              and page.evaluate('PIPELINE_STUDIO.current().model_change_decision.detected') is True
              and page.evaluate('PIPELINE_STUDIO.map.isConfirmedStructure(PIPELINE_STUDIO.current())') is False)
        check('shared physical aliases retain their separate structural-write condition',
              page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.target_identity.shared_canonical_physical_boundary') is True
              and page.locator('#write-reasons [data-reason="SYNCHRONIZED_ALIAS_EDIT_UNVERIFIED"]').count() == 1
              and page.locator('#write-reasons [data-reason="EXACT_EDIT_OBJECT_UNRESOLVED"]').count() == 0
              and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.eligible') is False)
        full_eval = source['evaluation']['argoverse']['full_boundary_geometry']
        check('primary geometry evaluation includes unpainted boundaries and actual positive truth labels',
              full_eval['metrics']['truth_positives'] > 0
              and full_eval['metrics']['true_positives'] > 0
              and full_eval['metrics']['recall'] is not None
              and '75 eligible physical boundaries' not in page.locator('#dataset-scope').inner_text())
        local_eval = full_eval['local_geometry_summary']
        check('local geometry agreement is reported separately from changed-boundary identity retrieval',
              local_eval['within_tolerance_detections'] <= local_eval['evaluable_detections'] <= local_eval['model_detections']
              and local_eval['within_tolerance_detections'] < full_eval['metrics']['true_positives']
              and str(local_eval['within_tolerance_detections']) + ' / ' + str(local_eval['model_detections']) in page.locator('#local-geometry-summary').inner_text()
              and 'Boundary-identity recall' in page.locator('#comparison').inner_text()
              and 'same' in page.locator('#local-geometry-summary').inner_text().lower())
        check('materiality settings reflect the actual policy without duration or revisit requirements',
              str(source['datasets']['nuscenes']['events'][0]['final_structural_decision']['policy']['distance_tolerance_m']) in page.locator('#materiality-method').inner_text()
              and format(source['datasets']['nuscenes']['events'][0]['final_structural_decision']['policy']['minimum_contiguous_length_m'], 'g') + ' m' in page.locator('#materiality-method').inner_text()
              and 'Further observations required · No' in page.locator('#parameters').inner_text()
              and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.duration_used') is False)
        support = source['evaluation']['argoverse']['task_support_breakdown']
        check('task coverage retains all 34 changed lane-side references without treating them as model inputs',
              support['changed_lane_geometry_references'] == 34
              and support['prior_marking_counts']['NONE'] == 34
              and '34' in page.locator('#task-support').inner_text()
              and source['prediction_freeze']['gt_read'] is False)
        page.screenshot(path=str(root / 'pipeline_argoverse_en.png'), full_page=True)
        page.locator('#filter').select_option('not_applicable')
        check('geometric-agreement filter matches the computed events instead of the former fixed count',
              page.locator('#events [data-event]').count() == len(consistent) and len(consistent) > 0)
        page.locator('#events [data-event]').first.click()
        check('geometric agreement does not adopt a structural change or active routing branch',
              page.locator('#routing-path article.active').count() == 0
              and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False
              and page.evaluate('PIPELINE_STUDIO.current().model_change_decision.detected') is False)
        page.locator('#filter').select_option('insufficient')
        check('remaining insufficient evidence stays inspectable instead of being automatically promoted',
              page.locator('#events [data-event]').count() == len(unresolved) and len(unresolved) > 0)
        page.locator('#events [data-event]').first.click()
        check('semantic routing cannot override a failed geometric decision',
              page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False
              and page.evaluate('PIPELINE_STUDIO.current().model_change_decision.detected') is False)
        paint_events = [event for event in argo['events'] if event.get('prior_marking_type') == 'NONE']
        check('unpainted references use geometry-or-presence hypotheses and no blanket semantic rejection',
              len(paint_events) == 64
              and any(event['model_change_decision']['detected'] for event in paint_events)
              and all(event['structural_hypothesis']['target_property'] == 'boundary_geometry_or_paint_presence' for event in paint_events)
              and all('PRIOR_EXPLICITLY_UNPAINTED_REQUIRES_INSERTION_VERIFICATION' not in event['reliability']['reason_codes'] for event in paint_events))
        paint = next(event for event in paint_events if any(alias['map_element_id'] == 'ls-168085012' and alias['boundary_side'] == 'right' for alias in event['map_aliases']))
        page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', paint['event_id'])
        check('the 46-frame example retains its raw model detection and a distinct final no-update decision',
              paint['model_change_decision']['detected'] is True
              and paint['distinct_frame_count'] == 46
              and page.evaluate('PIPELINE_STUDIO.map.isConfirmedStructure(PIPELINE_STUDIO.current())') is False
              and page.evaluate('PIPELINE_STUDIO.current().final_structural_decision.action') == 'no_update'
              and not paint['decision']['write_gate']['map_patch_executed'])
        check('local prediction does not invent paint identity, visibility or executed full-map edits',
              paint['observation_evidence']['paint_identity']['status'] == 'unverified'
              and paint['observation_evidence']['alignment']['status'] == 'unverified'
              and paint['decision']['base_map_write_eligible'] is False
              and page.locator('#structural-required-evidence-box').is_visible())
        if page.locator('#local-measurements').get_attribute('open') is None:
            page.locator('#local-measurements > summary').click()
        check('local measurement table preserves all frozen measurements and actual positive support',
              page.locator('#local-measurement-rows tbody tr').count() == len(paint['samples'])
              and any(sample['observation'] == 'supports_change' for sample in paint['samples'])
              and all('measured_local_relation' in sample for sample in paint['samples']))
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_model_detection_evidence_en.png'))
        check('full download preserves the complete original result', download(page, '#download') == (root / 'result.json').read_bytes())
        check('Argoverse context link uses its separate recorded-data route', page.locator('#source-link').get_attribute('href') == '../../argoverse/en/index.html')
        for name in ['nuscenes', 'argoverse']:
            page.locator('#dataset').select_option(name)
            for event in source['datasets'][name]['events']:
                page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', event['event_id'])
                assert page.locator('#selected-id').inner_text() == event['event_id']
                assert re.search('[\u3400-\u9fff]', page.locator('main').inner_text()) is None
        check('all 105 source events render in English without changing their outcomes', True)
        check('interaction preserves immutable predictions and state histories', page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)') == baseline)
        page.locator('#dataset').select_option('nuscenes')
        selected = page.evaluate('PIPELINE_STUDIO.state.eventId')
        page.locator('#language-link').click()
        page.wait_for_url('**/zh/index.html?*')
        page.locator('#map-view-evidence').click()
        check('language switching preserves dataset and selected event', page.evaluate('PIPELINE_STUDIO.state.dataset') == 'nuscenes' and page.evaluate('PIPELINE_STUDIO.state.eventId') == selected)
        check('both languages use identical source-derived data', page.evaluate('PIPELINE_STUDIO.data') == view)
        check('Chinese distinguishes unobserved clip-end state from unknown classification', '地图状态事件' in page.locator('#layer-status').inner_text() and '末帧状态未观测' in page.locator('#lifecycle').inner_text() and '并不代表没有检测到该障碍' in page.locator('#lifecycle').inner_text())
        page.locator('#nuscenes-candidate-audit > summary').click()
        check('Chinese audit reports all six candidates complete separately from object-motion uncertainty',
              page.locator('#nuscenes-status-audit').is_visible()
              and '6 / 6' in page.locator('#nuscenes-structure-abstention').inner_text()
              and page.locator('#map-count-closed').inner_text() == '6'
              and page.locator('#map-count-confirmed').inner_text() == '0'
              and '3/154' in page.locator('#nuscenes-motion-note').inner_text())
        page.locator('#nuscenes-status-audit').screenshot(path=str(root / 'pipeline_nuscenes_audit_zh.png'))
        page.locator('#nuscenes-candidate-audit > summary').click()
        check('Chinese explains semantic routing and expiry without a temporal class', '持续时长不决定图层' in page.locator('#model-boundary').inner_text() and '不会虚构默认有效期或解除' in page.locator('#lifecycle').inner_text() and '需要追加观测 · 否' in page.locator('#parameters').inner_text())
        page.screenshot(path=str(root / 'pipeline_zh.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(100)
        check('mobile event and lifecycle view has no horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('#evaluation-panel summary').click()
        check('mobile evaluation has no horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.screenshot(path=str(root / 'pipeline_mobile.png'), full_page=True)
        page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', 'S0980-LD-004')
        if page.locator('#local-measurements').get_attribute('open') is None:
            page.locator('#local-measurements > summary').click()
        check('Chinese mobile LD004 evidence preserves the same-window warning without horizontal overflow',
              '同一窗口内还观测到参考线' in page.locator('#same-window-evidence').inner_text()
              and '不能确认标线消失、移位或新增' in page.locator('#selected-evaluation').inner_text()
              and page.locator('#local-measurement-rows tbody tr').count() == 6
              and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_ld004_mobile_zh.png'))
        page.locator('#dataset').select_option('argoverse')
        page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', paint['event_id'])
        check('Chinese mobile displays the computed detection with its distinct model origin',
              page.locator('#final-decision-title').is_visible()
              and page.evaluate('PIPELINE_STUDIO.current().final_structural_decision.action') == 'no_update'
              and page.evaluate('PIPELINE_STUDIO.current().model_change_decision.detected') is True
              and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_model_detection_mobile_zh.png'))
        page.locator('#map-source').select_option('reference')
        check('Chinese mobile reference view identifies supplied truth and the material subset',
              page.locator('#reference-detail').is_visible()
              and '真值' in page.locator('.composite-panel').inner_text()
              and page.locator('#reference-change-select option').count() == material_count
              and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('.composite-panel').screenshot(path=str(root / 'pipeline_reference_mobile_zh.png'))
        page.goto(base + 'en/index.html?view=2d&dataset=invalid&event=missing')
        page.wait_for_function('window.PIPELINE_STUDIO')
        check('invalid deep link recovers to a real source event', page.evaluate('PIPELINE_STUDIO.state.dataset==="nuscenes"&&PIPELINE_STUDIO.current().event_id===PIPELINE_STUDIO.state.eventId'))
        check('all assets load without failed requests', not failures)
        check('all interactions finish without uncaught JavaScript errors', not errors)
        browser.close()
    result = {'status': 'PASS', 'count': len(checks), 'checks': checks, 'page_errors': errors, 'failed_requests': failures, 'result_sha256': sha256((root / 'result.json').read_bytes()).hexdigest()}
    (root / 'pipeline_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('PASS ' + str(len(checks)) + ' structure/state workspace checks')
finally:
    server.shutdown()
