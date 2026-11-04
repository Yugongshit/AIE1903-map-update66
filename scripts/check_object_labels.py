"""Verify semantic names, truthful geometry, cross-view selection and label layout."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
root=parser.parse_args().directory.resolve();checks=[]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root)))
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
def check(name,result):
    assert result,name
    checks.append(name)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1600,'height':1150})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        check('legend separates object class from occupancy region',all(label in page.locator('#semantic-key').inner_text() for label in ['车辆','人员','道路物体','其他','占用候选范围']))
        check('front view displays actual Chinese names',page.locator('#front-object-labels button').count()>=3 and '路障' in page.locator('#front-object-labels').inner_text())
        check('default browser lists source objects within the camera projection',page.locator('#object-list button').count()==page.evaluate('CAM.views[0].boxes.length'))
        check('labels carry the exact source category color',page.evaluate('''()=>[...document.querySelectorAll('#front-object-labels button')].every(b=>b.style.getPropertyValue('--object-color')===CAM.views[S.frame].annotations.find(a=>a.instance_token===b.dataset.objectToken).color)'''))
        check('front labels do not overlap each other',page.evaluate('''()=>{const r=[...document.querySelectorAll('#front-object-labels button')].map(x=>x.getBoundingClientRect());return r.every((a,i)=>r.slice(i+1).every(b=>a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top));}'''))
        label=page.locator('#bird-object-labels button').first
        selected=label.get_attribute('data-object-token');label.click()
        check('bird-view labels select their own annotated instance',page.evaluate('S.objectToken')==selected)
        box=page.locator('#bird-object-labels button').first.bounding_box();before=page.evaluate('S.center')
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+40,box['y']+box['height']/2+20,steps=5);page.mouse.up()
        check('dragging over a name label still pans the map',page.evaluate('!S.follow') and page.evaluate('S.center')!=before)
        page.locator('#map-follow').click()
        page.screenshot(path=str(root/'named_objects_preview.png'),full_page=True)
        vehicle=page.evaluate('CAM.views[0].boxes.find(b=>b.category==="vehicle.truck").instance_token')
        page.locator(f'#object-list [data-object-token="{vehicle}"]').click()
        check('selecting a truck exposes its identity and measurements','卡车' in page.locator('#object-title').inner_text() and page.locator('.object-dimensions b').count()==3 and page.evaluate('S.objectToken')==vehicle)
        dimensions=page.evaluate('CAM.views[S.frame].annotations.find(a=>a.instance_token===S.objectToken).dimensions_lwh_m')
        check('displayed dimensions preserve source values',page.locator('.object-dimensions b').all_inner_texts()==[f'{x:.2f}' for x in dimensions])
        page.locator('#object-provenance').click();check('source dialog distinguishes annotations from predictions','vehicle.truck' in page.locator('#dialog-body').inner_text() and '不是本轮模型预测' in page.locator('#dialog-body').inner_text());page.locator('#close-dialog').click()
        page.locator('#locate-object').click();check('map locates the selected source object',page.evaluate('!S.follow && S.center.every((v,i)=>v===CAM.views[S.frame].annotations.find(a=>a.instance_token===S.objectToken).center_xyz_m[i])'))
        check('front and bird view share one selected instance',page.locator(f'#front-object-labels [data-object-token="{vehicle}"]').get_attribute('class').endswith('selected') and page.locator(f'#bird-object-labels [data-object-token="{vehicle}"]').get_attribute('class').endswith('selected'))
        page.locator('#show-object-names').uncheck();check('names can be hidden while geometry remains selectable',page.locator('#front-object-labels button').count()==0 and page.evaluate('S.objectFrontHits.length>0'))
        # Click a source edge away from borders and the legend, with labels hidden.
        target=page.evaluate('''()=>{for(const b of CAM.views[S.frame].boxes){if(!b.category.includes('barrier'))continue;for(const s of b.segments){const p=[(s[0][0]+s[1][0])/2,(s[0][1]+s[1][1])/2];if(p[0]>30&&p[0]<1570&&p[1]>100&&p[1]<740)return {token:b.instance_token,p};}}return null;}''')
        check('an actual projected barrier edge is available for picking',target is not None)
        rect=page.locator('#front-canvas').bounding_box();page.mouse.click(rect['x']+target['p'][0]*rect['width']/1600,rect['y']+target['p'][1]*rect['height']/900)
        check('clicking the projected object selects its source record',page.evaluate('S.objectToken')==target['token'])
        page.locator('#show-object-names').check()
        page.locator('#object-family').select_option('vehicle')
        check('vehicle filter affects both rendered objects and list',page.evaluate('S.objectFrontHits.every(h=>CAM.views[S.frame].annotations.find(a=>a.instance_token===h.token).family==="vehicle") && S.objectBirdHits.every(h=>CAM.views[S.frame].annotations.find(a=>a.instance_token===h.token).family==="vehicle")') and all('路障' not in t for t in page.locator('#object-list button').all_inner_texts()))
        check('hidden selection is explained rather than relabeled','筛选隐藏' in page.locator('#object-detail').inner_text())
        page.locator('#object-family').select_option('all');page.locator('#objects-front-only').uncheck()
        check('all source annotations remain available outside front view',page.locator('#object-list button').count()==39)
        absent=page.evaluate('''()=>{const end=new Set(CAM.views[40].annotations.map(a=>a.instance_token));return CAM.views[0].annotations.find(a=>!end.has(a.instance_token)).instance_token;}''')
        page.locator(f'#object-list [data-object-token="{absent}"]').click()
        page.locator('#scene-frame').fill('40');page.locator('#scene-frame').dispatch_event('input')
        check('missing frame annotation is never presented as disappearance','不代表它已经消失' in page.locator('#object-detail').inner_text() and page.locator('#locate-object').is_disabled())
        page.locator('#scene-frame').fill('30');page.locator('#scene-frame').dispatch_event('input');page.locator('#object-family').select_option('person')
        check('construction workers retain their more specific label',any('施工人员' in t for t in page.locator('#object-list button').all_inner_texts()))
        page.locator('#object-family').select_option('road_object')
        check('cones debris and bicycle racks keep distinct source names',all(any(name in t for t in page.locator('#object-list button').all_inner_texts()) for name in ['交通锥','杂物','自行车架']))
        cone=page.locator('#object-list button').filter(has_text='交通锥').first;cone.click()
        check('occupancy associations remain distinct from object identity','交通锥' in page.locator('#object-title').inner_text() and '不是精细三维模型' in page.locator('.object-source-note').inner_text())
        page.locator('#object-family').select_option('all');page.locator('#objects-front-only').check()
        page.screenshot(path=str(root/'named_objects_dense_frame.png'),full_page=True)
        page.locator('#show-boxes').uncheck();check('shape switch also hides associated name tags',page.locator('#front-object-labels button').count()==0 and page.locator('#bird-object-labels button').count()==0);page.locator('#show-boxes').check()
        page.locator('[data-camera="video"]').click();check('new labels do not falsely annotate the unsynchronized source video',page.locator('#front-object-labels').is_hidden())
        page.locator('[data-camera="reconstruction"]').click();page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(150)
        check('semantic controls fit a mobile viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        check('mobile labels stay inside their image',page.evaluate('''()=>{const c=document.getElementById('front-canvas').getBoundingClientRect();return [...document.querySelectorAll('#front-object-labels button')].every(b=>{const r=b.getBoundingClientRect();return r.left>=c.left&&r.right<=c.right+1&&r.top>=c.top&&r.bottom<=c.bottom+1;});}'''))
        page.screenshot(path=str(root/'named_objects_mobile.png'),full_page=True)
        check('real map and evaluation are unaffected by annotation controls',page.evaluate('R.maintenance.base_map_updates.length===0 && R.accuracy.f1===null'))
        check('no JavaScript errors',not errors)
        (root/'object_label_checks.json').write_text(json.dumps({'status':'PASS','checks':checks,'javascript_errors':errors},ensure_ascii=False,indent=2)+'\n')
        print('PASS:',len(checks),'semantic annotation checks')
        browser.close()
finally:server.shutdown();server.server_close();thread.join()
