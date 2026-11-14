"""Browser exercise of the automatic traffic, causal map input and scoped export."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8092')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def check(name, value):
            assert value,name
            checks.append(name);print('PASS '+name,flush=True)
        page.goto(args.url)
        page.wait_for_function('window.MAP_PLATFORM?.state.snapshot',timeout=60000)
        page.locator('#driving-tab').click()
        page.locator('#traffic-profile').select_option('cycle')
        page.locator('#traffic-seed').fill('0')
        page.locator('#traffic-count').fill('8')
        page.locator('#traffic-speed-spread').select_option('0.3')
        page.locator('#start-interactive').click()
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.synthetic_traffic',timeout=120000)
        check('synthetic provenance is visible','合成循环交通' in page.locator('#interactive-summary').inner_text())
        check('generated cars are labelled synthetic', '合成车辆' in page.locator('[data-vehicle="P1"] [data-field="owner"]').inner_text())
        check('eight native vehicles plus ego',page.evaluate('Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles).length')==9)
        check('manual driver seats are hidden',page.locator('[data-action=claim]:visible').count()==0)
        page.evaluate('MAP_PLATFORM.interactiveControl("play")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step>10',timeout=60000)
        page.locator('[data-vehicle="P8"] [data-action="follow"]').click()
        page.wait_for_function('document.getElementById("live-map-summary").textContent.includes("观察车 P8")',timeout=30000)
        check('eighth vehicle can observe its own online map',True)
        page.locator('#live-map-policy').select_option('baseline')
        page.wait_for_function('document.getElementById("live-map-events").textContent.includes("当前策略不提供新增状态")',timeout=30000)
        check('baseline input can be selected without resetting the world',page.evaluate('MAP_PLATFORM.interactive.frame.step')>10)
        page.locator('#live-map-policy').select_option('observed')
        with page.expect_download() as download_info:
            page.locator('#export-planning').click()
        downloaded=download_info.value
        data=json.loads(Path(downloaded.path()).read_text())
        check('export contains bounded synthetic perception',data['schema']=='interactive-planning-input-1.0' and data['range_m']==50)
        check('private intentions are excluded',all(not(set(a)&{'route','goal_scenario_m','action','requested_action'})
              for a in [data['own_vehicle'],*data['current_objects']]))
        check('map layer and observation are atomic',data['state_layer']['observed_through_step']==data['step'])
        check('no structural edits are claimed',data['state_layer']['structural_writes']==0)
        downloaded.save_as(str(args.out/'perception.json'))
        page.screenshot(path=str(args.out/'synthetic-cycle.png'),full_page=True)
        check('no uncaught JavaScript errors',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("pause")')
        page.wait_for_timeout(500)
        page.evaluate('MAP_PLATFORM.interactiveControl("reset")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step===0 && MAP_PLATFORM.interactive.frame?.interactive?.synthetic_traffic',timeout=120000)
        check('reset preserves synthetic population',page.evaluate('Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles).length')==9)
        page.evaluate('MAP_PLATFORM.interactiveControl("close")')
        page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000)
        browser.close()
    (args.out/'checks.json').write_text(json.dumps({'passed':checks,'browser_errors':errors},indent=2)+'\n')

if __name__=='__main__':
    main()
