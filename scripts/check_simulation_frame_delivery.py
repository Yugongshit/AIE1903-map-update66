"""Regression-check real MetaDrive frame delivery under slow/failing PNG loads.

Start run_simulation_platform.py with the simulation interpreter first. Run
this checker with a Python that has Playwright; Chromium is a test dependency.
The browser must present a decoded image before advancing its timeline or
Planning export. Engine state alone is not evidence that an image was shown.
"""
import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode

from playwright.async_api import async_playwright


SCENE = 'argoverse-winter2021'
VERSION = 'updated'
START_STEP = 50

STATE_JS = """() => {
  const s=window.MAP_PLATFORM?.state, img=document.querySelector('#engine-frame');
  const d=s?.engineDisplayedFrame;
  return {step:s?.step,snapshotStep:s?.snapshot?.step,playing:s?.playing,busy:s?.engineCommandBusy,
    displayedStep:d?.step,frameId:d?.frame_id,sequence:d?.sequence,displayedKey:d?.key,
    imageStep:img?.dataset.step,imageFrameId:img?.dataset.frameId,
    width:img?.naturalWidth,complete:img?.complete,hidden:img?.hidden,src:img?.currentSrc,
    slider:Number(document.querySelector('#timeline')?.value),
    readout:document.querySelector('#frame-readout')?.textContent,
    time:document.querySelector('#current-time')?.textContent,
    error:Boolean(s?.engineFrameError),candidateStep:s?.engineFrameError?.result?.step};
}"""


def frame_url(base, result, *, scene=SCENE, version=VERSION, **overrides):
    fields = {'frame_id': result['frame_id'], 'sequence': result['sequence'],
              'scene_id': scene, 'version': version}
    fields.update(overrides)
    return base.rstrip('/') + '/api/engine/frame.png?' + urlencode(fields)


