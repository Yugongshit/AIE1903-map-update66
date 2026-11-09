"""Exercise the local Three.js map against immutable supplied geometry.

Checks an actual Chromium WebGL canvas, source XY, shared-camera clipping,
interaction, mobile gestures and the retained 2D fallback. No detector is run.
"""
import argparse
from functools import partial
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from math import hypot
from pathlib import Path
from threading import Thread

from PIL import Image
from playwright.sync_api import sync_playwright


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def geometry_points(geometry):
    if geometry['type'] == 'GeometryCollection':
        return [p for child in geometry['geometries'] for p in geometry_points(child)]
    def descend(value):
        if value and isinstance(value[0], (int, float)):
            return [value[:2]]
        return [p for item in value for p in descend(item)]
    return descend(geometry.get('coordinates', []))


def color_counts(png):
    picture = Image.open(BytesIO(png)).convert('RGB')
    pixels = list(getattr(picture, 'get_flattened_data', picture.getdata)())
    return {'width': picture.width, 'height': picture.height,
            'colors': len(set(pixels)),
            'yellow': sum(r > 140 and g > 100 and b < 150 and r > b * 1.45 and g > b * 1.3
                          for r, g, b in pixels),
            'red': sum(r > 110 and r > g * 1.65 and r > b * 1.3 for r, g, b in pixels)}


