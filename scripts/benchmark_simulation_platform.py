"""Measure real browser-visible MetaDrive performance on a fixed scenario.

Run once per server build, using the same arguments and an idle machine. FPS
counts distinct decoded images observed at browser animation frames; advancing
several source ticks does not manufacture additional rendered frames. Timing
results are measurements, not absolute pass/fail thresholds for other machines.
Requires Playwright and Chromium; the server uses its own simulation interpreter.
"""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil
import statistics
import time
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


MONITOR_JS = """() => {
  const b=window.__simulationBenchmark={frames:[],violations:[],samples:0,
    lastFrame:null,phase:'startup',stopAt:null,playbackDone:false,stop:false};
  // Stop before another command is scheduled, then measure the final image
  // at its next animation frame. An rAF-only stop can allow one extra tick.
  b.observer=new MutationObserver(()=>{
    if(!b.stopRequested&&b.stopAt!==null&&window.MAP_PLATFORM?.state.engineDisplayedFrame?.step>=b.stopAt){
      b.stopRequested=true;
      b.observer.disconnect();
      MAP_PLATFORM.stopPlayback();
    }
  });
  b.observer.observe(document.querySelector('#engine-view'),{childList:true,subtree:true});
  function tick(now){
    if(b.stop)return;
    const s=window.MAP_PLATFORM?.state,d=s?.engineDisplayedFrame;
    const image=document.querySelector('#engine-frame');
    if(s?.mode==='engine'&&d&&!image.hidden){
      b.samples++;
      const row={at_ms:now,phase:b.phase,step:s.step,displayed_step:d.step,
        snapshot_step:s.snapshot?.step,source_s:s.scene?.timeline.times_s[d.step],
        frame_id:d.frame_id,image_frame_id:image.dataset.frameId,
        image_step:Number(image.dataset.step),width:image.naturalWidth,height:image.naturalHeight,
        complete:image.complete,live_engine:d.result?.live_engine,
        map_sha256:d.result?.map_features_sha256,snapshot_sha256:s.snapshot?.map_sha256,
        selected_sha256:s.scene?.versions[d.version]?.map_sha256,
        camera:d.result?.camera,readout:document.querySelector('#frame-readout')?.textContent};
      const valid=row.width>0&&row.height>0&&row.complete&&row.live_engine===true&&
        row.frame_id===row.image_frame_id&&row.step===row.displayed_step&&
        row.step===row.snapshot_step&&row.step===row.image_step&&
        row.map_sha256===row.snapshot_sha256&&row.map_sha256===row.selected_sha256&&
        row.readout.includes('帧 '+row.step+' /');
      if(!valid)b.violations.push(row);
      if(d.frame_id!==b.lastFrame){b.frames.push(row);b.lastFrame=d.frame_id;}
      if(b.stopAt!==null&&d.step>=b.stopAt){
        MAP_PLATFORM.stopPlayback();b.playbackDone=true;b.playbackEndMs=now;b.stopAt=null;
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}"""


def latency_summary(rows):
    values = [row['latency_ms'] for row in rows]
    return {'samples': rows, 'mean_ms': statistics.mean(values) if values else None,
            'median_ms': statistics.median(values) if values else None,
            'max_ms': max(values) if values else None}