async def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'url': args.url, 'scene': SCENE, 'delay_ms': args.delay_ms,
              'checks': [], 'errors': [], 'image_requests': [], 'advance_requests': [],
              'unexpected_advance_during_png': [], 'presentations': []}

    def check(label, condition, details=None):
        report['checks'].append({'name': label, 'passed': bool(condition), 'details': details})
        print(('PASS ' if condition else 'FAIL ') + label, flush=True)
        if not condition:
            raise AssertionError(label + (': ' + repr(details) if details is not None else ''))

    async with async_playwright() as pw:
        request = await pw.request.new_context(base_url=args.url)

        async def post(path, body):
            response = await request.post(path, data=body)
            if response.status != 200 and response.status != 202:
                raise AssertionError('Engine API rejected command: ' + await response.text())
            return await response.json()

        async def wait_job(job):
            deadline = asyncio.get_running_loop().time() + 180
            while asyncio.get_running_loop().time() < deadline:
                status = await (await request.get('/api/engine/status')).json()
                if not status.get('busy'):
                    if status.get('error'):
                        raise AssertionError(status['error'])
                    check('engine command acknowledgement matches requested job', status['job_id'] == job['job_id'])
                    return status['last_result']
                await asyncio.sleep(.25)
            raise TimeoutError('Real MetaDrive operation did not complete')

        browser = page = None
        try:
            # API requests are independent of the browser's artificial delays.
            first = await wait_job(await post('/api/engine/open', {'scene_id': SCENE, 'version': VERSION, 'step': 30}))
            response = await request.get(frame_url(args.url, first))
            original_bytes = await response.body()
            check('first genuine Argoverse frame is a readable PNG',
                  response.status == 200 and original_bytes.startswith(b'\x89PNG\r\n\x1a\n')
                  and first.get('live_engine') is True and first.get('dataset') == 'argoverse')
            second = await wait_job(await post('/api/engine/command', {
                'command': 'advance', 'steps': 2, 'expected_scene_id': SCENE, 'expected_version': VERSION}))
            old_response = await request.get(frame_url(args.url, first))
            old_bytes = await old_response.body()
            check('previous frame remains byte-identical after the engine advances',
                  second['step'] == first['step']+2 and old_response.status == 200
                  and sha256(old_bytes).digest() == sha256(original_bytes).digest())
            report['immutable_frame'] = {'before': first['frame_id'], 'after': second['frame_id'],
                                         'first_step': first['step'], 'next_step': second['step'],
                                         'sha256': sha256(original_bytes).hexdigest(), 'bytes': len(original_bytes)}
            for label, overrides in [('wrong scene', {'scene_id': 'nuscenes-scene0980'}),
                                     ('wrong map version', {'version': 'original'}),
                                     ('wrong frame ID', {'frame_id': '0'*32}),
                                     ('wrong sequence', {'sequence': first['sequence']+1000})]:
                invalid = await request.get(frame_url(args.url, first, **overrides))
                check(label + ' cannot read another frame', invalid.status in (400, 404), invalid.status)
            missing_id = await request.get('/api/engine/frame.png?' + urlencode({
                'sequence': first['sequence'], 'scene_id': SCENE, 'version': VERSION}))
            check('sequence alone cannot read a frame from a different engine lifetime',
                  missing_id.status == 400, missing_id.status)
            if args.backend_only:
                return report

            browser = await pw.chromium.launch(executable_path=args.chromium, headless=True,
                args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
            page = await browser.new_page(viewport={'width': 1600, 'height': 1000}, accept_downloads=True)
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            fault = {'mode': 'normal', 'remaining': 0, 'pending': 0}
            next_png = asyncio.Event()

            async def png(route):
                mode = fault['mode']
                row = {'url': route.request.url, 'mode': mode, 'start': await page.evaluate(STATE_JS)}
                report['image_requests'].append(row)
                fault['pending'] += 1
                next_png.set()
                try:
                    if mode != 'normal':
                        await asyncio.sleep(args.delay_ms / 1000)
                    row['before_delivery'] = await page.evaluate(STATE_JS)
                    if mode == 'persistent' or mode == 'transient' and fault['remaining'] > 0:
                        fault['remaining'] = max(0, fault['remaining']-1)
                        row['http_status'] = 500
                        await route.fulfill(status=500, content_type='application/json',
                                            body='{"error":"injected frame delivery failure"}')
                    else:
                        upstream = await route.fetch()
                        row['http_status'] = upstream.status
                        await route.fulfill(response=upstream)
                finally:
                    fault['pending'] -= 1

            def command_request(request_event):
                if '/api/engine/command' not in request_event.url or request_event.method != 'POST':
                    return
                command = request_event.post_data_json
                if command.get('command') == 'advance':
                    record = {'command': command, 'png_in_flight': fault['pending']}
                    report['advance_requests'].append(record)
                    if fault['pending']:
                        report['unexpected_advance_during_png'].append(record)

            await page.route('**/api/engine/frame.png?*', png)
            page.on('request', command_request)
            async with page.expect_response(lambda response: response.url.split('?')[0].endswith('/simulation_platform.js')) as script_response:
                await page.goto(args.url.rstrip('/') + '/?scene=' + SCENE, wait_until='networkidle', timeout=60000)
            loaded_script = await script_response.value
            report['frontend_asset'] = {'url': loaded_script.url, 'sha256': sha256(await loaded_script.body()).hexdigest()}
            await page.wait_for_function('MAP_PLATFORM.state.key === "argoverse-winter2021" && MAP_PLATFORM.state.snapshot?.step === 0', timeout=60000)
            await page.evaluate('(step)=>MAP_PLATFORM.seek(step)', START_STEP)
            await page.locator('#start-engine').click()

            async def wait_presented(step=None):
                await page.wait_for_function("""expected => {
                    const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,img=document.querySelector('#engine-frame');
                    return !s.engineCommandBusy && d && !s.engineFrameError &&
                      img.complete && img.naturalWidth>0 && !img.hidden &&
                      s.step===d.step && s.snapshot?.step===d.step &&
                      (expected===null || d.step===expected);
                }""", arg=step, timeout=180000)

            await wait_presented(START_STEP)
            initial = await page.evaluate(STATE_JS)
            check('browser presents a decoded real engine image before committing the selected frame',
                  initial['width'] > 0 and initial['displayedStep'] == initial['step'] == initial['slider'] == START_STEP, initial)
            await page.evaluate("""() => {
                window.__frameDeliverySamples=[];
                window.__frameDeliveryTimer=setInterval(()=>{
                    const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,img=document.querySelector('#engine-frame');
                    if(d)window.__frameDeliverySamples.push({step:s.step,displayed:d.step,snapshot:s.snapshot?.step,
                      slider:Number(document.querySelector('#timeline').value),frameId:d.frame_id,
                      width:img.naturalWidth,complete:img.complete,hidden:img.hidden,src:img.currentSrc});
                },25);
            }""")

            # Slow delivery must back-pressure playback, not replace the shown
            # frame with a broken/loading image while time continues to move.
            fault['mode'] = 'delayed'
            next_png.clear()
            await page.locator('#play-pause').click()
            await asyncio.wait_for(next_png.wait(), timeout=30)
            presented_during_download = await page.evaluate(STATE_JS)
            async with page.expect_download(timeout=60000) as download_info:
                await page.locator('#export-planning').click()
            download = await download_info.value
            planning = json.loads(Path(await download.path()).read_text())
            await download.save_as(str(args.out/'planning-during-delayed-frame.json'))
            check('Planning export stays on the image presented during a slow PNG download',
                  planning['step'] == presented_during_download['displayedStep'],
                  {'export_step': planning['step'], 'presented': presented_during_download})
            await page.wait_for_function('(step)=>MAP_PLATFORM.state.engineDisplayedFrame?.step >= step', arg=START_STEP+2, timeout=120000)
            await page.locator('#engine-frame').screenshot(path=str(args.out/'argoverse-engine-near-frame52.png'))
            report['native_screenshot_state'] = await page.evaluate(STATE_JS)
            await page.wait_for_function('(step)=>MAP_PLATFORM.state.engineDisplayedFrame?.step >= step', arg=START_STEP+6, timeout=120000)
            await page.evaluate('MAP_PLATFORM.stopPlayback()')
            await wait_presented()
            delayed_requests = [r for r in report['image_requests'] if r['mode'] == 'delayed']
            check('no subsequent advance is issued while an engine PNG is in flight',
                  not report['unexpected_advance_during_png'], report['unexpected_advance_during_png'])
            check('timeline and visible image stay on the prior committed frame throughout PNG delay',
                  len(delayed_requests) >= 3 and all(
                      r['start']['step'] == r['before_delivery']['step'] == r['start']['displayedStep']
                      and r['before_delivery']['width'] > 0 for r in delayed_requests), len(delayed_requests))
            await page.screenshot(path=str(args.out/'playback-loaded.png'), full_page=True)

            # One failed GET should recover on the same immutable frame ID.
            fault.update(mode='transient', remaining=1)
            transient_before = await page.evaluate(STATE_JS)
            request_offset = len(report['image_requests'])
            await page.locator('#next-frame').click()
            await wait_presented(transient_before['step']+1)
            attempts = report['image_requests'][request_offset:]
            check('a transient image error retries the same frame and recovers',
                  len(attempts) >= 2 and attempts[0]['http_status'] == 500
                  and attempts[-1]['http_status'] == 200 and len({r['url'] for r in attempts}) == 1)

            # Persistent delivery failure must stop automatic playback while
            # keeping the previous image, clock, state and Planning selection.
            fault['mode'] = 'persistent'
            failure_before = await page.evaluate(STATE_JS)
            await page.locator('#play-pause').click()
            await page.wait_for_function('MAP_PLATFORM.state.engineFrameError && !MAP_PLATFORM.state.engineCommandBusy', timeout=120000)
            failed = await page.evaluate(STATE_JS)
            check('persistent PNG failures pause playback and retain the last decoded frame and clock',
                  not failed['playing'] and failed['step'] == failed['displayedStep'] == failed['slider'] == failure_before['step']
                  and failed['src'] == failure_before['src'] and failed['width'] > 0 and failed['complete'], failed)
            check('persistent failure has an actionable visible retry control',
                  await page.locator('#engine-frame-error').is_visible()
                  and await page.locator('#retry-engine-frame').is_visible())
            async with page.expect_download(timeout=60000) as download_info:
                await page.locator('#export-planning').click()
            failure_download = await download_info.value
            failure_planning = json.loads(Path(await failure_download.path()).read_text())
            check('Planning remains bound to the visible prior frame after an image failure',
                  failure_planning['step'] == failure_before['step'])
            await page.screenshot(path=str(args.out/'failure-retains-previous.png'), full_page=True)
            report['failure_snapshot'] = failed

            fault['mode'] = 'normal'
            await page.locator('#retry-engine-frame').click()
            await wait_presented(failed['candidateStep'])
            recovered = await page.evaluate(STATE_JS)
            check('explicit retry recovers the failed candidate without hiding or skipping its image',
                  recovered['step'] == failed['candidateStep'] and recovered['width'] > 0
                  and not recovered['error'] and not recovered['playing'], recovered)
            await page.screenshot(path=str(args.out/'recovered.png'), full_page=True)
            samples = await page.evaluate('clearInterval(window.__frameDeliveryTimer); window.__frameDeliverySamples')
            report['presentations'] = samples
            check('every sampled presentation has a loaded image and matching timeline/state', all(
                row['width'] > 0 and row['complete'] and not row['hidden']
                and row['step'] == row['displayed'] == row['snapshot'] == row['slider'] for row in samples))
            check('multiple distinct real engine images were visibly presented', len({row['frameId'] for row in samples}) >= 5)

            # A review seek changes the selected time without changing the
            # engine. Re-entering engine mode must hide the old engine image
            # while the requested frame is fetched, then commit atomically.
            await page.evaluate('(step)=>MAP_PLATFORM.seek(step)', START_STEP)
            await wait_presented(START_STEP)
            await page.locator('[data-mode="review"]').click()
            await page.evaluate('MAP_PLATFORM.seek(100)')
            fault['mode'] = 'delayed'
            next_png.clear()
            await page.locator('[data-mode="engine"]').click()
            await asyncio.wait_for(next_png.wait(), timeout=60)
            reenter_pending = await page.evaluate(STATE_JS)
            check('re-entering engine mode hides a stale image until the reviewed frame is decoded',
                  reenter_pending['hidden'] and reenter_pending['step'] == 100
                  and reenter_pending['snapshotStep'] == 100 and reenter_pending['displayedStep'] == START_STEP,
                  reenter_pending)
            async with page.expect_download(timeout=60000) as download_info:
                await page.locator('#export-planning').click()
            review_download = await download_info.value
            review_planning = json.loads(Path(await review_download.path()).read_text())
            check('Planning stays on the selected review time while an outdated engine image is hidden',
                  review_planning['step'] == 100)
            await wait_presented(100)
            reentered = await page.evaluate(STATE_JS)
            check('review-to-engine transition presents the requested frame with its matching state',
                  not reentered['hidden'] and reentered['width'] > 0
                  and reentered['step'] == reentered['snapshotStep'] == reentered['displayedStep'] == 100,
                  reentered)
            report['review_reentry'] = {'pending': reenter_pending, 'presented': reentered}

            # If re-entry fails, rollback includes the clock and state, not
            # only the old image; explicit retry still targets the review seek.
            await page.locator('[data-mode="review"]').click()
            await page.evaluate('MAP_PLATFORM.seek(150)')
            fault['mode'] = 'persistent'
            await page.locator('[data-mode="engine"]').click()
            await page.wait_for_function('MAP_PLATFORM.state.engineFrameError && !MAP_PLATFORM.state.engineCommandBusy', timeout=120000)
            failed_reentry = await page.evaluate(STATE_JS)
            check('failed review-to-engine transition restores the prior image and matching clock together',
                  not failed_reentry['hidden'] and failed_reentry['width'] > 0
                  and failed_reentry['step'] == failed_reentry['snapshotStep'] == failed_reentry['displayedStep'] == 100
                  and failed_reentry['candidateStep'] == 150, failed_reentry)
            fault['mode'] = 'normal'
            await page.locator('#retry-engine-frame').click()
            await wait_presented(150)
            reentry_recovered = await page.evaluate(STATE_JS)
            check('retry after a review-to-engine failure presents the original requested frame',
                  reentry_recovered['step'] == 150 and not reentry_recovered['error']
                  and not reentry_recovered['playing'], reentry_recovered)
            report['review_reentry_failure'] = {'failed': failed_reentry, 'recovered': reentry_recovered}

            # Cancel a same-step camera PNG and return immediately, before the
            # aborted promise has cleared its busy flag. The replacement frame
            # request must queue rather than leave the old image indefinitely.
            fault['mode'] = 'delayed'
            next_png.clear()
            await page.locator('[data-camera="rear"]').click()
            await asyncio.wait_for(next_png.wait(), timeout=60)
            await page.evaluate("""() => {
                document.querySelector('[data-mode="review"]').click();
                document.querySelector('[data-mode="engine"]').click();
            }""")
            await page.wait_for_function("""() => {
                const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,img=document.querySelector('#engine-frame');
                return !s.engineCommandBusy && !s.engineFrameError && d &&
                  d.frame_id===s.engine.last_result?.frame_id && !img.hidden && img.complete &&
                  img.naturalWidth>0 && s.step===d.step && s.snapshot?.step===d.step;
            }""", timeout=120000)
            canceled_reentry = await page.evaluate(STATE_JS)
            check('immediate mode re-entry after a canceled PNG queues and presents the current engine frame',
                  canceled_reentry['step'] == 150 and not canceled_reentry['busy']
                  and not canceled_reentry['hidden'] and canceled_reentry['width'] > 0, canceled_reentry)
            report['canceled_png_reentry'] = canceled_reentry
            check('no uncaught browser JavaScript errors', not report['errors'], report['errors'])
            return report
        finally:
            if page is not None:
                try:
                    await page.evaluate('window.MAP_PLATFORM?.stopPlayback()')
                except Exception:
                    pass
            (args.out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if browser is not None:
                await browser.close()
            await request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8092')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    parser.add_argument('--delay-ms', type=int, default=900)
    parser.add_argument('--backend-only', action='store_true')
    args = parser.parse_args()
    if args.delay_ms < 1:
        parser.error('--delay-ms must be positive')
    if not args.backend_only and not args.chromium:
        parser.error('Chromium executable not found; pass --chromium')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
