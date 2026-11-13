#!/usr/bin/env python3
"""Browser-only regressions for the driving sidebar and rendering pipeline.

Uses repository map geometry and explicit completed-pose fixtures; it does not
start MetaDrive or claim to validate physics. Run the real interactive checker
separately for native driving/collision evidence.
"""
import argparse
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import subprocess
import threading
from urllib.parse import parse_qs, urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/driving-ui-check')
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--data-python', default='.venv-sim/bin/python')
    parser.add_argument('--collision-report', help='Optional saved real native collision report to verify display')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    data = subprocess.run([str(root / args.data_python), '-c',
        'import json;from pathlib import Path;from map_update.simulation_platform_data import load_scene,build_snapshot,catalog;'
        'r=Path.cwd();s={k:load_scene(r,k) for k in ("nuscenes-scene0980","argoverse-winter2021")};'
        'print(json.dumps({"scenes":s,"snapshots":{k:build_snapshot(v,0) for k,v in s.items()},"catalog":catalog(r)}))'],
        cwd=root, text=True, capture_output=True, check=True)
    fixtures = json.loads(data.stdout)
    model = {'scene': 'nuscenes-scene0980', 'step': 0, 'revision': 0, 'playing': True,
             'epoch': 1, 'raised': False, 'collision': False}
    checks, errors, posts = [], [], []
    native_report = json.loads(Path(args.collision_report).read_text()) if args.collision_report else None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS:', name, flush=True)

    def result():
        scene = fixtures['scenes'][model['scene']]
        rows = {}
        for index, identifier in enumerate(['ego', 'P1', 'P2', 'P3', 'P4', 'P5']):
            center = [scene['origin'][0] + index * 3 + model['step'], scene['origin'][1] + index * 2, 1.0]
            basis = {'forward': [1, 0, 0], 'left': [0, 1, 0], 'up': [0, 0, 1]}
            if identifier == 'P1' and model['raised']:
                center[2] = 2.75
                basis = {'forward': [0, 1, 0], 'left': [0, 0, 1], 'up': [1, 0, 0]}
            contacts = [{'object_id': 'source-barrier-42', 'type': 'TRAFFIC_BARRIER', 'step': model['step']}]
            rows[identifier] = {'vehicle_id': identifier, 'mode': 'external' if identifier == 'ego' else 'ai',
                'position_city_m': center[:2], 'position_scenario_m': center[:2], 'heading_rad': 0,
                'chassis_center_city_m': center, 'orientation_basis_city': basis,
                'length_m': 4.5, 'width_m': 1.8, 'height_m': 1.5, 'speed_m_s': 1.,
                'collision_active': identifier == 'P1' and model['collision'],
                'current_contacts': contacts if identifier == 'P1' and model['collision'] else [],
                'last_collision': {'step': 1, 'contacts': contacts} if identifier == 'P1' and model['raised'] else None,
                'collision_event_count': int(identifier == 'P1' and model['raised'])}
        if model.get('native_frame'):
            rows['P1'] = dict(deepcopy(model['native_frame']), mode='manual')
        snapshot = deepcopy(fixtures['snapshots'][model['scene']])
        snapshot.update(step=model['step'], time_s=model['step']*.1, clock='interactive_simulation')
        snapshot['actors'] = [dict(vehicle, id=id, interactive_vehicle=id, is_ego=id == 'ego', persistent=False,
            position=vehicle['position_city_m'], heading=0, length=4.5, width=1.8, height=1.5)
            for id, vehicle in rows.items()] + deepcopy(scene.get('static_overview_actors', []))
        # Deliberately compact: static_actors exists only in snapshot.actors.
        return {'step': model['step'], 'state_id': f"state-{model['epoch']}-{model['revision']}-{model['step']}",
            'frame_id': None, 'image_available': False, 'sequence': model['revision'] + model['step'],
            'scene_id': scene['scene_id'], 'dataset': scene['dataset'], 'live_engine': True,
            'map_features_sha256': scene['versions']['updated']['map_sha256'], 'snapshot': snapshot,
            'interactive': {'enabled': True, 'step': model['step'], 'sim_time_s': model['step']*.1, 'vehicles': rows}}

    def envelope():
        frame = result()
        return {'active': True, 'playing': model['playing'], 'phase': 'running' if model['playing'] else 'paused',
            'room': {'session_id': 'ui-fixture-'+str(model['epoch']), 'epoch': model['epoch'],
                     'scene_id': model['scene'], 'version': 'updated', 'step': model['step'],
                     'vehicles': {id: {'mode': row['mode'], 'claimed': False} for id, row in frame['interactive']['vehicles'].items()}},
            'engine': {'last_result': frame}}

    class Files(SimpleHTTPRequestHandler):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, directory=str(root / 'map_update'), **kwargs)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Files)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source = (root / 'map_update/simulation_platform.js').read_text()
    source = source.replace('viewer=new ReviewRenderer()', 'viewer=window.__reviewViewer=new ReviewRenderer()', 1)
    source = source.replace('batchStatic(group){', 'batchStatic(group){const before=window.__digest(group);', 1)
    source = source.replace('group.add(node);}\n    }\n    actorPose',
        'group.add(node);}\n      window.__batchChecks.push(JSON.stringify(before)===JSON.stringify(window.__digest(group)));\n    }\n    actorPose', 1)
    digest = r"""window.__batchChecks=[];window.__digest=group=>{
      const result={};const round=value=>Math.round(value*1e5)/1e5;
      for(const node of group.children){if(!node.geometry||!(node.isMesh||node.isLine))continue;
        const m=node.material,p=node.geometry.getAttribute('position'),idx=node.geometry.index,d=node.geometry.getAttribute('lineDistance');
        const key=JSON.stringify([node.isMesh?'mesh':'line',m.type,m.color?.getHex(),m.opacity,m.depthWrite,m.dashSize,m.gapSize]);
        const bucket=result[key]||(result[key]={primitives:0,sum:0,xor:0});
        const vertex=i=>[round(p.getX(i)+node.position.x),round(p.getY(i)+node.position.y),round(p.getZ(i)+node.position.z),m.isLineDashedMaterial?round(d?.getX(i)||0):null];
        const emit=vertices=>{let hash=2166136261;for(const char of JSON.stringify(vertices)){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}bucket.primitives++;bucket.sum=(bucket.sum+(hash>>>0))>>>0;bucket.xor=(bucket.xor^hash)>>>0;};
        if(node.isMesh){const count=idx?idx.count:p.count;for(let i=0;i<count;i+=3)emit([0,1,2].map(j=>vertex(idx?idx.getX(i+j):i+j)));}
        else{const stride=node.isLineSegments?2:1;for(let i=0;i<p.count-1;i+=stride)emit([vertex(i),vertex(i+1)]);}}
      return Object.fromEntries(Object.entries(result).sort());};"""

    def intercept(route):
        request = route.request
        path = urlparse(request.url).path
        query = parse_qs(urlparse(request.url).query)
        if path.endswith('/simulation_platform.js'):
            route.fulfill(body=source, content_type='application/javascript')
            return
        if request.method == 'POST':
            posts.append(path)
        if path == '/api/scenes':
            payload = fixtures['catalog']
        elif path in ['/api/scenes/'+key for key in fixtures['scenes']]:
            payload = fixtures['scenes'][path.rsplit('/', 1)[-1]]
        elif path.endswith('/snapshot'):
            key = path.split('/')[-2]
            payload = deepcopy(fixtures['snapshots'][key])
            payload['step'] = int(query['step'][0])
            payload['version'] = query['version'][0]
            payload['map_sha256'] = fixtures['scenes'][key]['versions'][payload['version']]['map_sha256']
        elif path == '/api/interactive/open':
            model.update(scene=request.post_data_json['scene_id'], step=0, raised=False, collision=False)
            payload = {'room': {'session_id': 'ui-fixture-'+str(model['epoch']), 'epoch': model['epoch'],
                               'host_token': 'fixture-host', 'join_token': 'fixture-join'}, 'phase': 'starting'}
        elif path == '/api/interactive/state':
            payload = envelope()
        elif path == '/api/interactive/control':
            action = request.post_data_json['action']
            if action == 'reset':
                model.update(epoch=model['epoch']+1, step=0, raised=False, collision=False, playing=False, native_frame=None)
                payload = {'room': {'session_id': 'ui-fixture-'+str(model['epoch']), 'epoch': model['epoch'],
                                   'host_token': 'fixture-host', 'join_token': 'fixture-join'}, 'phase': 'starting'}
            else:
                model['playing'] = action == 'play'
                payload = {'accepted': True, 'playing': model['playing'], 'phase': 'paused'}
        else:
            raise AssertionError(path)
        route.fulfill(body=json.dumps(payload), content_type='application/json')

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.add_init_script(digest)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', intercept)
        page.route('**/simulation_platform.js', intercept)
        try:
            page.goto(f'http://127.0.0.1:{server.server_port}/simulation_platform.html')
            page.wait_for_function('window.MAP_PLATFORM?.state.snapshot != null')
            before = page.locator('#viewport').bounding_box()
            page.locator('#open-driving').click()
            after = page.locator('#viewport').bounding_box()
            check('opening driving controls does not resize or overlap desktop map', before == after and
                  page.locator('#driving-pane').bounding_box()['x'] >= after['x'] + after['width'])
            state_id=page.evaluate('MAP_PLATFORM.state.scene.state_overview.events[0].properties.event_id')
            page.evaluate('(id)=>MAP_PLATFORM.selectItem("state",id)',state_id)
            check('attached-state selection retains its outline, label and inspector',page.evaluate('(id)=>MAP_PLATFORM.state.selected.id===id && __reviewViewer.labels.some(row=>row.kind==="state"&&row.text) && __reviewViewer.groups.states.children.length>0',state_id))
            page.locator('#start-interactive').click()
            page.wait_for_function('MAP_PLATFORM.interactive.frame?.step === 0')
            page.wait_for_timeout(150)
            check('compact snapshots retain all physical static actors', page.evaluate('[...__reviewViewer.actorMeshes.values()].filter(mesh=>mesh.visible).length') ==
                  len(fixtures['scenes']['nuscenes-scene0980']['static_overview_actors']) + 6)
            model.update(step=1, raised=True, collision=True)
            page.wait_for_function('MAP_PLATFORM.state.step === 1')
            page.wait_for_timeout(180)
            pose = page.evaluate('MAP_PLATFORM.visual.poses.P1')
            expected = result()['interactive']['vehicles']['P1']['chassis_center_city_m']
            check('actual Bullet box center keeps height and does not flatten collision pose',
                  max(abs(a-b) for a, b in zip(pose['center_city_m'], expected)) < 1e-5)
            check('physical orientation basis becomes full 3D rotation', page.evaluate('''()=>{
              const q=__reviewViewer.actorMeshes.get('P1').quaternion;
              return new THREE.Vector3(1,0,0).applyQuaternion(q).distanceTo(new THREE.Vector3(0,1,0))<1e-5
                &&new THREE.Vector3(0,0,1).applyQuaternion(q).distanceTo(new THREE.Vector3(1,0,0))<1e-5;}'''))
            check('active and retained collisions name their object and physical step',
                  'source-barrier-42' in page.locator('[data-vehicle="P1"] [data-field="collision"]').inner_text() and
                  page.locator('[data-vehicle="P1"]').evaluate('(node)=>node.classList.contains("collision-active")'))
            model.update(step=2, collision=False)
            page.wait_for_function('MAP_PLATFORM.state.step === 2')
            check('collision history survives after contact ends', '上次碰撞' in page.locator('[data-vehicle="P1"] [data-field="collision"]').inner_text())
            page.wait_for_timeout(150)
            before_pose = page.evaluate('MAP_PLATFORM.visual.poses.P1.center_city_m[0]')
            model['step'] = 3
            page.wait_for_function('MAP_PLATFORM.state.step === 3')
            samples = page.evaluate('''()=>new Promise(resolve=>{const rows=[],start=performance.now();function sample(){rows.push({visual:MAP_PLATFORM.visual.poses.P1.center_city_m[0],truth:MAP_PLATFORM.interactive.frame.interactive.vehicles.P1.chassis_center_city_m[0],interpolation:MAP_PLATFORM.visual.interpolation});if(performance.now()-start<150)requestAnimationFrame(sample);else resolve(rows);}sample();})''')
            check('smoothing remains between received poses and never changes authoritative state', all(
                before_pose-1e-5 <= row['visual'] <= row['truth']+1e-5 for row in samples) and
                all(not row['interpolation'] or row['interpolation']['max_delay_ms'] <= 100 for row in samples))
            model.update(step=4, playing=False)
            page.wait_for_function('MAP_PLATFORM.state.step === 4')
            paused = page.evaluate('MAP_PLATFORM.visual.poses.P1')
            page.wait_for_timeout(220)
            check('paused frames do not continue moving or retain interpolation', paused == page.evaluate('MAP_PLATFORM.visual.poses.P1') and
                  page.evaluate('MAP_PLATFORM.visual.interpolation') is None)
            if native_report:
                native = next(frame for frame in native_report['frames'] if frame.get('collision_active'))
                model.update(step=native['step'], native_frame=native)
                page.wait_for_function('(step)=>MAP_PLATFORM.state.step===step', arg=native['step'])
                page.wait_for_timeout(180)
                actual = page.evaluate('MAP_PLATFORM.visual.poses.P1.center_city_m')
                check('saved real Bullet contact frame preserves its actual center and object identity',
                      max(abs(a-b) for a,b in zip(actual,native['chassis_center_city_m'])) < 1e-5 and
                      native['current_contacts'][0]['object_id'][:17] in page.locator('[data-vehicle="P1"] [data-field="collision"]').inner_text())
                page.evaluate('MAP_PLATFORM.followInteractive("P1")')
                page.wait_for_timeout(150)
            page.screenshot(path=str(output / 'driving-sidebar-desktop.png'))
            page.locator('#interactive-reset').click()
            page.wait_for_function('MAP_PLATFORM.interactive.room.epoch === 2 && MAP_PLATFORM.state.step === 0')
            page.wait_for_timeout(150)
            check('reset discards prior raised pose and smoothing history',
                  abs(page.evaluate('MAP_PLATFORM.visual.poses.P1.center_city_m[2]') - 1.) < 1e-5 and
                  page.evaluate('MAP_PLATFORM.visual.interpolation') is None)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(150)
            viewport, pane = page.locator('#viewport').bounding_box(), page.locator('#driving-pane').bounding_box()
            check('small-screen driving controls remain outside the map and page width', pane['y'] >= viewport['y']+viewport['height'] and
                  page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
            page.locator('#driving-pane').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'driving-sidebar-mobile.png'))
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('#leave-interactive').click()
            page.wait_for_function('MAP_PLATFORM.interactive.room === null')
            check('leaving a room clears interpolation and pending animation',page.evaluate('__reviewViewer.motion === null && MAP_PLATFORM.visual.interpolation === null'))
            page.evaluate('MAP_PLATFORM.selectScene("argoverse-winter2021")')
            page.locator('#changes-tab').click()
            page.wait_for_function('MAP_PLATFORM.state.scene?.dataset === "argoverse" && MAP_PLATFORM.state.snapshot != null')
            target = page.evaluate('MAP_PLATFORM.state.scene.changes.find(row=>row.before&&row.after)?.feature_id')
            page.evaluate('(id)=>MAP_PLATFORM.selectItem("structure",id)', target)
            page.wait_for_timeout(150)
            check('road selection retains original and updated highlighters after batching', page.evaluate(
                '__reviewViewer.groups.selected.children.length>0 && __reviewViewer.groups["selection-original"].children.length>0'))
            page.select_option('#map-version', 'original')
            page.wait_for_function('__reviewViewer.groups["base-original"].visible && !__reviewViewer.groups["base-updated"].visible')
            check('original map selection still switches road geometry', page.evaluate(
                '__reviewViewer.groups["base-original"].visible && !__reviewViewer.groups["base-updated"].visible'))
            page.select_option('#map-version', 'updated')
            page.wait_for_function('__reviewViewer.groups["base-updated"].visible && !__reviewViewer.groups["base-original"].visible')
            check('updated map selection still switches road geometry', page.evaluate(
                '__reviewViewer.groups["base-updated"].visible && !__reviewViewer.groups["base-original"].visible'))
            check('batching preserves every triangle, line segment, dashed distance and material',
                  page.evaluate('__batchChecks.length > 6 && __batchChecks.every(Boolean)'))
            check('camera and map review issue no simulation stepping commands', '/api/engine/command' not in posts)
            check('browser has no JavaScript errors', not errors)
            report = {'ok': True, 'ui_fixture_only': True, 'physics_mocked_for_rendering_checks': True,
                      'checks': checks, 'desktop_viewport': after, 'interpolation_samples': samples,
                      'ui_source_sha256': hashlib.sha256((root/'map_update/simulation_platform.js').read_bytes()).hexdigest()}
        except Exception as error:
            report = {'ok': False, 'ui_fixture_only': True, 'checks': checks, 'error': str(error), 'page_errors': errors}
            page.screenshot(path=str(output / 'failure.png'))
        finally:
            browser.close()
            server.shutdown()
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'ok': report['ok'], 'checks': len(checks), 'error': report.get('error')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
