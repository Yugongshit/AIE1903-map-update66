"""Browser acceptance for independent trips and broad current traffic distribution."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:8103');p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False);checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1600,'height':1050},accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def check(name,ok):
            assert ok,name
            checks.append(name);print('PASS '+name,flush=True)
        page.goto(args.url);page.wait_for_function('window.MAP_PLATFORM?.state.snapshot',timeout=60000)
        page.locator('#driving-tab').click()
        check('open-road independent journeys are the default',page.locator('#traffic-profile').input_value()=='journeys')
        check('rich preset uses 96 vehicles and 16 walkers',page.locator('#traffic-count').input_value()=='96' and page.locator('#pedestrian-count').input_value()=='16')
        page.locator('#traffic-density').select_option('stress');check('pressure preset allows 128 cars',page.locator('#traffic-count').input_value()=='128')
        page.locator('#traffic-event').select_option('pedestrians');page.locator('#traffic-density').select_option('light')
        check('pedestrian-only still has no background vehicles',page.locator('#traffic-count').input_value()=='0' and page.locator('#traffic-count').is_disabled())
        page.locator('#traffic-event').select_option('mixed');page.locator('#traffic-density').select_option('rich')
        page.locator('#start-interactive').click();page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.journeys',timeout=150000)
        state=page.evaluate('MAP_PLATFORM.interactive.frame.interactive')
        check('96 physical background actors have unique instance IDs',len(state['vehicles'])==97 and len({v['actor_instance_id'] for k,v in state['vehicles'].items() if k!='ego'})==96)
        check('open source graph exceeds seven kilometres',state['traffic_area']['unique_lane_length_m']>7000)
        outside=sum(not(395.85<=v['position_city_m'][0]<=611.91 and 1073.32<=v['position_city_m'][1]<=1463.36) for k,v in state['vehicles'].items() if k!='ego')
        check('more than half of vehicles start outside the old ring',outside>48)
        check('independent trip and boundary counts are visible','自主行程' in page.locator('#journey-status').inner_text() and '开放路网' in page.locator('#traffic-area-status').inner_text())
        page.locator('[data-camera=overview]').click();page.screenshot(path=str(args.out/'open-road-overview.png'),full_page=True)
        check('overview labels are decluttered without hiding physical actors',page.locator('#object-labels .world-label:visible').count()<60 and page.evaluate('Object.keys(MAP_PLATFORM.visual.poses).length>=96'))
        page.evaluate('MAP_PLATFORM.interactiveControl("play")');page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.sim_time_s>=2',timeout=180000)
        with page.expect_download() as info:page.locator('#export-planning').click()
        data=json.loads(Path(info.value.path()).read_text());encoded=json.dumps(data)
        check('Planning export excludes trip goals and future boundary assignments',all(k not in encoded for k in ('journey_lane','exit_lane','entry_lane','lifecycle','patrol_goals','recent')))
        check('no browser errors',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("close")');page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000);browser.close()
    (args.out/'checks.json').write_text(json.dumps(dict(passed=checks,browser_errors=errors),indent=2)+'\n')
if __name__=='__main__':main()