async def benchmark(args):
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'label': args.label, 'url': args.url, 'scene': args.scene,
              'requested_range': [args.start, args.end], 'frontend_asset': None,
              'errors': [], 'http_errors': [], 'command_requests': [], 'png_responses': [],
              'checks': [], 'timing_notes': [
                  'Delivered FPS counts distinct decoded images observed by requestAnimationFrame.',
                  'Source seconds per wall second is separate from delivered image FPS.',
                  'Startup includes engine launch and seeking to the requested starting frame.',
                  'Compare identical scene, viewport, engine image size, camera, source range, and hardware.',
                  'Headless Chromium and cloud software rendering are not the user Mac GPU.']}
    started = time.perf_counter()

    def check(name, condition, details=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'details': details})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = await browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.set_default_timeout(args.timeout * 1000)
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        pending_responses = set()

        async def capture_response(response):
            path = urlparse(response.url).path
            if response.status >= 400:
                report['http_errors'].append({'url': response.url, 'status': response.status})
            if path in ('/api/engine/open', '/api/engine/command'):
                await response.body()
                report['command_requests'].append({
                    'command': response.request.post_data_json,
                    'status': response.status, 'timing': response.request.timing})
            elif path == '/api/engine/frame.png' and response.status == 200:
                body = await response.body()
                report['png_responses'].append({
                    'frame_id': parse_qs(urlparse(response.url).query).get('frame_id', [None])[0],
                    'sha256': sha256(body).hexdigest(), 'bytes': len(body),
                    'valid_png': body.startswith(b'\x89PNG\r\n\x1a\n'),
                    'timing': response.request.timing})

        def response_received(response):
            task = asyncio.create_task(capture_response(response))
            pending_responses.add(task)
            task.add_done_callback(pending_responses.discard)

        page.on('response', response_received)

        async def wait_presented(step=None, previous_id=None):
            await page.wait_for_function("""({step,previous})=>{
                const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,i=document.querySelector('#engine-frame');
                return !s.engineCommandBusy&&!s.engineFrameError&&d&&!i.hidden&&i.complete&&i.naturalWidth>0&&
                  s.step===d.step&&s.snapshot?.step===d.step&&
                  (step==null||d.step===step)&&(previous==null||d.frame_id!==previous)&&
                  window.__simulationBenchmark.frames.some(f=>f.frame_id===d.frame_id);
            }""", arg={'step': step, 'previous': previous_id})

        async def presentation():
            return await page.evaluate('window.__simulationBenchmark.frames.at(-1)')

        async def phase(value):
            await page.evaluate('(value)=>window.__simulationBenchmark.phase=value', value)

        try:
            async with page.expect_response(lambda r: urlparse(r.url).path == '/simulation_platform.js') as script_event:
                await page.goto(args.url.rstrip('/') + '/?scene=' + args.scene, wait_until='networkidle')
            script = await script_event.value
            report['frontend_asset'] = {'url': script.url, 'sha256': sha256(await script.body()).hexdigest()}
            await page.wait_for_function('(scene)=>MAP_PLATFORM.state.key===scene&&MAP_PLATFORM.state.snapshot?.step===0', arg=args.scene)
            check('requested source range exists', await page.evaluate('(end)=>MAP_PLATFORM.state.scene.frame_count>end', args.end))
            await page.evaluate('(step)=>MAP_PLATFORM.seek(step)', args.start)
            report['configuration'] = await page.evaluate("""() => ({
                scene_id:MAP_PLATFORM.state.scene.scene_id,dataset:MAP_PLATFORM.state.scene.dataset,
                timestep_s:MAP_PLATFORM.state.scene.timestep_s,speed:MAP_PLATFORM.state.speed,
                viewport:[innerWidth,innerHeight],device_pixel_ratio:devicePixelRatio,
                map_sha256:MAP_PLATFORM.state.scene.versions.updated.map_sha256,
                user_agent:navigator.userAgent})""")
            await page.evaluate(MONITOR_JS)
            startup_begin = await page.evaluate("""() => {
                const t=performance.now();document.querySelector('#start-engine').click();return t;
            }""")
            await wait_presented(args.start)
            first = await presentation()
            report['startup'] = {'open_to_presented_ms': first['at_ms'] - startup_begin,
                                 'first_frame': first}
            print('STARTUP ' + str(round(report['startup']['open_to_presented_ms'], 1)) + ' ms', flush=True)

            await phase('playback')
            playback_begin = await page.evaluate("""end => {
                const b=window.__simulationBenchmark;b.stopAt=end;b.playbackStartMs=performance.now();
                MAP_PLATFORM.play();return b.playbackStartMs;
            }""", args.end)
            for _ in range(max(1, args.timeout // 5)):
                progress = await page.evaluate("""() => ({
                    done:window.__simulationBenchmark.playbackDone,
                    frames:window.__simulationBenchmark.frames.length,
                    sampled:window.__simulationBenchmark.samples,
                    step:MAP_PLATFORM.state.step,busy:MAP_PLATFORM.state.engineCommandBusy,
                    shown:MAP_PLATFORM.state.engineDisplayedFrame?.step,
                    stopAt:window.__simulationBenchmark.stopAt,
                    error:MAP_PLATFORM.state.engineFrameError})""")
                if progress['done']:
                    print('PRESENTED END ' + json.dumps(progress), flush=True)
                    break
                print('PROGRESS ' + json.dumps(progress), flush=True)
                await asyncio.sleep(5)
            else:
                raise TimeoutError('Playback did not present its requested final frame')
            await wait_presented(await page.evaluate('MAP_PLATFORM.state.step'))
            playback_data = await page.evaluate("""() => ({
                frames:window.__simulationBenchmark.frames.filter(f=>f.phase==='playback'),
                end_ms:window.__simulationBenchmark.playbackEndMs})""")
            frames = playback_data['frames']
            duration_s = (playback_data['end_ms'] - playback_begin) / 1000
            last = frames[-1]
            source_s = last['source_s'] - first['source_s']
            gaps = [b['at_ms']-a['at_ms'] for a,b in zip([first]+frames, frames)]
            report['playback'] = {'start_step': args.start, 'last_step': last['step'],
                'wall_seconds': duration_s, 'source_seconds': source_s,
                'source_seconds_per_wall_second': source_s/duration_s,
                'delivered_images': len(frames), 'delivered_images_per_second': len(frames)/duration_s,
                'mean_image_interval_ms': statistics.mean(gaps),
                'max_image_interval_ms': max(gaps),
                'source_step_gaps': [b['step']-a['step'] for a,b in zip([first]+frames, frames)],
                'frames': frames}
            check('playback delivers genuine increasing source frames through the requested range',
                  args.end <= last['step'] <= args.end + 1 and
                  all(1 <= gap <= 2 for gap in report['playback']['source_step_gaps']))
            print('PLAYBACK ' + json.dumps({k:v for k,v in report['playback'].items()
                                           if k not in ('frames','source_step_gaps')}, ensure_ascii=False), flush=True)
            await page.screenshot(path=str(args.out/'playback.png'), full_page=True)

            # Adaptive display batching may finish one source tick past the
            # requested range. Compare camera/seek latency at identical poses.
            if last['step'] != args.end:
                await phase('align')
                await page.evaluate('(step)=>MAP_PLATFORM.seek(step)', args.end)
                await wait_presented(args.end)

            await phase('camera')
            camera_rows = []
            box = await page.locator('#viewport').bounding_box()
            x, y = box['x']+box['width']*.65, box['y']+box['height']*.5
            for dx in (40, -80, 40):
                old = await presentation()
                await page.mouse.move(x, y)
                begin = await page.evaluate('performance.now()')
                await page.mouse.down()
                await page.mouse.move(x+dx, y)
                await page.mouse.up()
                await wait_presented(old['step'], old['frame_id'])
                new = await presentation()
                camera_rows.append({'dx_px': dx, 'step': new['step'],
                    'latency_ms': new['at_ms']-begin, 'before': old.get('camera'), 'after': new.get('camera')})
            report['camera'] = latency_summary(camera_rows)
            check('three paused yaw changes produce three matching-step images',
                  len(camera_rows) == 3 and all(row['before'] != row['after'] for row in camera_rows))
            print('CAMERA ' + str(round(report['camera']['median_ms'], 1)) + ' median ms', flush=True)

            await phase('scrub')
            scrub_rows = []
            maximum = await page.evaluate('MAP_PLATFORM.state.scene.frame_count-1')
            for target in (min(args.start+20, maximum), args.start+5, min(args.start+20, maximum)):
                old = await presentation()
                begin = await page.evaluate("""step => {
                    const t=performance.now(),input=document.querySelector('#timeline');
                    input.value=String(step);input.dispatchEvent(new Event('change',{bubbles:true}));return t;
                }""", target)
                await wait_presented(target, old['frame_id'])
                new = await presentation()
                scrub_rows.append({'from_step': old['step'], 'to_step': target,
                                   'direction': 'forward' if target>old['step'] else 'backward',
                                   'latency_ms': new['at_ms']-begin})
            report['scrub'] = latency_summary(scrub_rows)
            print('SCRUB ' + json.dumps(scrub_rows), flush=True)
            monitored = await page.evaluate("""() => {
                const b=window.__simulationBenchmark;b.stop=true;b.observer.disconnect();
                return {frames:b.frames,violations:b.violations,samples:b.samples};
            }""")
            report['presentation_monitor'] = monitored
            if pending_responses:
                await asyncio.gather(*pending_responses)
            check('every browser-visible image matches its step, snapshot and selected map hash',
                  monitored['samples'] > 0 and not monitored['violations'], monitored['violations'])
            check('all presented engine images have a corresponding readable PNG response',
                  {f['frame_id'] for f in monitored['frames']} <=
                  {r['frame_id'] for r in report['png_responses'] if r['valid_png']})
            check('no browser JavaScript or HTTP errors', not report['errors'] and not report['http_errors'],
                  {'javascript': report['errors'], 'http': report['http_errors']})
            report['passed'] = True
        except Exception as error:
            report['passed'] = False
            report['exception'] = repr(error)
            try:
                await page.screenshot(path=str(args.out/'failure.png'), full_page=True)
            except Exception:
                pass
            raise
        finally:
            try:
                await page.evaluate('window.MAP_PLATFORM?.stopPlayback()')
                if 'presentation_monitor' not in report:
                    report['presentation_monitor'] = await page.evaluate("""() => {
                        const b=window.__simulationBenchmark;
                        return b?{frames:b.frames,violations:b.violations,samples:b.samples,
                          stopAt:b.stopAt,playbackDone:b.playbackDone,
                          final_busy:window.MAP_PLATFORM?.state.engineCommandBusy,
                          final_step:window.MAP_PLATFORM?.state.step,
                          final_snapshot:window.MAP_PLATFORM?.state.snapshot?.step}:null;
                    }""")
            except Exception:
                pass
            report['total_wall_seconds'] = time.perf_counter()-started
            (args.out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            await browser.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8095')
    parser.add_argument('--label', required=True)
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    parser.add_argument('--scene', default='nuscenes-scene0980')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--end', type=int, default=40)
    parser.add_argument('--timeout', type=int, default=240, help='Per-operation timeout in seconds')
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    args = parser.parse_args()
    if not args.chromium:
        parser.error('Chromium was not found; pass --chromium')
    if not 0 <= args.start < args.end or args.end-args.start < 20:
        parser.error('Use a source range of at least 20 frames')
    asyncio.run(benchmark(args))


if __name__ == '__main__':
    main()
