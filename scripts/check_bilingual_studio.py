"""Verify language isolation, source identity, navigation, exports and interaction."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
from threading import Thread
from zipfile import ZipFile

from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
root=parser.parse_args().directory.resolve();checks=[]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root)))
Thread(target=server.serve_forever,daemon=True).start()
def check(name,result):
    assert result,name
    checks.append(name)
def no_han(text):return not re.search('[\u3400-\u9fff]',text)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1600,'height':1150});page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        base=f'http://127.0.0.1:{server.server_port}/'
        page.goto(base);page.wait_for_url('**/en/index.html*')
        check('default entry opens the English website',page.locator('html').get_attribute('lang')=='en')
        # The decision studio is now the entry page; retain the legacy scene regression scope.
        page.locator('[data-page="drive"]').click()
        english=page.evaluate('({real:R,camera:CAM,experiment:LAB})')
        check('English scene view has no untranslated Chinese UI',no_han(page.locator('body').inner_text()))
        check('English object labels use exact original categories',page.evaluate('''()=>[...document.querySelectorAll('#front-object-labels button')].every(b=>{const a=CAM.views[S.frame].annotations.find(a=>a.instance_token===b.dataset.objectToken);return b.innerText===a.category+' #'+a.display_id;})'''))
        check('English labels display original categories without truncation',page.evaluate('''()=>[...document.querySelectorAll('#front-object-labels button span:last-child')].every(e=>e.scrollWidth<=e.clientWidth+1)'''))
        check('original object categories also appear in the object list',page.evaluate('''()=>[...document.querySelectorAll('#object-list button')].every(b=>b.innerText.startsWith(CAM.views[S.frame].annotations.find(a=>a.instance_token===b.dataset.objectToken).category))'''))
        page.screenshot(path=str(root/'english_scene.png'),full_page=True)
        (root/'english_scene_text.txt').write_text(page.locator('body').inner_text())
        page.locator('#scene-frame').fill('30');page.locator('#scene-frame').dispatch_event('input')
        check('English frame controls advance the source frame',page.evaluate('S.frame')==30)
        page.locator('#object-family').select_option('person')
        worker=page.locator('#object-list button').filter(has_text='human.pedestrian.construction_worker').first
        worker.click();token=page.evaluate('S.objectToken')
        check('original construction-worker class is retained','human.pedestrian.construction_worker' in page.locator('#object-title').inner_text())
        page.locator('#object-provenance').click()
        check('English source dialog retains category and original instance token',no_han(page.locator('#dialog-body').inner_text()) and token in page.locator('#dialog-body').inner_text())
        page.locator('#close-dialog').click();page.locator('#language-switch').click();page.wait_for_url('**/zh/index.html*')
        check('language switch preserves frame and object instance',page.evaluate('S.frame')==30 and page.evaluate('S.objectToken')==token)
        check('Chinese display translates the same original worker','施工人员' in page.locator('#object-title').inner_text())
        chinese=page.evaluate('({real:R,camera:CAM,experiment:LAB})')
        check('both websites retain identical source data geometry and evaluation',english==chinese)
        page.screenshot(path=str(root/'chinese_scene.png'),full_page=True)
        page.locator('#language-switch').click();page.wait_for_url('**/en/index.html*')
        check('return to English retains frame and identity',page.evaluate('S.frame')==30 and page.evaluate('S.objectToken')==token)
        page.locator('[data-camera="video"]').click();page.wait_for_function('document.getElementById("perception-video").readyState>=1')
        check('shared original video resolves from the English route',page.evaluate('document.getElementById("perception-video").duration>32'))
        page.locator('[data-camera="photo"]').click();check('English missing-photo state is explicit',page.locator('#photo-empty').is_visible() and no_han(page.locator('#photo-empty').inner_text()))
        page.locator('[data-camera="reconstruction"]').click();page.locator('#go-changes').click()
        check('English real map result still reports zero base-map writes',page.evaluate('R.maintenance.base_map_updates.length')==0 and no_han(page.locator('#changes-page').inner_text()))
        page.locator('#start-experiment').click();page.locator('[data-stage="3"]').click()
        check('controlled write works with English explanations',page.evaluate('JSON.stringify(currentStage().map.elements[0].geometry_city)!==JSON.stringify(LAB.baseline.elements[0].geometry_city)') and no_han(page.locator('#changes-page').inner_text()))
        with page.expect_download() as event:page.locator('#experiment-export').click()
        patch=json.loads(Path(event.value.path()).read_text())
        check('English trial export remains explicitly controlled',patch['real_map_modified'] is False)
        check('English trial export translates explanatory assumptions',all(no_han(x) for x in patch['assumptions']))
        page.locator('[data-stage="4"]').click()
        check('English rollback restores original geometry',page.evaluate('JSON.stringify(currentStage().map.elements[0].geometry_city)===JSON.stringify(LAB.baseline.elements[0].geometry_city)'))
        page.locator('[data-page="issues"]').click()
        check('all eleven English issue entries are translated',page.locator('.issue-card').count()==11 and no_han(page.locator('#issues-page').inner_text()))
        page.locator('.issue-card summary').first.click()
        check('expanded English issue has translated evidence and acceptance',no_han(page.locator('#issues-page').inner_text()) and 'Acceptance criteria' in page.locator('#issues-page').inner_text())
        with page.expect_download() as event:page.locator('.download').click()
        with ZipFile(event.value.path()) as archive:xml=archive.read('word/document.xml').decode()
        check('English issue download is an English Word document','Runtime Issues and Next Steps' in xml and no_han(xml))
        page.screenshot(path=str(root/'english_issues.png'),full_page=True)
        page.goto(base+'en/review/index.html')
        check('linked English evidence workspace is translated',no_han(page.locator('body').inner_text()) and 'Evidence Review Workspace' in page.title())
        page.locator('[data-inspector="review"]').click();page.locator('#wb-review-note').fill('Bilingual persistence check');page.locator('#wb-review-status').select_option('needs_evidence')
        selected=page.evaluate('state.selected');page.locator('#language-switch').click();page.wait_for_url('**/zh/review/index.html*')
        page.locator('[data-inspector="review"]').click()
        check('existing review drafts are shared across languages',page.locator('#wb-review-note').input_value()=='Bilingual persistence check' and page.evaluate('state.selected')==selected)
        page.goto(base+'en/review/index.html');page.locator('[data-inspector="decision"]').click()
        page.locator('#open-source').click()
        check('English source drawer translates M3 explanations without changing source data',no_han(page.locator('#source-content').inner_text()))
        page.locator('#close-source').click()
        # Review explanations and evaluation values are the same source outputs.
        for tab in ['evaluation','evidence']:
            page.evaluate('(tab)=>showTab(tab)',tab)
            check('English review tab is translated: '+tab,no_han(page.locator('body').inner_text()))
        page.screenshot(path=str(root/'english_evidence.png'),full_page=True)
        page.goto(base+'review/index.html#case='+selected+'&frame=16&tab=scene');page.wait_for_url('**/en/review/index.html*')
        check('older shared review links preserve object and frame',page.evaluate('state.selected')==selected and page.evaluate('state.frame')==16)
        page.locator('#wb-case-list [data-id="S0980-LD-004"]').click();page.locator('#wb-frame-number').fill('22');page.locator('#wb-frame-number').dispatch_event('change');page.locator('nav.tabs [data-tab="evaluation"]').click()
        page.locator('#language-switch').click();page.wait_for_url('**/zh/review/index.html*')
        check('review language switching preserves current object frame and tab',page.evaluate('state.selected')=='S0980-LD-004' and page.evaluate('state.frame')==22 and page.evaluate('state.tab')=='evaluation')
        for language in ['en','zh']:
            page.goto(base+language+'/index.html');page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
            for tab in ['drive','changes','issues']:
                if tab=='changes':
                    page.locator('[data-page="drive"]').click();page.locator('#go-changes').click()
                else:page.locator(f'[data-page="{tab}"]').click()
                check(language+' mobile layout fits: '+tab,page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                if language=='en':check('English mobile UI translated: '+tab,no_han(page.locator('body').inner_text()))
            page.locator('[data-page="drive"]').click();page.screenshot(path=str(root/(language+'_mobile.png')),full_page=True)
            if language=='en':check('English mobile labels stay inside the image',page.evaluate('''()=>{const c=document.getElementById('front-canvas').getBoundingClientRect();return [...document.querySelectorAll('#front-object-labels button')].every(e=>{const b=e.getBoundingClientRect();return b.left>=c.left&&b.right<=c.right+1;});}'''))
        check('no browser script errors',not errors)
        (root/'bilingual_checks.json').write_text(json.dumps({'status':'PASS','checks':checks,'javascript_errors':errors},ensure_ascii=False,indent=2)+'\n')
        print('PASS:',len(checks),'bilingual interaction checks')
        browser.close()
finally:server.shutdown()
