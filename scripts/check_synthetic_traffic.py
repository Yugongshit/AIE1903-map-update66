"""Browser exercise of the automatic traffic, causal map input and scoped export."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8092')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--construction', action='store_true')
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
        if args.construction:
            page.locator('#traffic-stop-duration').fill('1')
            page.locator('#traffic-event').select_option('construction')
            check('construction duration minimum is reflected in the input',page.locator('#traffic-stop-duration').input_value()=='5')
            page.locator('#traffic-stop-duration').fill('12')
        page.locator('#start-interactive').click()
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.synthetic_traffic',timeout=120000)
        check('synthetic provenance is visible','合成循环交通' in page.locator('#interactive-summary').inner_text())
        check('generated cars are labelled synthetic', '合成车辆' in page.locator('[data-vehicle="P1"] [data-field="owner"]').inner_text())
        check('eight native vehicles plus ego',page.evaluate('Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles).length')==9)
        check('nearby source route is selected',page.evaluate('MAP_PLATFORM.interactive.frame.interactive.traffic_area.route_distance_to_recorded_ego_m')<15)
        check('route distance is visible','回放附近' in page.locator('#traffic-area-status').inner_text())
        check('ego can observe nearby generated traffic',page.evaluate('''async()=>{const s=MAP_PLATFORM.interactive.room.session_id;const d=await (await fetch('/api/interactive/perception?session_id='+s+'&vehicle_id=ego')).json();return d.current_objects.some(x=>x.vehicle_id==='P1')}'''))
        check('manual driver seats are hidden',page.locator('[data-action=claim]:visible').count()==0)
        if args.construction:
            check('construction physical actor is visible and labelled synthetic',page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.some(a=>a.id==="construction-C1" && a.source_scope==="synthetic_construction_event")'))
            page.wait_for_function('document.getElementById("object-labels").textContent.includes("仿真施工路障")')
            page.locator('#focus-construction').click()
            check('construction can be focused without changing the observation vehicle',page.evaluate('MAP_PLATFORM.camera.target==="map" && MAP_PLATFORM.interactive.frame.step===0'))
            page.screenshot(path=str(args.out/'construction-active.png'),full_page=True)
        page.evaluate('MAP_PLATFORM.interactiveControl("play")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step>10',timeout=60000)
        check('IDM driver feedback is shown','IDM' in page.locator('[data-vehicle="P8"] [data-field="following"]').inner_text())
        if args.construction:
            page.wait_for_function('''async()=>{const s=MAP_PLATFORM.interactive.room.session_id;const d=await(await fetch('/api/interactive/planning-input?session_id='+s+'&vehicle_id=ego')).json();return d.state_layer.events.some(e=>e.actor_id==='construction-C1'&&e.status==='active')}''',timeout=90000)
            check('HTTP planning input receives observed construction occupancy',True)
            page.wait_for_function('MAP_PLATFORM.interactive.frame.interactive.synthetic_events[0].status==="removed"',timeout=90000)
            page.wait_for_function('''async()=>{const s=MAP_PLATFORM.interactive.room.session_id;const d=await(await fetch('/api/interactive/planning-input?session_id='+s+'&vehicle_id=ego')).json();return d.state_layer.events.some(e=>e.actor_id==='construction-C1'&&e.status==='cleared'&&e.clear_reason==='repeated_observed_free_previous_footprint')}''',timeout=90000)
            check('HTTP map clearance follows observed empty footprint',True)
            check('cleared obstacle is removed from physical snapshot',page.evaluate('!MAP_PLATFORM.interactive.frame.snapshot.actors.some(a=>a.id==="construction-C1")'))
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
        check('private intentions are excluded',all(not(set(a)&{'route','goal_scenario_m','action','requested_action','following','driver_parameters'})
              for a in [data['own_vehicle'],*data['current_objects']]))
        check('map layer and observation are atomic',data['state_layer']['observed_through_step']==data['step'])
        check('no structural edits are claimed',data['state_layer']['structural_writes']==0)
        downloaded.save_as(str(args.out/'perception.json'))
        page.locator('#traffic-area-status').scroll_into_view_if_needed()
        page.screenshot(path=str(args.out/'synthetic-cycle.png'),full_page=True)
        check('no uncaught JavaScript errors',not errors)
        page.evaluate('MAP_PLATFORM.interactiveControl("pause")')
        page.wait_for_timeout(500)
        page.evaluate('MAP_PLATFORM.interactiveControl("reset")')
        page.wait_for_function('MAP_PLATFORM.interactive.frame?.step===0 && MAP_PLATFORM.interactive.frame?.interactive?.synthetic_traffic',timeout=120000)
        check('reset preserves synthetic population',page.evaluate('Object.keys(MAP_PLATFORM.interactive.frame.interactive.vehicles).length')==9)
        check('reset preserves area and IDM model',page.evaluate('MAP_PLATFORM.interactive.frame.interactive.traffic_area.selection==="near_recording" && MAP_PLATFORM.interactive.frame.interactive.following_model==="idm"'))
        if args.construction:
            check('reset reconstructs initial construction actor',page.evaluate('MAP_PLATFORM.interactive.frame.snapshot.actors.some(a=>a.id==="construction-C1") && MAP_PLATFORM.interactive.frame.interactive.synthetic_events[0].status==="active"'))
        page.evaluate('MAP_PLATFORM.interactiveControl("close")')
        page.wait_for_function('!MAP_PLATFORM.interactive.room',timeout=30000)
        browser.close()
    (args.out/'checks.json').write_text(json.dumps({'passed':checks,'browser_errors':errors},indent=2)+'\n')

if __name__=='__main__':
    main()
