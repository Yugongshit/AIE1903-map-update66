"""Browser acceptance for continuous stage-two mixed traffic."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:8098');p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False);checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1050},accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def check(name,value):
            assert value,name
            checks.append(name);print('PASS '+name,flush=True)
        page.goto(args.url);page.wait_for_function('window.MAP_PLATFORM?.state.snapshot',timeout=60000)
        page.locator('#driving-tab').click()
        check('new mode defaults to network and mixed events',page.locator('#traffic-profile').input_value()=='network' and page.locator('#traffic-event').input_value()=='mixed')
        check('network area selection is explicit',page.locator('#traffic-area').is_disabled())
        page.locator('#traffic-count').fill('8');page.locator('#traffic-speed-spread').select_option('0.3')
        page.locator('#construction-count-max').fill('3');page.locator('#clearance-max').fill('60')
        page.locator('#start-interactive').click()
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.traffic_profile==="synthetic_network_v1"',timeout=120000)
        initial=page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.filter(a=>a.source_scope?.startsWith("synthetic_")).map(a=>({id:a.id,position:a.position}))')
        check('three source routes and four conflict zones are live',page.evaluate('MAP_PLATFORM.interactive.frame.interactive.traffic_area.route_count===3 && MAP_PLATFORM.interactive.frame.interactive.junction_control.zone_count===4'))
        check('pedestrians have native bodies and synthetic provenance',page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.filter(a=>a.source_scope==="synthetic_pedestrian").length===3'))
        page.locator('#focus-pedestrian').click()
        check('pedestrian label is visible','仿真行人' in page.locator('#object-labels').inner_text())
        check('no manual seats returned',page.locator('[data-action=claim]:visible').count()==0)
        page.screenshot(path=str(args.out/'pedestrian-active.png'),full_page=True)
        page.evaluate('MAP_PLATFORM.interactiveControl("play")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.completed_crossings>0',timeout=180000)
        check('pedestrians complete physical crossings',True)
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.synthetic_events.some(e=>e.kind==="synthetic_vehicle_incident")',timeout=180000)
        page.locator('#focus-incident').click()
        page.wait_for_function('document.getElementById("live-map-summary").textContent.includes("观察车 P")')
        check('incident vehicle can be inspected',not page.locator('#focus-incident').is_hidden())
        page.screenshot(path=str(args.out/'incident-active.png'),full_page=True)
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.population?.recurring_spawned>0 && MAP_PLATFORM.interactive.frame?.interactive?.population?.incident_completed>0',timeout=240000)
        check('new construction and repair continue after startup',True)
        check('traffic metrics are visible','后续施工' in page.locator('#population-status').inner_text() and '源路网 3 条路线' in page.locator('#traffic-area-status').inner_text())
        with page.expect_download() as info:page.locator('#export-planning').click()
        download=info.value;data=json.loads(Path(download.path()).read_text())
        forbidden=['next_spawn','next_at','until','wait_until','source_crosswalk_id','junction_control','population','driver_parameters','construction_count_max']
        check('Planning export excludes private schedules and generator counters',all(k not in json.dumps(data) for k in forbidden))
        check('Planning state is from the same observation step',data['state_layer']['observed_through_step']==data['step'])
        check('no structural map edits are claimed',data['state_layer']['structural_writes']==0)
        download.save_as(str(args.out/'planning-input.json'))
        page.locator('[data-camera=overview]').click();page.locator('#population-status').scroll_into_view_if_needed()
        page.screenshot(path=str(args.out/'mixed-overview.png'),full_page=True)
        page.evaluate('MAP_PLATFORM.interactiveControl("pause")');page.wait_for_timeout(500)
        page.evaluate('MAP_PLATFORM.interactiveControl("reset")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step===0 && MAP_PLATFORM.interactive.frame?.interactive?.population?.incident_started===0',timeout=120000)
        after=page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.filter(a=>a.source_scope?.startsWith("synthetic_")).map(a=>({id:a.id,position:a.position}))')
        check('reset reproduces all generated initial actors',after==initial)
        check('no browser exceptions',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("close")');page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000)
        browser.close()
    (args.out/'checks.json').write_text(json.dumps(dict(passed=checks,browser_errors=errors),indent=2)+'\n')
if __name__=='__main__':main()
