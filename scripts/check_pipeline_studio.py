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
        check('all geometry, semantic classifications, decisions and evidence are preserved', all(
            all(event[field] == original[field] for field in ['geometry_city', 'reliability', 'map_layer', 'decision']) and
            [{key: value for key, value in sample.items() if key != 'geometry_city'} for sample in original['samples']] == event['samples']
            for name in source['datasets'] for event, original in zip(view['datasets'][name]['events'], source['datasets'][name]['events'])
        ) and all(len(view['datasets'][name]['events']) == len(source['datasets'][name]['events']) for name in source['datasets']))
        check('actual state layers, evaluation and method are preserved unchanged', all(view['datasets'][name]['state_event_layer'] == source['datasets'][name]['state_event_layer'] for name in source['datasets']) and view['evaluation'] == source['evaluation'] and view['method'] == source['method'])
        check('nuScenes opens with 29 source events and 23 applied state classes', page.locator('#events [data-event]').count() == 29 and '23 / 0' in page.locator('#overview').inner_text())
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
