"""Browser checks of unified scene review and optional real MetaDrive control.

Start run_simulation_platform.py in the simulation environment first. This
checker needs Playwright/Chromium only in the developer's test environment.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--engine', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, errors = [], []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle', timeout=60000)
        page.wait_for_function('window.MAP_PLATFORM && MAP_PLATFORM.state.snapshot', timeout=60000)
        check('scene is explicitly nuScenes and both layer toggles exist',
              page.locator('#dataset-badge').inner_text().lower() == 'nuscenes'
              and page.locator('#show-states').is_checked() and page.locator('#show-structure').is_checked())
        check('source geometry canvas renders without JavaScript errors', page.locator('#review-view canvas').count() == 1 and not errors)
        baseline = page.evaluate('MAP_PLATFORM.state.snapshot.states.objects.map(o=>o.instance_id).sort()')

        def seek(step):
            page.locator('#timeline').evaluate('(el,value)=>{el.value=value;el.dispatchEvent(new Event("change",{bubbles:true}));}', step)
            page.wait_for_function('(step)=>MAP_PLATFORM.state.snapshot?.step===step && MAP_PLATFORM.state.step===step', arg=step, timeout=60000)

        seek(137)
        check('timeline displays only observations available at frame 137',
              page.evaluate('MAP_PLATFORM.state.snapshot.states.objects.length') == 61
              and page.evaluate('MAP_PLATFORM.state.snapshot.states.events.length') == 16)
        page.locator('[data-camera=rear]').click()
        check('review camera can look behind without advancing time',
              page.evaluate('MAP_PLATFORM.camera.yaw') == 180 and page.evaluate('MAP_PLATFORM.state.step') == 137)
        page.screenshot(path=str(args.out/'states-rear.png'), full_page=True)
        seek(0)
        check('backward scrub removes future state instances', page.evaluate('MAP_PLATFORM.state.snapshot.states.objects.map(o=>o.instance_id).sort()') == baseline)
        page.locator('#play-pause').click()
        page.wait_for_function('MAP_PLATFORM.state.step>=2', timeout=20000)
        page.locator('#play-pause').click()
        check('play and pause work', not page.evaluate('MAP_PLATFORM.state.playing'))

        page.evaluate('MAP_PLATFORM.selectScene("nuscenes-combined-demo")')
        page.wait_for_function('MAP_PLATFORM.state.key==="nuscenes-combined-demo" && MAP_PLATFORM.state.snapshot?.step===0')
        check('one same-scene payload contains both structural edits and current states',
              page.evaluate('MAP_PLATFORM.state.scene.changes.length') == 1
              and page.evaluate('MAP_PLATFORM.state.snapshot.states.events.length') > 0
              and page.evaluate('MAP_PLATFORM.state.scene.source.synthetic') is True)
        page.locator('#map-version').select_option('compare')
        check('before and after share the same scene clock and camera', page.locator('#split-labels').is_visible())
        page.locator('[data-tab=structure]').click()
        page.locator('#change-list .change-item').first.click()
        check('selected road edit shows explicit before/after geometry', page.locator('.before-after').is_visible())
        page.screenshot(path=str(args.out/'combined-layers.png'), full_page=True)
        page.locator('#map-version').select_option('updated')
        seek(30)
        with page.expect_download() as download_info:
            page.locator('#export-planning').click()
        download = download_info.value
        data = json.loads(Path(download.path()).read_text())
        check('Planning export binds current map, timestamp and both layers without a recorded route',
              data['step'] == 30 and data['route'] is None and 'tracks' not in data
              and len(data['map_state_snapshot']['events']) > 0 and data['map_version'])
        download.save_as(str(args.out/'planning-current.json'))

        page.evaluate('MAP_PLATFORM.selectScene("argoverse-winter2021")')
        page.wait_for_function('MAP_PLATFORM.state.key==="argoverse-winter2021" && MAP_PLATFORM.state.scene?.changes.length===170')
        page.locator('[data-tab=validation]').click()
        page.wait_for_function('MAP_PLATFORM.state.validation?.detection?.available', timeout=60000)
        detection = page.evaluate('MAP_PLATFORM.state.validation.detection')
        check('detector accuracy is independent of reference-map application',
              abs(detection['metrics']['precision']-9/14)<1e-9
              and abs(detection['metrics']['recall']-9/31)<1e-9)
        check('accuracy metrics are visible to the user', '64.3%' in page.locator('#validation-panel').inner_text()
              and '29.0%' in page.locator('#validation-panel').inner_text())
        page.screenshot(path=str(args.out/'argoverse-validation.png'), full_page=True)

        if args.engine:
            page.evaluate('MAP_PLATFORM.selectScene("nuscenes-combined-demo")')
            page.wait_for_function('MAP_PLATFORM.state.key==="nuscenes-combined-demo" && MAP_PLATFORM.state.snapshot?.step===0')
            seek(30)
            page.locator('#start-engine').click()
            wait_engine(page, 30)
            result = page.evaluate('MAP_PLATFORM.state.engine.last_result')
            check('real MetaDrive opens at the selected frame with both kinds of changes',
                  result['step'] == 30 and result.get('export_verified') is True
                  and result['structural_changes'] == 1 and result['state_summary']['events']['features'] > 0)
            initial_pose = result['position_scenario_m']
            page.locator('[data-camera=rear]').click()
            page.wait_for_function('MAP_PLATFORM.state.engine.last_result?.camera.yaw===180 && !MAP_PLATFORM.state.engineCommandBusy', timeout=120000)
            check('rear camera changes the real engine view without moving ego',
                  page.evaluate('MAP_PLATFORM.state.engine.last_result.position_scenario_m') == initial_pose
                  and page.evaluate('MAP_PLATFORM.state.engine.last_result.step') == 30)
            page.screenshot(path=str(args.out/'metadrive-rear.png'), full_page=True)
            seek(5)
            wait_engine(page, 5)
            early = page.evaluate('MAP_PLATFORM.state.engine.last_result')
            seek(30)
            wait_engine(page, 30)
            repeated = page.evaluate('MAP_PLATFORM.state.engine.last_result')
            check('real engine backward/forward seek reconstructs the same world',
                  repeated['position_scenario_m'] == initial_pose
                  and repeated['persistent_obstacle_ids'] == result['persistent_obstacle_ids']
                  and early['persistent_obstacle_count'] < repeated['persistent_obstacle_count'])
            page.locator('[data-tab=states]').click()
            page.locator('.detail-focus').click()
            page.wait_for_function('MAP_PLATFORM.state.engine.last_result?.camera.target==="selection" && !MAP_PLATFORM.state.engineCommandBusy', timeout=120000)
            check('event list focuses actual MetaDrive annotations', page.evaluate('MAP_PLATFORM.state.engine.last_result.step') == 30)
            page.locator('#map-version').select_option('original')
            wait_engine(page, 30, 'original')
            original = page.evaluate('MAP_PLATFORM.state.engine.last_result')
            check('map version switch keeps the frame and attached states',
                  original['map_features_sha256'] != repeated['map_features_sha256']
                  and original['state_summary'] == repeated['state_summary'])
            page.locator('[data-tab=structure]').click()
            page.locator('#change-list .change-item').first.click()
            page.wait_for_function('MAP_PLATFORM.state.engine.last_result?.camera.target==="map" && !MAP_PLATFORM.state.engineCommandBusy', timeout=120000)
            check('original map can focus the corresponding changed location without applying the edit',
                  page.evaluate('MAP_PLATFORM.state.engine.last_result.map_features_sha256') == original['map_features_sha256']
                  and page.evaluate('MAP_PLATFORM.state.engine.last_result.step') == 30)
            page.locator('#map-version').select_option('updated')
            wait_engine(page, 30, 'updated')
            check('returning to updated map restores its verified hash',
                  page.evaluate('MAP_PLATFORM.state.engine.last_result.map_features_sha256') == repeated['map_features_sha256'])
            advances = []
            def record_advance(request):
                if request.method == 'POST' and request.url.endswith('/api/engine/command'):
                    body = request.post_data_json
                    if body.get('command') == 'advance':
                        advances.append(body['steps'])
            page.on('request', record_advance)
            for speed, expected_frames in [('0.5', 1), ('1', 2)]:
                advances.clear()
                before_step = page.evaluate('MAP_PLATFORM.state.step')
                page.locator('#play-speed').select_option(speed)
                page.locator('#play-pause').click()
                page.wait_for_function('(step)=>MAP_PLATFORM.state.step>step', arg=before_step, timeout=60000)
                page.locator('#play-pause').click()
                page.wait_for_function('!MAP_PLATFORM.state.engineCommandBusy', timeout=60000)
                assert advances and all(value == expected_frames for value in advances), (speed, advances)
            check('real engine half-speed advances fewer source frames than normal speed', True)
            page.screenshot(path=str(args.out/'metadrive-both-layers.png'), full_page=True)

        check('no uncaught browser errors throughout', not errors)
        (args.out/'checks.json').write_text(json.dumps({'passed':checks,'browser_errors':errors},ensure_ascii=False,indent=2))
        browser.close()


def wait_engine(page, step, version=None):
    page.wait_for_function('args=>MAP_PLATFORM.state.engine.ready && !MAP_PLATFORM.state.engineCommandBusy && '
        'MAP_PLATFORM.state.engine.last_result?.step===args.step && '
        '(!args.version || MAP_PLATFORM.state.engine.version===args.version)',
        arg={'step':step,'version':version}, timeout=180000)
    page.wait_for_function('document.querySelector("#engine-frame").complete && document.querySelector("#engine-frame").naturalWidth>0', timeout=30000)


if __name__ == '__main__':
    main()
