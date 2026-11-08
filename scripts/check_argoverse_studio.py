"""Browser regression checks for the separate Argoverse rerun workspace."""
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
checks, errors, failed_requests = [], [], []


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check(name, result):
    if not result:
        raise AssertionError(name)
    checks.append(name)
    print('PASS ' + name, flush=True)


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}/'
source = json.loads((root / 'result.json').read_text())
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1460, 'height': 1100})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: failed_requests.append([response.url, response.status]) if response.status >= 400 else None)
        page.goto(base + 'en/index.html')
        page.wait_for_function('window.ARGO_STUDIO')
        baseline = page.evaluate('JSON.stringify(ARGO_STUDIO.data)')
        check('embedded result exactly matches the executed pipeline result', page.evaluate('ARGO_STUDIO.data') == source)
        check('overview preserves actual 582 frames, 10278 observations, four candidates and one group', all(text in page.locator('#overview').inner_text() for text in ['582', '10,278', '6,770']) and source['summary']['m3_candidates'] == 4 and source['summary']['m4_groups'] == 1 and source['summary']['confirmed_changes'] == 0)
        check('M2 is identified as reused input while M3 and M4 were rerun', 'FROZEN INPUTS' in page.locator('[data-stage="M2"]').inner_text() and 'EXECUTED AGAIN' in page.locator('[data-stage="M3"]').inner_text() and 'EXECUTED AGAIN' in page.locator('[data-stage="M4"]').inner_text())
        check('Argoverse M3 is distinguished from nuScenes reliability M3', 'different implementations' in page.locator('#stage-context').inner_text())
        check('uncertain change confirmation is separate from unimplemented duration classification', 'Uncertain' in page.locator('#status').inner_text() and 'Not implemented' in page.locator('#status').inner_text() and 'not its duration' in page.locator('#duration-note').inner_text())
        check('M3 shows actual insufficient and projection-failure counts', '10,271' in page.locator('#m3-outcomes').inner_text() and '3,508' in page.locator('#m3-reasons [data-reason="NO_USABLE_CITY_GEOMETRY"]').inner_text())
        diagnostics = source['stages']['M2'].get('diagnostics')
        if diagnostics:
            diagnostic_note = page.locator('#m2-diagnostics').inner_text()
            check('M2 duplicate-reference diagnostic uses actual counts and does not claim an algorithm fix', all(text in diagnostic_note for text in [f"{diagnostics['ambiguous_matches']:,}", f"{diagnostics['ambiguous_top2_same_boundary_and_attribute']:,}", 'shared physical boundary', 'diagnostic findings', 'algorithms have not changed']))
            risk_count = diagnostics['quality_flag_counts']['curb_confusion_possible']
            check('global curb-risk flags are not presented as object-specific proof', f"{risk_count:,} / {source['summary']['observations']:,}" in diagnostic_note and 'global model-risk notice' in diagnostic_note and 'not object-specific proof' in diagnostic_note)
        check('all five M4 reasons remain visible', page.locator('#reasons [data-reason]').count() == len(source['candidates'][0]['reason_codes']) == 5)
        check('M4 compares four support frames and 0.301 seconds against configured five frames and one second', all(text in page.locator('#m4-evidence').inner_text() for text in ['4', '0.301 s', '≥ 5', '≥ 1.0 s', 'Unknown', 'Unavailable']))
        check('evaluation truth, metrics and overlay are hidden by default', not page.locator('#truth-toggle').is_checked() and not page.locator('#evaluation-content').is_visible() and not page.locator('#truth-watermark').is_visible() and page.evaluate('ARGO_STUDIO.state.drawn.truth') == 0)
        check('map draws actual prior lines, four M3 curves and one M4 representative', page.evaluate('ARGO_STUDIO.state.drawn.prior>0&&ARGO_STUDIO.state.drawn.m3===4&&ARGO_STUDIO.state.drawn.m4===1'))
        check('displayed candidate coordinates are source XY values without reconstruction', page.evaluate('ARGO_STUDIO.geometry.candidateLines[0]') == [point[:2] for point in source['candidates'][0]['geometry_city']])
        canvas = page.locator('#map')
        before = canvas.screenshot()
        scale = page.evaluate('ARGO_STUDIO.state.scale')
        page.locator('#zoom-in').click()
        check('zoom changes the metric scale and map pixels', page.evaluate('ARGO_STUDIO.state.scale') > scale and sha256(before).digest() != sha256(canvas.screenshot()).digest())
        center = page.evaluate('ARGO_STUDIO.state.center')
        rect = canvas.bounding_box()
        page.mouse.move(rect['x'] + 150, rect['y'] + 120)
        page.mouse.down()
        page.mouse.move(rect['x'] + 180, rect['y'] + 145, steps=5)
        page.mouse.up()
        check('map supports panning', page.evaluate('ARGO_STUDIO.state.center') != center)
        page.locator('#fit-all').click()
        wide_scale = page.evaluate('ARGO_STUDIO.state.scale')
        page.locator('#fit').click()
        check('map and candidate fit controls use different source bounds', page.evaluate('ARGO_STUDIO.state.scale') > wide_scale)
        page.locator('#truth-toggle').check()
        check('truth requires an explicit toggle and has a visible evaluation watermark', page.locator('#evaluation-content').is_visible() and page.locator('#truth-watermark').is_visible() and page.evaluate('ARGO_STUDIO.state.drawn.truth') > 0)
        check('evaluation shows zero of 98 retrieved, undefined precision and zero of one candidate hits', '0 / 98' in page.locator('#evaluation-metrics').inner_text() and '0 / 1' in page.locator('#evaluation-metrics').inner_text() and 'Undefined with no confirmed predictions' in page.locator('#evaluation-metrics').inner_text() and source['evaluation']['precision'] is None)
        check('full-map retrieval is not presented as visibility-conditioned accuracy', 'not perception accuracy' in page.locator('#evaluation-limit').inner_text() and '273' in page.locator('#evaluation-scope').inner_text() and '288' in page.locator('#evaluation-scope').inner_text() and '15' in page.locator('#evaluation-scope').inner_text())
        check('uncertain candidate is evaluated separately from confirmed predictions', 'Unchanged in supplied truth' in page.locator('#candidate-diagnostics').inner_text() and 'not a final positive' in page.locator('#candidate-diagnostics').inner_text())
        page.screenshot(path=str(root / 'argoverse_evaluation_en.png'), full_page=True)
        page.locator('#truth-toggle').uncheck()
        check('disabling truth removes both the evaluation layer and metrics', page.evaluate('ARGO_STUDIO.state.drawn.truth') == 0 and not page.locator('#evaluation-content').is_visible())
        with page.expect_download() as download:
            page.locator('#download').click()
        check('download preserves the complete immutable rerun JSON', Path(download.value.path()).read_bytes() == (root / 'result.json').read_bytes())
        check('nuScenes navigation keeps the datasets on distinct routes', page.locator('#nuscenes-link').get_attribute('href') == '../../en/index.html#decisions')
        check('English main content is fully translated', re.search('[\u3400-\u9fff]', page.locator('main').inner_text()) is None)
        check('inspection and evaluation do not mutate predictions or truth', page.evaluate('JSON.stringify(ARGO_STUDIO.data)') == baseline)
        page.screenshot(path=str(root / 'argoverse_en.png'), full_page=True)
        page.locator('#language-link').click()
        page.wait_for_url('**/zh/index.html')
        check('Chinese route retains the exact same source data', page.evaluate('ARGO_STUDIO.data') == source)
        check('Chinese outcome and all reason labels are translated', '尚未实现' in page.locator('#status').inner_text() and '有效可见帧分母未知' in page.locator('#reasons').inner_text() and '支持观测的时间跨度过短' in page.locator('#reasons').inner_text())
        check('Chinese route defaults to hidden evaluation truth', not page.locator('#truth-toggle').is_checked() and page.evaluate('ARGO_STUDIO.state.drawn.truth') == 0)
        check('Chinese context link returns to the Chinese nuScenes workspace', page.locator('#nuscenes-link').get_attribute('href') == '../../zh/index.html#decisions')
        page.screenshot(path=str(root / 'argoverse_zh.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(100)
        check('mobile default view has no horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.locator('#truth-toggle').check()
        check('mobile evaluation view has no horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.screenshot(path=str(root / 'argoverse_mobile.png'), full_page=True)
        check('all page assets load without failed requests', not failed_requests)
        check('all interactions complete without uncaught JavaScript errors', not errors)
        browser.close()
    result = {'status': 'PASS', 'count': len(checks), 'checks': checks, 'page_errors': errors, 'failed_requests': failed_requests, 'result_sha256': sha256((root / 'result.json').read_bytes()).hexdigest()}
    (root / 'argoverse_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('PASS ' + str(len(checks)) + ' Argoverse workspace checks')
finally:
    server.shutdown()