def run(directory):
    root = directory.resolve()
    source_bytes = (root / 'result.json').read_bytes()
    source = json.loads(source_bytes)
    checks, errors, failures, external = [], [], [], []
    report = {'status': 'FAIL', 'checks': checks, 'page_errors': errors,
              'failed_requests': failures, 'external_requests': external,
              'result_sha256': sha256(source_bytes).hexdigest()}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/'

    def debug(page):
        return page.evaluate('PIPELINE_STUDIO.map3d.getDebug()')

    def settle(page):
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def listen(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: failures.append([response.url, response.status]) if response.status >= 400 else None)
        page.on('request', lambda request: external.append(request.url) if not request.url.startswith(base) and not request.url.startswith(('data:', 'blob:')) else None)

    def start(page, path='en/index.html'):
        page.goto(base + path)
        page.wait_for_function('window.PIPELINE_STUDIO && PIPELINE_STUDIO.map3d')
        page.wait_for_function("PIPELINE_STUDIO.map3d.getDebug().ready && PIPELINE_STUDIO.map3d.getDebug().mode === '3d'")
        settle(page)

    def snap(page, filename):
        settle(page)
        png = page.locator('#composite-map-3d').screenshot(path=str(root / filename))
        return color_counts(png)

    try:
        with sync_playwright() as pw:
            browser = None
            for flags in (['--no-sandbox'], ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']):
                probe_browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=flags)
                probe = probe_browser.new_page()
                available = probe.evaluate("() => { const c=document.createElement('canvas'); const g=c.getContext('webgl2'); return !!g; }")
                probe.close()
                if available:
                    browser = probe_browser
                    report['chromium_flags'] = flags
                    break
                probe_browser.close()
            if browser is None:
                raise AssertionError('Chromium has no usable WebGL2 context with default or SwiftShader settings')
            context = browser.new_context(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
            page = context.new_page()
            listen(page)
            start(page)
            page.evaluate('window.__originalPipelineJSON = JSON.stringify(PIPELINE_STUDIO.data)')
            page.screenshot(path=str(root/'three_nuscenes_first_view_en.png'), full_page=True)
            page.locator('#map-preset-compare').click()
            settle(page)
            first = debug(page)
            check('3D is the default visible canvas and uses an actual live WebGL renderer',
                  first['ready'] and not first['contextLost'] and page.locator('#composite-map-3d').is_visible()
                  and not page.locator('#composite-map').is_visible())
            check('local Three.js and the local 3D module load without external services',
                  page.evaluate("typeof THREE === 'object' && typeof Map3DRenderer?.create === 'function'") and not external)
            check('nuScenes actions and colors retain 23 states and zero accepted structural edits',
                  page.locator('#map-count-state').inner_text() == '23'
                  and page.locator('#map-count-confirmed').inner_text() == '0'
                  and page.locator('#map-count-closed').inner_text() == '6')
            check('schematic event height is explicitly disclosed in both metadata and visible controls',
                  first['schematicHeights']['state'] > 0 and 'schematic' in page.locator('.composite-panel').inner_text().lower())

            # Source geometry is checked independently against the exported context.
            features = page.evaluate('PIPELINE_STUDIO.map.features()')
            actual = {(f['id'], f.get('view', 'after')): f for f in features}
            origin = first['sourceOriginXY']
            source_matches = []
            world_matches = []
            for rendered in first['features']:
                original = actual[(rendered['id'], rendered.get('view', 'after'))]
                points = geometry_points(original['geometry_city'])
                expected_bounds = [min(p[0] for p in points), min(p[1] for p in points),
                                   max(p[0] for p in points), max(p[1] for p in points)]
                source_matches.append(all(abs(a-b) < 1e-7 for a, b in zip(rendered['source_xy_bounds'], expected_bounds)))
                xy, xyz = rendered['source_xy_sample'], rendered['world_xyz_sample']
                xy = [xy] if xy and isinstance(xy[0], (int, float)) else xy
                xyz = [xyz] if xyz and isinstance(xyz[0], (int, float)) else xyz
                world_matches.extend(abs(a[0]-origin[0]-b[0]) < 1e-5 and abs(-(a[1]-origin[1])-b[2]) < 1e-5
                                     for a, b in zip(xy, xyz))
            check('every rendered nuScenes feature keeps its supplied XY bounds', source_matches and all(source_matches))
            check('world coordinates are only a centered XY transform plus display height', world_matches and all(world_matches))
            check('unaccepted structural candidates do not acquire 3D meshes or red markers',
                  not any(f['layer'] == 'structure' for f in first['features']))
            passes = first['passes']
            check('before and after use one camera and the same projection',
                  len(passes) == 2 and passes[0]['cameraMatrix'] == passes[1]['cameraMatrix'])
            state_ids = {f['id'] for f in features if f['layer'] == 'state'}
            before_pass = next(p for p in passes if p['side'] == 'before')
            after_pass = next(p for p in passes if p['side'] == 'after')
            check('all state meshes are restricted to the updated scissor pass',
                  not state_ids.intersection(before_pass['visibleFeatureIds'])
                  and state_ids.issubset(set(after_pass['visibleFeatureIds'])))

            page.locator('#map-fit-all').click()
            page.locator('#map-preset-after').click()
            after_pixels = snap(page, 'three_nuscenes_updated_en.png')
            page.locator('#map-preset-before').click()
            before_pixels = snap(page, 'three_nuscenes_original_en.png')
            check('real nuScenes WebGL pixels contain terrain and yellow state geometry after update',
                  after_pixels['colors'] > 100 and after_pixels['yellow'] > 15)
            check('full original view removes yellow and red event overlays',
                  before_pixels['yellow'] < after_pixels['yellow'] * .1 and before_pixels['red'] < 8)
            report['nuscenes_pixels'] = {'before': before_pixels, 'after': after_pixels}
            check('rightmost divider reports no updated-area markers',
                  debug(page)['splitFraction'] == 1 and not any(m['visible_updated'] for m in debug(page)['markers']))

            page.locator('#map-preset-compare').click()
            page.locator('#map-camera-perspective').click()
            old_camera = debug(page)['camera']
            box = page.locator('#composite-map-3d').bounding_box()
            page.mouse.move(box['x'] + box['width'] * .73, box['y'] + box['height'] * .68)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * .87, box['y'] + box['height'] * .73, steps=9)
            page.mouse.up()
            settle(page)
            orbit_camera = debug(page)['camera']
            check('dragging the canvas actually rotates the shared 3D camera',
                  old_camera['position'] != orbit_camera['position'] and old_camera['azimuth'] != orbit_camera['azimuth'])
            check('rotation keeps the original and updated camera matrices identical',
                  debug(page)['passes'][0]['cameraMatrix'] == debug(page)['passes'][1]['cameraMatrix'])
            distance = debug(page)['camera']['distance']
            page.locator('#map-zoom-in').click()
            settle(page)
            check('zoom changes the actual 3D camera distance', debug(page)['camera']['distance'] < distance)
            page.locator('#map-camera-top').click()
            settle(page)
            check('top view changes the camera to an overhead angle',
                  debug(page)['camera']['view'] == 'top' and debug(page)['camera']['polar'] < .1)
            page.locator('#map-camera-perspective').click()
            settle(page)
            check('perspective view restores the tilted camera',
                  debug(page)['camera']['view'] == 'perspective' and debug(page)['camera']['polar'] > .2)
            page.locator('.map-options').evaluate('(element) => element.open = true')
            page.locator('#map-layer-state').uncheck()
            settle(page)
            check('state layer control removes state meshes from the renderer',
                  not any(f['layer'] == 'state' for f in debug(page)['features']))
            page.locator('#map-layer-state').check()
            page.locator('#map-fit-all').click()
            page.locator('#map-preset-after').click()
            settle(page)
            check('restoring the state layer restores every supplied state feature',
                  len([f for f in debug(page)['features'] if f['layer'] == 'state']) == 23)

            # Click a visible marker, using projected source positions rather than guessed coordinates.
            markers = [m for m in debug(page)['markers'] if m['visible_updated']]
            viewport = debug(page)['viewport']
            candidates = [m for m in markers if 60 < m['screen_xy'][0] < viewport['width']-60
                          and 80 < m['screen_xy'][1] < viewport['height']-70]
            check('real source events have visible projected 3D selection markers', bool(candidates))
            chosen = max(candidates, key=lambda marker: min(
                (hypot(marker['screen_xy'][0]-other['screen_xy'][0], marker['screen_xy'][1]-other['screen_xy'][1])
                 for other in markers if other['event_id'] != marker['event_id']), default=float('inf')))
            box = page.locator('#composite-map-3d').bounding_box()
            page.mouse.click(box['x'] + chosen['screen_xy'][0], box['y'] + chosen['screen_xy'][1])
            settle(page)
            check('clicking an updated 3D event selects the corresponding supplied record',
                  page.evaluate('PIPELINE_STUDIO.state.eventId') == chosen['event_id'])
            page.locator('#map-preset-before').click()
            old_selection = page.evaluate('PIPELINE_STUDIO.state.eventId')
            page.mouse.click(box['x'] + chosen['screen_xy'][0], box['y'] + chosen['screen_xy'][1])
            settle(page)
            check('the original-only region cannot select an updated event',
                  page.evaluate('PIPELINE_STUDIO.state.eventId') == old_selection)
            page.locator('#map-mode-2d').click()
            check('2D remains available as a usable visible map',
                  page.locator('#composite-map').is_visible() and not page.locator('#composite-map-3d').is_visible()
                  and debug(page)['mode'] == '2d')
            page.locator('#map-mode-3d').click()
            settle(page)
            check('switching back to 3D preserves the selected source record',
                  debug(page)['mode'] == '3d' and page.evaluate('PIPELINE_STUDIO.state.eventId') == old_selection)

            page.select_option('#dataset', 'argoverse')
            settle(page)
            argo = debug(page)
            page.screenshot(path=str(root/'three_argoverse_first_view_en.png'), full_page=True)
            reference = source['datasets']['argoverse']['material_reference_replay']
            expected_count = reference['summary']['accepted_material_changes']
            check('Argoverse defaults to the material reference replay in the 3D view',
                  page.evaluate('PIPELINE_STUDIO.state.mapSource') == 'reference'
                  and page.locator('#map-count-confirmed').inner_text() == str(expected_count))
            check('Argoverse 3D includes every accepted structure and no rejected candidate',
                  len([f for f in argo['features'] if f['layer'] == 'structure']) == expected_count)
            check('all supplied additions remain part of the accepted reference replay',
                  reference['summary']['added'] == 27
                  and len([c for c in reference['changes'] if c['operation'] == 'add']) == 27)
            model_before = {f['id'] for f in argo['features'] if f['layer'] == 'base' and f['view'] == 'before'}
            model_after = {f['id'] for f in argo['features'] if f['layer'] == 'base' and f['view'] == 'after'}
            prior_context = json.loads((root/'argoverse_map_context.json').read_text())
            expected_before = {item['element_id'] for item in prior_context['elements']}
            expected_after = {item['element_id'] for item in reference['updated_map_context']['elements']}
            check('reference original and modified base geometries are both rendered from their own maps',
                  model_before == expected_before and model_after == expected_after and model_before != model_after)
            argo_input = {(item['id'], item.get('view', 'after')): item
                          for item in page.evaluate('PIPELINE_STUDIO.map.features()')}
            argo_bounds_match = []
            for rendered in argo['features']:
                points = geometry_points(argo_input[(rendered['id'], rendered['view'])]['geometry_city'])
                expected = [min(p[0] for p in points), min(p[1] for p in points),
                            max(p[0] for p in points), max(p[1] for p in points)]
                argo_bounds_match.append(all(abs(a-b) < 1e-7 for a, b in zip(rendered['source_xy_bounds'], expected)))
            check('all Argoverse original, updated and change meshes keep their own source XY bounds',
                  argo_bounds_match and all(argo_bounds_match))
            page.locator('#map-fit-all').click()
            page.locator('#map-preset-after').click()
            argo_after = snap(page, 'three_argoverse_updated_en.png')
            page.locator('#map-preset-before').click()
            argo_before = snap(page, 'three_argoverse_original_en.png')
            check('updated Argoverse canvas visibly renders red accepted structures', argo_after['red'] > 50)
            check('original Argoverse canvas has no red change overlay', argo_before['red'] < argo_after['red']*.01)
            report['argoverse_pixels'] = {'before': argo_before, 'after': argo_after}
            page.locator('#map-preset-compare').click()
            page.locator('.composite-panel').screenshot(path=str(root/'three_argoverse_comparison_en.png'))
            page.select_option('#map-source', 'model')
            settle(page)
            check('switching to independent model results does not display truth changes as predictions',
                  page.locator('#map-count-confirmed').inner_text() == '0'
                  and not any(f['layer'] == 'structure' for f in debug(page)['features']))
            check('desktop interactions never mutate frozen source predictions or reference data',
                  page.evaluate('JSON.stringify(PIPELINE_STUDIO.data) === window.__originalPipelineJSON'))
            report['desktop_final_debug'] = debug(page)

            # Mobile runs actual touch events through the Chromium protocol.
            mobile_context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1,
                                                  is_mobile=True, has_touch=True)
            mobile = mobile_context.new_page()
            listen(mobile)
            start(mobile, 'zh/index.html?dataset=nuscenes')
            mobile.evaluate('window.__originalPipelineJSON = JSON.stringify(PIPELINE_STUDIO.data)')
            mobile.locator('#composite-map-3d').scroll_into_view_if_needed()
            check('Chinese mobile view explains schematic height and fits the document width',
                  '示意' in mobile.locator('.composite-panel').inner_text()
                  and mobile.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
            mobile.locator('#map-preset-after').click()
            box = mobile.locator('#composite-map-3d').bounding_box()
            cdp = mobile_context.new_cdp_session(mobile)
            x, y = box['x']+box['width']*.7, box['y']+box['height']*.5
            camera = debug(mobile)['camera']
            cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
            cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':x-70,'y':y+35}]})
            cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
            settle(mobile)
            check('one-finger touch drag rotates the real mobile camera', debug(mobile)['camera']['position'] != camera['position'])
            camera = debug(mobile)['camera']
            cx, cy = box['x']+box['width']*.5, box['y']+box['height']*.5
            cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':cx-30,'y':cy,'id':1},{'x':cx+30,'y':cy,'id':2}]})
            cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':cx-70,'y':cy,'id':1},{'x':cx+70,'y':cy,'id':2}]})
            cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
            settle(mobile)
            check('two-finger pinch changes the mobile camera distance', abs(debug(mobile)['camera']['distance']-camera['distance']) > .01)
            mobile.locator('#map-camera-reset').click()
            mobile.locator('#map-preset-compare').click()
            mobile.locator('.composite-panel').screenshot(path=str(root/'three_nuscenes_mobile_zh.png'))
            check('mobile touch and camera controls keep input data immutable',
                  mobile.evaluate('JSON.stringify(PIPELINE_STUDIO.data) === window.__originalPipelineJSON'))

            # Deliberately unavailable/lost WebGL must not make the map unusable.
            report['context_loss_test'] = mobile.evaluate("""() => {
                const canvas=document.getElementById('composite-map-3d');
                const gl=canvas.getContext('webgl2'), extension=gl?.getExtension('WEBGL_lose_context');
                if(extension){extension.loseContext();return 'actual_WEBGL_lose_context';}
                canvas.dispatchEvent(new Event('webglcontextlost',{cancelable:true}));
                return 'context_loss_event_fallback';
            }""")
            mobile.wait_for_function("PIPELINE_STUDIO.map3d.getDebug().mode === '2d'")
            check('WebGL context loss falls back to a working 2D map with a visible explanation',
                  mobile.locator('#composite-map').is_visible() and debug(mobile)['unavailable'])
            fallback_context = browser.new_context(viewport={'width': 1200, 'height': 900})
            fallback = fallback_context.new_page()
            fallback.add_init_script("""(() => { const original=HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext=function(kind,...args){
                    return /webgl/.test(kind) ? null : original.call(this,kind,...args);
                }; })();""")
            fallback.goto(base+'en/index.html')
            fallback.wait_for_function("window.PIPELINE_STUDIO && PIPELINE_STUDIO.map3d.getDebug().mode === '2d'")
            check('a browser without WebGL automatically retains 2D map controls',
                  fallback.locator('#composite-map').is_visible() and fallback.locator('#map-mode-3d').is_disabled())
            fallback.locator('#map-preset-after').click()
            check('fallback remains interactive and shows the same saved state count',
                  fallback.locator('#map-count-state').inner_text() == '23'
                  and fallback.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == 0)
            explicit = fallback_context.new_page()
            explicit.goto(base+'en/index.html?view=2d')
            explicit.wait_for_function('window.PIPELINE_STUDIO')
            check('the explicit 2D URL retains the existing top-down renderer',
                  explicit.locator('#composite-map').is_visible()
                  and explicit.evaluate("PIPELINE_STUDIO.map3d.getDebug().mode === '2d'"))
            missing_asset = fallback_context.new_page()
            missing_asset.route('**/assets/vendor/three.min.js*', lambda route: route.abort())
            missing_asset.goto(base+'en/index.html')
            missing_asset.wait_for_function("window.PIPELINE_STUDIO && PIPELINE_STUDIO.map3d.getDebug().mode === '2d'")
            check('a missing 3D dependency falls back to 2D without blocking the recorded map',
                  missing_asset.locator('#composite-map').is_visible()
                  and missing_asset.locator('#map-mode-3d').is_disabled())
            check('normal 3D interactions finish without uncaught browser errors', not errors)
            check('all normal 3D assets load locally without failed or external requests', not failures and not external)
            report['status']='PASS'
            browser.close()
    finally:
        report['count'] = len(checks)
        (root/'three_map_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        server.shutdown()
    print('PASS '+str(len(checks))+' 3D map checks')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    run(parser.parse_args().directory)
