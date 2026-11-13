"""Exercise real fleet physics, shared-map delivery and browser-bound exports.

Start run_simulation_platform.py in the simulation environment first. This
checker uses Playwright/Chromium and never substitutes rendered engine frames.
"""
import argparse
import json
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'console_errors': []}

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = browser.new_page(base_url=args.url, viewport={'width': 1600, 'height': 1100})
        page.set_default_timeout(180000)
        page.on('pageerror', lambda error: report['console_errors'].append(str(error)))
        try:
            page.goto(args.url, wait_until='networkidle')
            page.wait_for_function('window.MAP_PLATFORM?.state.scene && MAP_PLATFORM.state.snapshot')
            page.evaluate("MAP_PLATFORM.selectScene('nuscenes-scene0980')")
            page.locator('#fleet-panel').evaluate('(el)=>el.open=true')
            page.select_option('#fleet-mode', 'offline')
            page.select_option('#fleet-variant', 'full')
            page.evaluate('MAP_PLATFORM.launchFleet()')
            check('explicit fleet launch succeeds', page.evaluate('!!MAP_PLATFORM.state.fleet'))
            page.evaluate('MAP_PLATFORM.seek(20)')
            state = page.evaluate('({step:MAP_PLATFORM.state.step,fleet:MAP_PLATFORM.state.fleet,frame:MAP_PLATFORM.state.engineDisplayedFrame?.frame_id})')
            check('real engine and display agree at frame 20', state['step'] == 20)
            check('C moves using physics', state['fleet']['vehicles']['C']['actual_trace'][0]['position_city_m'] !=
                  state['fleet']['vehicles']['C']['position_city_m'])
            response = page.request.get('/api/engine/fleet/planning?vehicle_id=C&frame_id=' + state['frame'])
            check('per-car planning export succeeds', response.status == 200)
            planning = response.json()
            check('export uses displayed C pose, step and received knowledge',
                  planning['vehicle_state']['step'] == 20 and planning['vehicle_id'] == 'C' and
                  planning['received_version'] == state['fleet']['vehicles']['C']['received_version'] and
                  planning['vehicle_state']['position'] == state['fleet']['vehicles']['C']['position_city_m'])
            page.screenshot(path=str(args.out/'native-nuscenes.png'), full_page=True)
            page.evaluate("MAP_PLATFORM.setMode('review')")
            page.evaluate('MAP_PLATFORM.seek(30)')
            check('review seek advances actual physics and trace',
                  page.evaluate('MAP_PLATFORM.state.step===30 && MAP_PLATFORM.state.fleet.vehicles.C.actual_trace.length===31'))
            page.screenshot(path=str(args.out/'review-nuscenes.png'), full_page=True)
            page.evaluate("MAP_PLATFORM.followFleetVehicle('C')")
            check('follow C switches to its native camera', page.evaluate(
                "MAP_PLATFORM.state.mode==='engine' && MAP_PLATFORM.state.engineDisplayedFrame.result.camera.target==='recipient-C'"))
            page.evaluate("MAP_PLATFORM.selectScene('argoverse-winter2021')")
            page.select_option('#fleet-mode', 'online')
            page.select_option('#fleet-variant', 'full')
            page.evaluate('MAP_PLATFORM.launchFleet()')
            check('online fleet launch succeeds', page.evaluate("MAP_PLATFORM.state.fleet?.mode==='online'"))
            before = page.evaluate('MAP_PLATFORM.state.fleet.vehicles.B.current_route.lane_ids')
            page.evaluate("MAP_PLATFORM.fleetCommand({action:'subscription',vehicle_id:'C',enabled:false})")
            c_version = page.evaluate('MAP_PLATFORM.state.fleet.vehicles.C.received_version')
            page.evaluate("MAP_PLATFORM.fleetCommand({action:'publish'})")
            after = page.evaluate('MAP_PLATFORM.state.fleet.vehicles.B.current_route.lane_ids')
            check('B replans through genuine updated connectors', before != after and 'ls-168084910' in after)
            check('unsubscribed C retains previous knowledge',
                  page.evaluate('MAP_PLATFORM.state.fleet.vehicles.C.received_version') == c_version)
            page.evaluate("MAP_PLATFORM.fleetCommand({action:'subscription',vehicle_id:'C',enabled:true})")
            check('resubscribed C receives the published map',
                  page.evaluate('MAP_PLATFORM.state.fleet.vehicles.C.received_version') != c_version)
            page.evaluate('MAP_PLATFORM.seek(25)')
            positions = page.evaluate('Object.fromEntries(Object.entries(MAP_PLATFORM.state.fleet.vehicles).map(([k,v])=>[k,v.position_city_m]))')
            page.evaluate('MAP_PLATFORM.seek(10)')
            page.evaluate('MAP_PLATFORM.seek(25)')
            repeated = page.evaluate('Object.fromEntries(Object.entries(MAP_PLATFORM.state.fleet.vehicles).map(([k,v])=>[k,v.position_city_m]))')
            check('rewind reproduces delivery commands and physical positions',
                  all(abs(positions[key][axis] - repeated[key][axis]) < .01 for key in positions for axis in (0, 1)))
            page.screenshot(path=str(args.out/'native-argoverse.png'), full_page=True)
            report.update(argo_original_route=before, argo_received_route=after,
                          native_positions_at25=positions,
                          exported_C_pose_at20=planning['vehicle_state']['position'])
            check('no browser exceptions', not report['console_errors'])
        finally:
            (args.out/'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()


if __name__ == '__main__':
    main()
