"""Exercise the real WASM/WebGL viewer, local file path and evidence bridge."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
from threading import Thread
from zipfile import ZipFile
from PIL import Image,ImageChops
from playwright.sync_api import sync_playwright

p=argparse.ArgumentParser();p.add_argument('directory',type=Path);root=p.parse_args().directory.resolve()
checks=[];errors=[];failed=[]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root)));Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}/'
def check(name,ok):
    if not ok:raise AssertionError(name)
    checks.append(name);print('PASS '+name,flush=True)
def loaded(page,mode='scene'):
    page.wait_for_function('(mode)=>window.AIE_RERUN?.mode===mode&&!AIE_RERUN.busy&&AIE_RERUN.viewer?.ready&&!!AIE_RERUN.recordingId',arg=mode,timeout=90000)
try:
 with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader'])
    context=browser.new_context(viewport={'width':1500,'height':1150});page=context.new_page();requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append({'url':r.url,'method':r.method}))
    page.on('response',lambda r:failed.append((r.url,r.status)) if r.status>=400 else None)
    page.goto(base+'en/index.html')
    check('viewer is lazy: ordinary scene page does not download WASM',not any('.wasm' in r['url'] for r in requests))
    page.locator('#rerun-nav').click();loaded(page)
    check('Rerun tab mounts a real visible WebGL canvas',page.locator('#rerun-host canvas').is_visible())
    check('known scene exposes all 41 frames',page.evaluate("AIE_RERUN.viewer.get_time_range(AIE_RERUN.recordingId,'frame').max===40"))
    check('default playback uses source elapsed time, not 30 fps frame sequence',page.evaluate("AIE_RERUN.viewer.get_active_timeline(AIE_RERUN.recordingId)==='elapsed_s'"))
    check('English wrapper has no untranslated Chinese',not re.search('[\u3400-\u9fff]',page.locator('body').inner_text()))
    page.locator('#rerun-frame').fill('30');page.locator('#rerun-frame').dispatch_event('input');page.wait_for_timeout(800)
    check('website slider seeks the real recorded timestamp',page.evaluate("S.frame===30&&Math.abs(AIE_RERUN.viewer.get_current_time(AIE_RERUN.recordingId,'elapsed_s')-R.frames[30].elapsed_s*1e9)<1000"))
    page.evaluate("AIE_RERUN.viewer.set_current_time(AIE_RERUN.recordingId,'elapsed_s',R.frames[12].elapsed_s*1e9)")
    page.wait_for_function('S.frame===12',timeout=15000)
    check('native time updates select the matching evidence frame',page.locator('#rerun-frame').input_value()=='12')
    if page.evaluate('!!window.DECISION_STUDIO'):
        check('native timeline also updates the new decision workspace',page.evaluate('DECISION_STUDIO.state.frame===12'))
        page.locator('#rerun-candidates button').first.click()
        check('Rerun candidate selection reaches the decision workspace',page.evaluate('DECISION_STUDIO.state.caseId===S.selected'))
    page.locator('#rerun-frame').fill('0');page.locator('#rerun-frame').dispatch_event('input');page.wait_for_timeout(700)
    page.screenshot(path=str(root/'rerun_english.png'),full_page=True)
    canvas=page.locator('#rerun-host canvas');rect=canvas.bounding_box();before=canvas.screenshot()
    page.mouse.move(rect['x']+rect['width']*.25,rect['y']+200);page.mouse.down();page.mouse.move(rect['x']+rect['width']*.25+85,rect['y']+240,steps=12);page.mouse.up();page.wait_for_timeout(400)
    after=canvas.screenshot();a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(after)).convert('RGB')
    diff=ImageChops.difference(a,b).crop((20,65,int(a.width*.45),400));changed=sum(v>25 for v in diff.convert('L').getdata())/(diff.width*diff.height)
    check('dragging the native map changes the rendered map',changed>.01)
    page.locator('#rerun-inspect').click();page.wait_for_timeout(300)
    page.screenshot(path=str(root/'rerun_inspector.png'),full_page=True)
    check('candidate evidence links retain the current scene frame','frame=0' in page.locator('#rerun-review').get_attribute('href'))
    page.evaluate('AIE_RERUN.viewer.set_playing(AIE_RERUN.recordingId,true)');page.wait_for_timeout(1800)
    now=page.evaluate('S.frame');check('native playback advances at roughly the real scene rate',1<=now<=7)
    page.locator('[data-page="drive"]').click();page.wait_for_timeout(150)
    paused=page.evaluate("AIE_RERUN.viewer.get_current_time(AIE_RERUN.recordingId,'elapsed_s')");page.wait_for_timeout(700)
    check('leaving the viewer pauses native playback',page.evaluate("AIE_RERUN.viewer.get_current_time(AIE_RERUN.recordingId,'elapsed_s')")==paused)
    page.locator('#rerun-nav').click()
    page.locator('#rerun-file').set_input_files({'name':'broken.rrd','mimeType':'application/octet-stream','buffer':b'not a recording'*5})
    page.wait_for_function('document.getElementById("rerun-status").classList.contains("error")')
    check('invalid local file is rejected without destroying the open scene',page.evaluate("AIE_RERUN.mode==='scene'&&AIE_RERUN.viewer.ready"))
    wrong=bytearray((root/'assets/rerun/original-overlay.rrd').read_bytes());wrong[5]=42
    page.locator('#rerun-file').set_input_files({'name':'new-version.rrd','mimeType':'application/octet-stream','buffer':bytes(wrong)})
    check('unsupported RRD version has an explicit error','0.42.1' in page.locator('#rerun-status').inner_text())
    before_local=len(requests)
    page.locator('#rerun-file').set_input_files(root/'assets/rerun/scene0980-delivered-en.rrd');loaded(page,'local')
    check('local RRD is decoded by the real viewer',bool(page.evaluate('AIE_RERUN.recordingId')))
    check('local file does not falsely synchronize evaluation',page.locator('#rerun-frame').is_disabled() and not page.locator('#rerun-context').is_visible())
    local_requests=requests[before_local:]
    check('local file uses no upload request or external URL',all(x['method']=='GET' and x['url'].startswith(base) for x in local_requests))
    page.locator('#rerun-unload').click();check('unload removes the native canvas and releases the active viewer',page.locator('#rerun-host canvas').count()==0 and page.evaluate('AIE_RERUN.viewer===null'))
    page.locator('#rerun-overlay').click();loaded(page,'overlay')
    check('original delivered overlay loads with its own identity',page.evaluate("AIE_RERUN.recordingId==='8a8001d9-046d-435c-bf22-63c2568a80ba'"))
    check('partial overlay is described and kept independent','not the full camera' in page.locator('#rerun-status').inner_text() and page.locator('#rerun-frame').is_disabled())
    page.locator('#rerun-default').click();loaded(page)
    check('restoring scene re-enables the evidence bridge',not page.locator('#rerun-frame').is_disabled())
    page.locator('#rerun-frame').fill('30');page.locator('#rerun-frame').dispatch_event('input')
    page.locator('#language-switch').click();page.wait_for_url('**/zh/index.html*');loaded(page)
    check('Chinese Rerun route preserves the chosen scene frame',page.evaluate('S.frame')==30)
    check('Chinese Rerun route loads the Chinese recording',page.evaluate("AIE_RERUN.recordingId==='8508eda8-6a91-51f6-b282-630f0bfeced7'"))
    check('Chinese controls and native-language limit are explicit','打开本地 RRD' in page.locator('#rerun-page').inner_text() and '内置菜单仍为英文' in page.locator('#rerun-page').inner_text())
    page.screenshot(path=str(root/'rerun_chinese.png'),full_page=True)
    page.locator('[data-page="issues"]').click();check('audit distinguishes four fixed bugs from eleven existing evidence issues',page.locator('.rr-audit details').count()==4 and page.locator('.issue-card').count()==11)
    with page.expect_download() as event:page.locator('.rr-audit a[download]').first.click()
    with ZipFile(event.value.path()) as z:xml=z.read('word/document.xml').decode()
    check('Chinese audit downloads as a valid Word document','运行检查' in xml and 'human.pedestrian.construction_worker' in xml)
    page.locator('#language-switch').click();page.wait_for_url('**/en/index.html*')
    with page.expect_download() as event:page.locator('.rr-audit a[download]').first.click()
    with ZipFile(event.value.path()) as z:xml=z.read('word/document.xml').decode()
    check('English audit is a separate English Word document','Runtime Audit' in xml and not re.search('[\u3400-\u9fff]',xml))
    page.locator('#rerun-nav').click();loaded(page)
    page.evaluate("()=>{AIE_RERUN.load('scene');AIE_RERUN.release();}");page.wait_for_timeout(800)
    check('cancel during restart leaves no orphan viewer',page.locator('#rerun-host canvas').count()==0 and page.evaluate('!AIE_RERUN.busy&&AIE_RERUN.viewer===null'))
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/'rerun_mobile.png'),full_page=True)
    check('mobile wrapper stays within the viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    check('loaded source data still has zero real base-map writes',page.evaluate('R.maintenance.base_map_updates.length')==0)
    check('no failed asset requests in the normal flow',not failed)
    check('normal interaction has no uncaught JavaScript exceptions',not errors)
    # A blocked WASM request must leave a usable fallback instead of a blank site.
    fallback=browser.new_context(viewport={'width':1400,'height':950});bad=fallback.new_page()
    bad.route('**/*.wasm',lambda route:route.abort())
    bad.goto(base+'en/index.html#rerun');bad.wait_for_function('document.getElementById("rerun-status").classList.contains("error")',timeout=30000)
    check('viewer load failure has an actionable visible error','Unable to open' in bad.locator('#rerun-status').inner_text())
    bad.locator('[data-page="drive"]').click();bad.locator('#scene-frame').fill('20');bad.locator('#scene-frame').dispatch_event('input')
    check('ordinary scene controls remain usable after viewer failure',bad.evaluate('S.frame')==20)
    fallback.close();browser.close()
    result={'status':'PASS','checks':checks,'count':len(checks),'browser':'Chromium / WebGL / SwiftShader','limits':'Cloud Chromium only; no physical Mac or real-vehicle validation','page_errors':errors,'normal_failed_requests':failed}
    (root/'rerun_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('PASS: '+str(len(checks))+' Rerun browser checks')
finally:server.shutdown()
