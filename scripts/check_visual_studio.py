"""Exercise actual user controls, isolated write results, media and issue delivery."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from zipfile import ZipFile

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
args=parser.parse_args();root=args.directory.resolve();checks=[]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root)))
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
def check(name,result):
    assert result,name
    checks.append(name)
try:
    with sync_playwright() as pw,TemporaryDirectory(prefix='studio-browser-fixtures-') as fixtures:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1600,'height':1100})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        url=f'http://127.0.0.1:{server.server_port}/'
        page.goto(url)
        check('default view uses supplied nuScenes and explicitly says reconstruction',
              page.evaluate('DATA.dataset_id==="nuscenes" && S.mode==="reconstruction"')
              and '非原始照片' in page.locator('#front-source').inner_text())
        check('41 camera-time projections and no invented source images',page.evaluate('CAM.views.length===41 && CAM.available_images===0'))
        check('real trajectory never masquerades as a planned route',page.evaluate('CAM.route_type==="recorded_ego_path_not_planned" && DATA.planning.route===null'))
        page.screenshot(path=str(root/'studio_driving_preview.png'),full_page=True)
        # Time controls follow actual source frames.
        page.locator('#scene-frame').fill('30');page.locator('#scene-frame').dispatch_event('input')
        check('timeline updates camera and selected frame together',page.evaluate('S.frame===30') and '30 / 40' in page.locator('#front-frame').inner_text())
        page.locator('#next-frame').click();check('single-frame stepping',page.evaluate('S.frame')==31)
        before=page.evaluate('S.zoom');page.locator('#map-zoom-in').click();check('map zoom control',page.evaluate('S.zoom')>before)
        box=page.locator('#bird-canvas').bounding_box();center=page.evaluate('S.center')
        page.mouse.move(box['x']+box['width']*.4,box['y']+box['height']*.4);page.mouse.down();page.mouse.move(box['x']+box['width']*.4+40,box['y']+box['height']*.4+20,steps=5);page.mouse.up()
        check('map supports user panning',page.evaluate('!S.follow') and page.evaluate('S.center')!=center)
        page.locator('#map-follow').click();check('follow mode returns to actual ego position',page.evaluate('JSON.stringify(S.center)===JSON.stringify(frame().ego.xy)'))
        page.locator('#show-route').uncheck();check('recorded route overlay can be toggled',not page.locator('#show-route').is_checked());page.locator('#show-route').check()
        page.locator('#scene-speed').select_option('4');page.locator('#play-scene').click();page.wait_for_function('S.frame>=33');page.locator('#play-scene').click()
        check('playback advances real source frames',page.evaluate('S.frame')>=33)
        page.locator('#camera-details').click();check('projection provenance is inspectable','calibration token' in page.locator('#dialog-body').inner_text());page.locator('#close-dialog').click()
        page.locator('[data-camera="video"]').click()
        page.wait_for_function('document.getElementById("perception-video").readyState>=1')
        check('original perception recording plays as a real video',page.evaluate('document.getElementById("perception-video").duration>32') and '不自动同步' in page.locator('#camera-time-note').inner_text())
        page.locator('[data-camera="photo"]').click();check('missing camera photo is an honest actionable state',page.locator('#photo-empty').is_visible())
        page.locator('#scene-frame').fill('0');page.locator('#scene-frame').dispatch_event('input')
        filename=page.evaluate('CAM.views[0].filename.split("/").at(-1)')
        wrong=Path(fixtures)/'wrong';wrong.mkdir();Image.new('RGB',(10,10),'gray').save(wrong/filename)
        page.locator('#camera-files').set_input_files(str(wrong));page.wait_for_function('document.getElementById("toast").textContent.includes("尺寸不符")')
        check('incorrect camera dimensions are rejected',page.evaluate('S.images.size')==0)
        valid=Path(fixtures)/'valid';valid.mkdir();test_image=Image.new('RGB',(1600,900),'#3c4652');ImageDraw.Draw(test_image).text((50,50),'BROWSER TEST ONLY - NOT SCENE DATA',fill='white');test_image.save(valid/filename)
        page.locator('#camera-files').set_input_files(str(valid));page.wait_for_function('S.images.size===1 && S.photo!==null')
        check('matched local photo is accepted and not uploaded',page.locator('#photo-empty').is_hidden() and page.evaluate('S.photo.width===1600'))
        page.locator('#next-frame').click();check('a missing next photo cannot reuse the previous frame',page.locator('#photo-empty').is_visible() and page.evaluate('S.photo===null'))
        page.locator('[data-camera="reconstruction"]').click();page.locator('#map-fit').click()
        # Pick an actual polygon interior. The export is only a request, never a computed route.
        goal=page.evaluate('''()=>{for(const e of R.map.elements){if(e.layer!=='lane')continue;const pts=e.geometry_city.coordinates[0],p=centroid(pts);if(laneAt(p)){const v=S.birdTransform,q=viewTransform(v,v.width,v.height)(p);if(q[0]>10&&q[1]>10&&q[0]<v.width-10&&q[1]<v.height-10)return q;}}return null;}''')
        check('an actual reference-lane interior is available for goal selection',goal is not None)
        page.locator('#set-goal').click();box=page.locator('#bird-canvas').bounding_box();page.mouse.click(box['x']+goal[0],box['y']+goal[1])
        check('user can set a planning goal without inventing a route',page.evaluate('S.goal!==null && S.goal.route===null'))
        with page.expect_download() as info:page.locator('#export-plan-request').click()
        request=Path(fixtures)/'planning-request.json';info.value.save_as(str(request));request_data=json.loads(request.read_text())
        check('planning request includes source time and map identity',request_data['dataset_id']=='nuscenes' and request_data['decision_timestamp_ns'] and request_data['map_sha256'] and request_data['route'] is None)
        page.locator('#go-changes').click()
        check('real maintenance makes zero base-map writes explicit',page.locator('#real-change-view').is_visible() and page.evaluate('R.maintenance.base_map_updates.length===0'))
        page.locator('#real-before').click();before=page.locator('#event-canvas').evaluate('(c)=>c.toDataURL()')
        page.locator('#real-after').click();after=page.locator('#event-canvas').evaluate('(c)=>c.toDataURL()')
        check('actual event-layer before and after have visible differences',before!=after)
        page.locator('#event-frame').fill('30');page.locator('#event-frame').dispatch_event('input');check('event layer follows its actual frame records','帧 30' in page.locator('#event-count').inner_text())
        page.screenshot(path=str(root/'studio_real_events_preview.png'),full_page=True)
        page.locator('#start-experiment').click();check('controlled trial is clearly separated from real findings',page.locator('#experiment-view').is_visible() and '三次复访由试验设定' in page.locator('.experiment-banner').inner_text())
        page.locator('[data-stage="2"]').click();check('two simulated visits do not write a map',page.evaluate('currentStage().versions.length===1') and page.locator('#experiment-export').is_disabled())
        page.locator('[data-stage="3"]').click();check('third visit exposes actual engine patch and changed geometry',page.evaluate('currentStage().versions.length===2 && currentStage().versions[1].patch.operation==="geometry_change"') and '已写入' in page.locator('#experiment-result').inner_text())
        page.locator('#wipe').fill('0');page.locator('#wipe').dispatch_event('input');new_map=page.locator('#change-canvas').evaluate('(c)=>c.toDataURL()')
        page.locator('#wipe').fill('100');page.locator('#wipe').dispatch_event('input');old_map=page.locator('#change-canvas').evaluate('(c)=>c.toDataURL()')
        check('wipe slider reveals visibly different before and after maps',new_map!=old_map)
        page.locator('#wipe').fill('50');page.locator('#wipe').dispatch_event('input')
        page.screenshot(path=str(root/'studio_map_write_preview.png'),full_page=True)
        with page.expect_download() as info:page.locator('#experiment-export').click()
        trial=Path(fixtures)/'trial.json';info.value.save_as(str(trial));trial_data=json.loads(trial.read_text())
        check('patch export cannot be mistaken for real change evidence',trial_data['dataset_type']=='controlled_map_write_test' and trial_data['real_map_modified'] is False and trial_data['engine_output']['versions'][-1]['patch']['after'])
        page.locator('[data-stage="4"]').click()
        check('rollback restores exact original coordinates and adds history',page.evaluate('JSON.stringify(currentStage().map.elements)===JSON.stringify(LAB.baseline.elements) && currentStage().versions.length===3'))
        page.locator('#experiment-offset').select_option('2');check('changing offset starts a fresh isolated scenario',page.evaluate('S.stage===0 && currentExperiment().offset_m===1.2'))
        page.locator('[data-stage="3"]').click();check('second offset uses its matching engine result','1.20 m' in page.locator('#delta-label').inner_text())
        check('interactive trials never change the real map or labels',page.evaluate('R.maintenance.base_map_updates.length===0 && R.accuracy.f1===null'))
        page.locator('[data-page="issues"]').click();check('all eleven actionable issues are visible',page.locator('.issue-card').count()==11)
        page.locator('[data-issue-filter="P0"]').click();check('six blocking issues filter correctly',page.locator('.issue-card').count()==6)
        page.locator('.issue-card').first.locator('summary').click();check('issue shows owner next action and acceptance','怎样算完成' in page.locator('.issue-card').first.inner_text())
        with page.expect_download() as info:page.locator('a[href="assets/AIE1903_issues_and_next_steps.docx"]').click()
        word=Path(fixtures)/'issues.docx';info.value.save_as(str(word))
        with ZipFile(word) as z:text=z.read('word/document.xml').decode()
        check('one-click Word download contains the actual issue report','六个 LD' in text and '32 MiB' in text and '94.1%' in text)
        page.screenshot(path=str(root/'studio_issues_preview.png'),full_page=True)
        for name in ['drive','changes','issues']:
            page.set_viewport_size({'width':390,'height':844});page.locator('[data-page="'+name+'"]').click()
            check('mobile layout '+name,page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.goto(url+'#case=S0980-LD-004&frame=30&tab=scene')
        page.wait_for_url('**/review/index.html#*')
        check('older shared evidence links still reach the original workbench',page.locator('#candidate').input_value()=='S0980-LD-004')
        check('no browser script errors',not errors)
        report={'status':'PASS','checks':checks,'javascript_errors':errors}
        (root/'studio_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('PASS:',len(checks),'studio interaction checks')
        browser.close()
finally:server.shutdown();server.server_close();thread.join()
