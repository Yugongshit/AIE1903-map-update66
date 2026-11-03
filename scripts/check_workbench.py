"""Exercise user interactions, review persistence, and the deployable static page."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
args=parser.parse_args();root=args.directory.resolve()
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root)))
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
checks=[]
def check(name,result):
    assert result,name
    checks.append(name)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1600,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        url=f'http://127.0.0.1:{server.server_port}/index.html'
        page.goto(url)
        check('nuScenes is active and Argoverse is visibly pending',
              page.locator('#dataset-select').input_value()=='nuscenes'
              and page.locator('#dataset-select option[value="argoverse"]').evaluate('(option)=>option.disabled')
              and '未计入评估' in page.locator('#dataset-pending').inner_text())
        check('30 selectable evidence objects',page.locator('.wb-case-row').count()==30)
        check('reference comparison is initially hidden',page.locator('#wb-reference-map').is_hidden())
        stage=page.locator('#wb-map-stage').bounding_box();map_box=page.locator('#map').bounding_box()
        check('map fills its workspace',abs(map_box['height']-stage['height'])<2)
        check('actual oriented vehicle footprints are shown',page.locator('[data-kind="vehicle-shapes"] path').count()>0)
        page.screenshot(path=str(root/'workbench_preview.png'))
        page.locator('[data-family="priority"]').click()
        check('priority filter returns two records',page.locator('.wb-case-row').count()==2)
        page.locator('#wb-search').fill('LD004')
        check('search combines with filter',page.locator('.wb-case-row').count()==1)
        page.locator('.wb-case-row').click()
        page.locator('#wb-search').fill('not-an-id')
        check('empty search is explicit',page.locator('.wb-empty').is_visible())
        page.locator('#wb-search').fill('')
        page.locator('[data-family="all"]').click()
        page.locator('#wb-compare').click()
        check('reference and observations can be compared',page.locator('#wb-reference-map').is_visible()
              and page.locator('#wb-reference-map path').count()==157)
        v=page.locator('#map').get_attribute('viewBox')
        page.locator('#wb-zoom-in').click()
        check('zoom changes both synchronized views',v!=page.locator('#map').get_attribute('viewBox')
              and page.locator('#map').get_attribute('viewBox')==page.locator('#wb-reference-map').get_attribute('viewBox'))
        box=page.locator('#map').bounding_box();before=page.locator('#map').get_attribute('viewBox')
        page.mouse.move(box['x']+box['width']*.15,box['y']+box['height']*.4)
        page.mouse.down();page.mouse.move(box['x']+box['width']*.15+35,box['y']+box['height']*.4+20,steps=5);page.mouse.up()
        check('drag pans the map',before!=page.locator('#map').get_attribute('viewBox'))
        page.locator('#wb-measure').click()
        points=[{'x':box['x']+box['width']*.2,'y':box['y']+box['height']*.3},
                {'x':box['x']+box['width']*.6,'y':box['y']+box['height']*.5}]
        points=[{k:round(v) for k,v in p.items()} for p in points]
        expected=page.evaluate('points=>{const m=document.getElementById("map").getScreenCTM().inverse();const [a,b]=points.map(p=>new DOMPoint(p.x,p.y).matrixTransform(m));return Math.hypot(a.x-b.x,a.y-b.y)}',points)
        for p in points:page.mouse.click(p['x'],p['y'])
        measured=float(page.locator('#wb-measure-layer text[data-distance]').get_attribute('data-distance'))
        check('two-point measurement uses actual map coordinates',abs(measured-expected)<1e-7)
        check('measurement does not change candidate',page.locator('#candidate').input_value()=='S0980-LD-004')
        page.screenshot(path=str(root/'comparison_measurement.png'))
        page.keyboard.press('Escape')
        check('escape exits measuring',page.evaluate('WB.tool')=='browse')
        page.locator('#wb-frame-number').fill('13');page.locator('#wb-frame-number').press('Enter');page.locator('#wb-frame-number').blur()
        check('frame number jump updates both views',page.locator('#frame').input_value()=='13'
              and '不等于恢复' in page.locator('#current-observation').inner_text())
        page.locator('#wb-visible').check()
        check('current-frame filter excludes absent LD004',page.locator('.wb-case-row[data-id="S0980-LD-004"]').count()==0)
        page.locator('#wb-visible').uncheck()
        page.locator('[data-family="occupancy"]').click()
        check('occupancy filter returns 23 records',page.locator('.wb-case-row').count()==23)
        page.locator('.wb-case-row[data-id="S0980-OCC-scene-0980-O001"]').click()
        page.locator('[data-inspector="observations"]').click()
        check('per-frame evidence panel shows actual occupancy', '占用面积' in page.locator('#wb-observation-pane').inner_text())
        page.locator('[data-inspector="review"]').click()
        page.locator('#wb-review-status').select_option('needs_evidence')
        page.locator('#wb-review-note').fill('测试草稿：需要核对遮挡和后续独立访问。')
        check('review draft is saved locally',page.evaluate('WB.reviews.size')==1
              and '已自动保存' in page.locator('#wb-review-saved').inner_text())
        before_frame=page.locator('#frame').input_value();page.locator('#wb-review-note').press('ArrowRight')
        check('typing does not trigger timeline shortcuts',page.locator('#frame').input_value()==before_frame)
        page.reload();page.locator('[data-inspector="review"]').click()
        check('reviews survive reload',page.locator('#wb-review-note').input_value().startswith('测试草稿'))
        check('review drafts cannot change real accuracy',page.evaluate('R.accuracy.f1===null && R.maintenance.base_map_updates.length===0'))
        with page.expect_download() as info:page.locator('#wb-export-reviews').click()
        export=root/'test_review_export.json';info.value.save_as(str(export));payload=json.loads(export.read_text())
        check('review export is explicitly not truth',payload['role']=='personal_review_not_ground_truth' and len(payload['records'])==1)
        check('review export has a dataset namespace',payload['dataset_id']=='nuscenes'
              and payload['schema']=='aie1903-human-review-draft-1.1')
        invalid_dataset={**payload,'dataset_id':'argoverse'}
        page.locator('#wb-import-file').set_input_files({'name':'wrong-dataset.json','mimeType':'application/json','buffer':json.dumps(invalid_dataset).encode()})
        page.wait_for_function('document.getElementById("wb-toast").textContent.includes("数据集不匹配")')
        check('same-scene Argoverse review cannot contaminate nuScenes',page.evaluate('WB.reviews.size')==1)
        invalid={**payload,'scene_id':'other-scene'}
        page.locator('#wb-import-file').set_input_files({'name':'wrong-scene.json','mimeType':'application/json','buffer':json.dumps(invalid).encode()})
        page.wait_for_function('document.getElementById("wb-toast").textContent.includes("导入未执行")')
        check('wrong-scene import is rejected atomically',page.evaluate('WB.reviews.size')==1)
        valid={**payload,'records':[{**payload['records'][0],'record_id':'S0980-LD-003','note':'测试导入草稿'}]}
        page.locator('#wb-import-file').set_input_files({'name':'valid.json','mimeType':'application/json','buffer':json.dumps(valid).encode()})
        page.wait_for_function('WB.reviews.size===2')
        check('valid review import merges drafts',page.evaluate('WB.reviews.get("S0980-LD-003").note')=='测试导入草稿')
        page.screenshot(path=str(root/'review_controls.png'))
        page.goto(url+'#case=S0980-LD-003&frame=21&tab=evaluation')
        page.wait_for_function('state.tab==="evaluation" && state.frame===21')
        check('shared view restores tab and frame',page.locator('#evaluation-page').is_visible()
              and page.locator('#frame').input_value()=='21')
        page.locator('.tabs [data-tab="scene"]').click()
        check('returning from a shared evaluation view keeps a valid map',page.evaluate('state.view[2]>0 && state.view[3]>0'))
        page.locator('#wb-speed').select_option('4')
        page.locator('#play').click();page.wait_for_function('state.frame>=23');page.locator('#play').click()
        check('playback speed control advances actual frames',int(page.locator('#frame').input_value())>=23)
        with page.expect_download() as info:page.locator('#export-current').click()
        selected_export=root/'test_selected_evidence.json';info.value.save_as(str(selected_export))
        check('current evidence export identifies nuScenes',json.loads(selected_export.read_text())['dataset_id']=='nuscenes')
        legacy_context=browser.new_context()
        legacy_page=legacy_context.new_page();legacy_page.goto(url)
        legacy={**payload,'schema':'aie1903-human-review-draft-1.0'};legacy.pop('dataset_id')
        legacy_page.evaluate('(p)=>localStorage.setItem("aie1903-review-v1:"+R.scene_id+":"+R.map.source_sha256,JSON.stringify(p))',legacy)
        legacy_page.reload()
        check('existing nuScenes review drafts migrate without deleting old storage',legacy_page.evaluate('''()=>{
            const oldKey='aie1903-review-v1:'+R.scene_id+':'+R.map.source_sha256;
            const newKey='aie1903-review-v2:nuscenes:'+R.scene_id+':'+R.map.source_sha256;
            return WB.reviews.size===1 && !!localStorage.getItem(oldKey) && JSON.parse(localStorage.getItem(newKey)).dataset_id==='nuscenes';
        }'''))
        legacy_context.close()
        for tab in ['scene','evaluation','evidence']:
            page.set_viewport_size({'width':390,'height':844});page.locator(f'.tabs [data-tab="{tab}"]').click()
            check('mobile layout '+tab,page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        check('no JavaScript errors',not errors)
        (root/'workbench_checks.json').write_text(json.dumps({'status':'PASS','checks':checks,'javascript_errors':errors},ensure_ascii=False,indent=2)+'\n')
        print(f'PASS: {len(checks)} user-operation checks',flush=True)
        browser.close()
finally:server.shutdown();server.server_close();thread.join()
