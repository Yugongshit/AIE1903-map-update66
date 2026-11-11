"""Check toolbar/status stability while real MetaDrive images advance.

Start run_simulation_platform.py with the simulation interpreter first. This
checker requires Playwright and Chromium. --observe-only records the old
flicker without failing its stability assertions, for before/after evidence.
"""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import shutil
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


MONITOR_JS = """() => {
  const m=window.__controlMonitor={samples:[],frames:[],stopped:false,lastFrame:null};
  function sample(source) {
    const s=MAP_PLATFORM.state;
    if(m.stopped||!s.playing||s.mode!=='engine')return;
    const ids=['start-engine','map-version','pending-state'];
    const controls=Object.fromEntries(ids.map(id=>{
      const e=document.getElementById(id),c=getComputedStyle(e);
      return [id,{disabled:Boolean(e.disabled),visible:c.display!=='none'&&c.visibility!=='hidden',
        opacity:c.opacity,background:c.backgroundColor,color:c.color,
        text:id==='pending-state'?e.textContent:null}];
    }));
    const d=s.engineDisplayedFrame,i=document.getElementById('engine-frame');
    m.samples.push({at_ms:performance.now(),source,step:s.step,busy:s.engineCommandBusy,controls});
    if(source==='animation'&&d?.frame_id!==m.lastFrame&&i.complete&&i.naturalWidth>0&&!i.hidden){
      m.frames.push({frame_id:d.frame_id,step:d.step,snapshot_step:s.snapshot?.step,
        image_step:Number(i.dataset.step),width:i.naturalWidth,height:i.naturalHeight});
      m.lastFrame=d.frame_id;
    }
  }
  m.observer=new MutationObserver(()=>sample('mutation'));
  for(const id of ['start-engine','map-version','pending-state'])
    m.observer.observe(document.getElementById(id),{attributes:true,childList:true,subtree:true,characterData:true});
  function tick(){if(m.stopped)return;sample('animation');requestAnimationFrame(tick);}
  requestAnimationFrame(tick);
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scene', default='nuscenes-scene0980')
    parser.add_argument('--start', type=int, default=120)
    parser.add_argument('--frames', type=int, default=6)
    parser.add_argument('--observe-only', action='store_true')
    parser.add_argument('--frontend-script', type=Path, help='Serve a saved frontend script for before/after comparison')
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    args = parser.parse_args()
    if args.frames < 6 or args.start < 0:
        parser.error('Use at least six displayed frames and a nonnegative start')
    if not args.chromium:
        parser.error('Chromium not found; pass --chromium')
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'url': args.url, 'scene': args.scene, 'start_step': args.start,
              'observe_only': args.observe_only, 'checks': [], 'errors': [], 'commands': []}

    def check(name, condition, details=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'details': details})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.set_default_timeout(180000)
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        if args.frontend_script:
            page.route('**/simulation_platform.js', lambda route: route.fulfill(
                path=str(args.frontend_script.resolve()), content_type='application/javascript'))

        def request_seen(request):
            if request.method == 'POST' and urlparse(request.url).path in ('/api/engine/open', '/api/engine/command'):
                report['commands'].append({'path': urlparse(request.url).path, 'body': request.post_data_json})
        page.on('request', request_seen)

        def wait_presented(step=None, version='updated'):
            print('WAIT_PRESENTED '+json.dumps({'step':step,'version':version}), flush=True)
            page.wait_for_function("""({step,version})=>{
              const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,i=document.getElementById('engine-frame');
              return !s.engineCommandBusy&&!s.engineFrameError&&d&&d.version===version&&
                (step==null||d.step===step)&&s.step===d.step&&s.snapshot?.step===d.step&&
                i.complete&&i.naturalWidth>0&&!i.hidden&&Number(i.dataset.step)===d.step;
            }""", arg={'step': step, 'version': version})

        def controls_unlocked():
            return page.evaluate("""()=>!document.getElementById('start-engine').disabled&&
              !document.getElementById('map-version').disabled""")

        try:
            with page.expect_response(lambda r: urlparse(r.url).path == '/simulation_platform.js') as js_response:
                page.goto(args.url.rstrip('/')+'/?scene='+args.scene, wait_until='networkidle')
            report['frontend_sha256'] = sha256(js_response.value.body()).hexdigest()
            page.wait_for_function('(key)=>MAP_PLATFORM.state.key===key&&MAP_PLATFORM.state.snapshot?.step===0', arg=args.scene)
            check('source range leaves enough frames for continuous playback',
                  page.evaluate('(last)=>MAP_PLATFORM.state.scene.frame_count>last', args.start+args.frames*2+2))
            page.evaluate('(step)=>MAP_PLATFORM.seek(step)', args.start)
            page.wait_for_function('(step)=>MAP_PLATFORM.state.snapshot?.step===step', arg=args.start)
            before_open = len(report['commands'])
            page.evaluate("""()=>{
              const b=document.getElementById('start-engine');b.click();b.click();
            }""")
            page.wait_for_function('MAP_PLATFORM.state.engineCommandBusy')
            check('startup locks launch and road-version controls against duplicate requests',
                  page.locator('#start-engine').is_disabled() and page.locator('#map-version').is_disabled())
            wait_presented(args.start)
            check('double click starts exactly one engine job',
                  len([c for c in report['commands'][before_open:] if c['path']=='/api/engine/open']) == 1)
            check('initial scene is paused and controls unlock after a decoded frame',
                  controls_unlocked() and not page.evaluate('MAP_PLATFORM.state.playing'))
            page.evaluate(MONITOR_JS)
            page.locator('#play-pause').click()
            page.wait_for_function('(count)=>window.__controlMonitor.frames.length>=count', arg=args.frames+1)
            page.screenshot(path=str(args.out/'playing.png'), full_page=True)
            page.evaluate('MAP_PLATFORM.stopPlayback()')
            wait_presented()
            monitored = page.evaluate("""()=>{
              const m=window.__controlMonitor;m.stopped=true;m.observer.disconnect();
              return {samples:m.samples,frames:m.frames};
            }""")
            report['monitor'] = monitored
            check('at least six distinct advancing real images are displayed with matching timestamps',
                  len(monitored['frames']) >= args.frames+1 and
                  all(a['step'] < b['step'] for a,b in zip(monitored['frames'],monitored['frames'][1:])) and
                  all(f['step']==f['snapshot_step']==f['image_step'] for f in monitored['frames']))
            signatures = {}
            for control in ('start-engine', 'map-version', 'pending-state'):
                rows = [sample['controls'][control] for sample in monitored['samples']]
                distinct = {json.dumps(row, sort_keys=True) for row in rows}
                changes = sum(a != b for a,b in zip(rows, rows[1:]))
                signatures[control] = {'distinct_states': len(distinct), 'transitions': changes,
                                       'states': [json.loads(value) for value in sorted(distinct)]}
            report['control_stability'] = signatures
            print('CONTROL_STABILITY '+json.dumps(signatures,ensure_ascii=False), flush=True)
            if not args.observe_only:
                for control in signatures:
                    check(control+' stays visually stable across playback frames',
                          signatures[control]['distinct_states']==1, signatures[control])
            check('pause drains the current command and unlocks launch and road-version controls',
                  controls_unlocked() and not page.evaluate('MAP_PLATFORM.state.playing'))
            paused_step = page.evaluate('MAP_PLATFORM.state.step')
            page.locator('#next-frame').click()
            wait_presented(paused_step+1)
            check('manual next-frame still advances exactly one source frame while paused',
                  controls_unlocked() and not page.evaluate('MAP_PLATFORM.state.playing'))
            during_pause = page.evaluate("""()=>{
              MAP_PLATFORM.play();MAP_PLATFORM.stopPlayback();
              return {busy:MAP_PLATFORM.state.engineCommandBusy,playing:MAP_PLATFORM.state.playing,
                launchDisabled:document.getElementById('start-engine').disabled,
                versionDisabled:document.getElementById('map-version').disabled};
            }""")
            check('pausing an in-flight advance keeps conflicting controls locked',
                  during_pause['busy'] and not during_pause['playing'] and
                  during_pause['launchDisabled'] and during_pause['versionDisabled'], during_pause)
            wait_presented()
            check('controls unlock when the paused in-flight advance finishes', controls_unlocked())
            next_step = page.evaluate('MAP_PLATFORM.state.step')
            before_switch = len(report['commands'])
            page.locator('#map-version').select_option('original')
            page.wait_for_function('MAP_PLATFORM.state.engineCommandBusy')
            check('road-version reload locks conflicting controls',
                  page.locator('#start-engine').is_disabled() and page.locator('#map-version').is_disabled())
            wait_presented(next_step, 'original')
            check('road-version switch reloads once and keeps the displayed source frame',
                  len([c for c in report['commands'][before_switch:] if c['path']=='/api/engine/open']) == 1
                  and controls_unlocked())
            page.locator('[data-mode=review]').click()
            review_step = page.evaluate('MAP_PLATFORM.state.step')
            page.locator('#play-pause').click()
            page.wait_for_function('(step)=>MAP_PLATFORM.state.step>=step+2', arg=review_step)
            check('review playback does not inherit the engine playback control lock',
                  controls_unlocked() and page.evaluate('MAP_PLATFORM.state.mode')=='review')
            page.evaluate('MAP_PLATFORM.stopPlayback()')
            check('no uncaught browser errors', not report['errors'], report['errors'])
            report['passed'] = True
        except Exception as error:
            report['passed'] = False
            report['exception'] = repr(error)
            try:
                report['failure_ui'] = page.evaluate("""()=>{
                  const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,i=document.getElementById('engine-frame');
                  return {busy:s.engineCommandBusy,playing:s.playing,error:s.engineFrameError,step:s.step,
                    version:s.version,displayed_step:d?.step,displayed_version:d?.version,
                    snapshot_step:s.snapshot?.step,image_step:i?.dataset.step,hidden:i?.hidden,
                    complete:i?.complete,width:i?.naturalWidth,monitor:window.__controlMonitor?.frames};
                }""")
                print('FAILURE_UI '+json.dumps(report['failure_ui'],ensure_ascii=False), flush=True)
                page.screenshot(path=str(args.out/'failure.png'), full_page=True)
            except Exception:
                pass
            raise
        finally:
            try:
                page.evaluate('window.MAP_PLATFORM?.stopPlayback()')
            except Exception:
                pass
            (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            browser.close()


if __name__ == '__main__':
    main()
