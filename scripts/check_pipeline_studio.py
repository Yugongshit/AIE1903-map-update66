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
        page.goto(base + 'en/index.html')
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
        check('nuScenes opens with 29 events, 23 state classes and one locally supported structural hypothesis', page.locator('#events [data-event]').count() == 29 and '23 / 1' in page.locator('#overview').inner_text() and source['datasets']['nuscenes']['summary']['reliability_counts'] == {'supported_conflict': 24, 'insufficient_geometry': 5})
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
        check('unverified marking semantics identify a structural hypothesis independently of reliability', page.locator('#layer-status').inner_text() == 'Base-map structure' and 'Geometry evidence insufficient' in page.locator('#reliability-status').inner_text() and 'actual change is not verified' in page.locator('#layer-note').inner_text() and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False)
        check('structural hypothesis does not create a state record or a base patch', not page.locator('#lifecycle').is_visible() and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.map_patch_executed') is False)
        page.locator('#events [data-event="S0980-LD-004"]').click()
        ld004 = page.evaluate('PIPELINE_STUDIO.current()')
        check('LD004 reports computed local support while its structural operation and edit remain unconfirmed',
              page.locator('#reliability-status').inner_text() == 'Local conflict supported'
              and ld004['reliability']['status'] == 'supported_conflict'
              and ld004['change_type'] == 'line_discrepancy'
              and ld004['structural_hypothesis']['operation'] == 'unresolved_parallel_marking_or_insertion'
              and ld004['structural_hypothesis']['confirmed'] is False
              and ld004['decision']['classification_applied'] is True
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
              'not a verified edit target' in page.locator('#structural-hypothesis-note').inner_text()
              and 'parallel marking or insertion' in page.locator('#structural-hypothesis-note').inner_text()
              and 'Paint identity' in page.locator('#observation-dimensions').inner_text()
              and 'Physical alignment' in page.locator('#observation-dimensions').inner_text()
              and ld004['observation_evidence']['change_assessment']['confirmed_map_change'] is False
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
        check('Argoverse loads 76 events with one applied structural class and no state records', page.locator('#events [data-event]').count() == 76 and '0 / 1' in page.locator('#overview').inner_text() and source['datasets']['argoverse']['summary']['stored_state_events'] == 0)
        check('71 structural hypotheses are distinguished from the single applied class', '71 / 1' in page.locator('#comparison').inner_text() and 'Structural hypotheses / applied' in page.locator('#comparison').inner_text())
        check('applied structural claim is still exposed as a left-boundary geometry false positive', 'geometric false positive' in page.locator('#selected-evaluation').inner_text() and 'left-boundary geometry' in page.locator('#selected-evaluation').inner_text() and '2 map references' in page.locator('#selected-title').inner_text())
        check('shared physical aliases are distinguished from unresolved physical targets without authorizing a patch',
              page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.target_identity.shared_canonical_physical_boundary') is True
              and page.locator('#write-reasons [data-reason="SYNCHRONIZED_ALIAS_EDIT_UNVERIFIED"]').count() == 1
              and page.locator('#write-reasons [data-reason="EXACT_EDIT_OBJECT_UNRESOLVED"]').count() == 0
              and 'share one known physical boundary' in page.locator('#edit-target-note').inner_text()
              and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.eligible') is False)
        check('geometry recall is undefined with no true changes in the 75-boundary scope', '75 eligible physical boundaries' in page.locator('#dataset-scope').inner_text() and 'no changed boundaries' in page.locator('.comparison-warning').inner_text() and 'recall is undefined' in page.locator('.comparison-warning').inner_text() and '0 / 1 / 0' in page.locator('#comparison').inner_text())
        check('evaluation keeps coarse object retrieval distinct from exact boundary accuracy', '0 / 98' in page.locator('#evaluation').inner_text() and 'not boundary, side or operation accuracy' in page.locator('#evaluation').inner_text() and source['evaluation']['argoverse']['layer_on_true_geometry_hits']['accuracy'] is None)
        check('structural write gate has quality conditions without duration or visit minima', 'Minimum write duration · No minimum' in page.locator('#parameters').inner_text() and 'Minimum write visits · No minimum' in page.locator('#parameters').inner_text() and page.evaluate('PIPELINE_STUDIO.current().decision.write_gate.duration_used') is False)
        support = source['evaluation']['argoverse']['task_support_breakdown']
        check('truth coverage explains the 34 unpainted boundary references and 24 intersection references', str(support['changed_lane_geometry_references']) in page.locator('#task-support').inner_text() and str(support['intersection_references']) in page.locator('#task-support').inner_text() and 'prior marking NONE' in page.locator('#task-support').inner_text() and 'Reference records can share physical geometry' in page.locator('#task-support').inner_text() and 'not prediction inputs' in page.locator('#task-support').inner_text())
        page.screenshot(path=str(root / 'pipeline_argoverse_en.png'), full_page=True)
        page.locator('#filter').select_option('not_applicable')
        check('five map-consistent events have a dedicated no-change filter', page.locator('#events [data-event]').count() == 5)
        page.locator('#events [data-event]').first.click()
        check('map-consistent events have no adopted change class or misleading active routing branch', page.locator('#layer-status').inner_text() == 'No observed change class' and page.locator('#routing-path article.active').count() == 0 and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False)
        page.locator('#filter').select_option('insufficient')
        check('all 70 insufficient-geometry Argoverse hypotheses remain inspectable', page.locator('#events [data-event]').count() == 70)
        page.locator('#events [data-event]').first.click()
        check('semantic structure identity does not override insufficient observation evidence', 'Geometry evidence insufficient' in page.locator('#reliability-status').inner_text() and 'actual change is not verified' in page.locator('#layer-note').inner_text() and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is False)
        paint_events = [event for event in source['datasets']['argoverse']['events'] if event.get('prior_marking_type') == 'NONE']
        check('all 64 unpainted references have explicit paint-presence hypotheses rather than claimed old-line displacement',
              len(paint_events) == 64 and all(event['structural_hypothesis']['target_property'] == 'paint_presence'
                  and event['structural_hypothesis']['operation'] == 'marking_presence_change'
                  and event['structural_hypothesis']['confirmed'] is False for event in paint_events))
        paint = next(event for event in paint_events if event['observation_evidence']['temporal_support']['status'] == 'sufficient'
                     and event['observation_evidence']['local_consistency']['status'] == 'stable')
        page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', paint['event_id'])
        check('paint-presence details keep measured geometry and semantic uncertainty separate',
              'Paint-presence hypothesis' in page.locator('#selected-title').inner_text()
              and 'not displacement of an old painted line' in page.locator('#structural-hypothesis-note').inner_text()
              and 'Sufficient local support' in page.locator('#observation-dimensions').inner_text()
              and 'Stable at the fixed anchor' in page.locator('#observation-dimensions').inner_text()
              and paint['reliability']['status'] == 'insufficient_geometry')
        check('paint confirmation evidence is visibly labeled as required conditions, not achieved verification',
              page.locator('#structural-required-evidence-box').is_visible()
              and 'Conditions for confirming this hypothesis' in page.locator('#structural-required-evidence-box').inner_text()
              and all(item.inner_text().startswith('Evidence ') for item in page.locator('#structural-required-evidence li').all())
              and paint['structural_hypothesis']['confirmed'] is False)
        if page.locator('#local-measurements').get_attribute('open') is None:
            page.locator('#local-measurements > summary').click()
        check('unknown change assessment does not erase the actual measured local relations',
              page.locator('#local-measurement-rows tbody tr').count() == len(paint['samples'])
              and all(sample['observation'] == 'unknown' and sample['measured_local_relation'] in {'within_tolerance', 'offset_positive', 'offset_negative'} for sample in paint['samples'])
              and 'Unknown' in page.locator('#local-measurement-rows').inner_text())
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_paint_presence_evidence_en.png'))
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
        check('Chinese explains semantic routing and expiry without a temporal class', '持续时长不决定图层' in page.locator('#model-boundary').inner_text() and '不会虚构默认有效期或解除' in page.locator('#lifecycle').inner_text() and '最低写入访问次数 · 无最低值' in page.locator('#parameters').inner_text())
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
        check('Chinese mobile paint-presence evidence remains a hypothesis and fits the screen',
              '新标线存在候选' in page.locator('#selected-title').inner_text()
              and '不是旧油漆线发生位移' in page.locator('#structural-hypothesis-note').inner_text()
              and page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        check('Chinese confirmation conditions request evidence without claiming it is already verified',
              '确认此假设所要求的证据' in page.locator('#structural-required-evidence-box').inner_text()
              and all(item.inner_text().startswith('证明') for item in page.locator('#structural-required-evidence li').all())
              and '已核验' not in page.locator('#structural-required-evidence').inner_text())
        page.locator('#structural-evidence').screenshot(path=str(root / 'pipeline_paint_presence_mobile_zh.png'))
        page.goto(base + 'en/index.html?dataset=invalid&event=missing')
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
