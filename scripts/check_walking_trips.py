"""Browser regression for pedestrian-only configuration and native actor population."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:8099');p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False);checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1600,'height':1050},accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def check(name,value):
            assert value,name
            checks.append(name);print('PASS '+name,flush=True)
        page.goto(args.url);page.wait_for_function('window.MAP_PLATFORM?.state.snapshot',timeout=60000)
        page.locator('#driving-tab').click();page.locator('#traffic-profile').select_option('network');page.locator('#traffic-count').fill('7')
        page.locator('#traffic-event').select_option('pedestrians')
        check('pedestrian-only disables background count at zero',page.locator('#traffic-count').is_disabled() and page.locator('#traffic-count').input_value()=='0')
        page.locator('#traffic-event').select_option('mixed')
        check('mixed mode restores chosen count',not page.locator('#traffic-count').is_disabled() and page.locator('#traffic-count').input_value()=='7')
        page.locator('#traffic-event').select_option('pedestrians')
        page.locator('#start-interactive').click()
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.pedestrian_spawned===3',timeout=120000)
        check('native room contains ego and zero background cars',page.evaluate('JSON.stringify(Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles))===JSON.stringify(["ego"])'))
        initial=page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.filter(a=>a.source_scope==="synthetic_pedestrian").map(a=>({id:a.id,position:a.position}))')
        check('three synthetic pedestrian bodies are rendered',len(initial)==3)
        check('only ego observation card exists',page.locator('.interactive-vehicle').count()==1)
        page.locator('#focus-pedestrian').click()
        check('pedestrian can be located','仿真行人' in page.locator('#object-labels').inner_text())
        page.evaluate('MAP_PLATFORM.interactiveControl("play")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.pedestrian_travelled_m>20',timeout=120000)
        check('walking distances advance and appear in UI','步行' in page.locator('#population-status').inner_text())
        page.screenshot(path=str(args.out/'walking-only.png'),full_page=True)
        with page.expect_download() as info:page.locator('#export-planning').click()
        data=json.loads(Path(info.value.path()).read_text())
        check('Planning export excludes private walking goals and schedules',all(k not in json.dumps(data) for k in ('completed_trips','wait_until','walking_network','visited_crosswalks','detour')))
        page.evaluate('MAP_PLATFORM.interactiveControl("pause")');page.wait_for_timeout(500)
        page.evaluate('MAP_PLATFORM.interactiveControl("reset")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step===0 && MAP_PLATFORM.interactive.frame?.interactive?.population?.pedestrian_travelled_m===0',timeout=120000)
        after=page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.filter(a=>a.source_scope==="synthetic_pedestrian").map(a=>({id:a.id,position:a.position}))')
        check('reset reproduces pedestrian spawn positions',after==initial)
        check('no browser exceptions',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("close")');page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000)
        browser.close()
    (args.out/'checks.json').write_text(json.dumps(dict(passed=checks,browser_errors=errors),indent=2)+'\n')
if __name__=='__main__':main()
