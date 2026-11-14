"""Browser acceptance for scale presets, native populations and private activity."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:8100');p.add_argument('--out',type=Path,required=True);p.add_argument('--quick',action='store_true')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False);checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1600,'height':1050},accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def check(name,ok):
            assert ok,name
            checks.append(name);print('PASS '+name,flush=True)
        page.goto(args.url);page.wait_for_function('window.MAP_PLATFORM?.state.snapshot',timeout=60000)
        check('source walkway overlay is available',page.evaluate('MAP_PLATFORM.state.scene.display_walkways.length>50'))
        page.locator('#driving-tab').click()
        check('expanded district is default',page.locator('#traffic-profile').input_value()=='district')
        check('rich preset is 24 vehicles per lane-km and 16 walkers',page.locator('#traffic-count').input_value()=='48' and page.locator('#pedestrian-count').input_value()=='16')
        page.locator('#traffic-density').select_option('stress')
        check('stress preset exposes 64 cars 32 walkers and 12-16 obstacles',page.locator('#traffic-count').input_value()=='64' and page.locator('#pedestrian-count').input_value()=='32' and page.locator('#construction-count-min').input_value()=='12' and page.locator('#construction-count-max').input_value()=='16')
        page.locator('#traffic-event').select_option('pedestrians');page.locator('#traffic-density').select_option('light')
        check('pedestrian-only remains zero cars when changing density',page.locator('#traffic-count').input_value()=='0' and page.locator('#traffic-count').is_disabled())
        page.locator('#traffic-event').select_option('mixed')
        check('switching back restores light vehicle count',page.locator('#traffic-count').input_value()=='16')
        page.locator('#traffic-density').select_option('rich');page.locator('#perception-profile').select_option('occlusion_noise_v1');page.locator('#start-interactive').click()
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.pedestrian_spawned===16',timeout=150000)
        check('all 48 cars have native states',page.evaluate('Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles).length===49'))
        check('density and lane changes are visible', '24.2' in page.locator('#traffic-area-status').inner_text() and '虚线变道' in page.locator('#lane-change-status').inner_text())
        check('eight routes and deduplicated lane coverage visible',page.evaluate('MAP_PLATFORM.interactive.frame.interactive.traffic_area.route_count===8') and '去重车道长度' in page.locator('#traffic-area-status').inner_text())
        check('multiple obstacles and dispersed walkers',page.evaluate('MAP_PLATFORM.interactive.frame.interactive.construction_generation.placed_count>=1 && MAP_PLATFORM.interactive.frame.interactive.population.pedestrian_occupied_cells>=10'))
        page.locator('[data-camera=overview]').click()
        page.screenshot(path=str(args.out/'district-overview.png'),full_page=True)
        page.evaluate('MAP_PLATFORM.interactiveControl("play")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.pedestrian_travelled_m>20' if args.quick else 'MAP_PLATFORM.interactive.frame?.interactive?.population?.brief_stops_completed>0',timeout=180000)
        check('native population continues moving' if args.quick else 'random brief stops recover during live play',True)
        with page.expect_download() as info:page.locator('#export-planning').click()
        data=json.loads(Path(info.value.path()).read_text())
        check('stage-three observation model is explicit',data.get('noise_modelled') is True and data.get('sensor_model',{}).get('calibrated') is False)
        check('Planning cannot read activity schedule or pedestrian style',all(k not in json.dumps(data) for k in ('stop_at','speed_at','wait_until','walk_style','background_activity','brief_stops','display_walkways')))
        page.locator('#focus-pedestrian').click();page.screenshot(path=str(args.out/'district-walker.png'),full_page=True)
        check('no browser errors',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("close")');page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000)
        browser.close()
    (args.out/'checks.json').write_text(json.dumps(dict(passed=checks,browser_errors=errors),indent=2)+'\n')
if __name__=='__main__':main()
