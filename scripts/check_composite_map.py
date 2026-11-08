"""Check the delivered composite maps against source coordinates and browser pixels.

This is a regression check for the two supplied scenes, not a model evaluation.
It reads original reference maps independently of the presentation-map builder.
"""

import argparse
from collections import Counter
import csv
from functools import partial
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]


def lines(geometry):
    kind = geometry.get('type')
    if kind == 'GeometryCollection':
        return [line for item in geometry['geometries'] for line in lines(item)]
    coords = geometry.get('coordinates', [])
    if kind == 'LineString':
        return [coords]
    if kind in {'Polygon', 'MultiLineString'}:
        return coords
    if kind == 'MultiPolygon':
        return [ring for polygon in coords for ring in polygon]
    raise AssertionError('Unexpected source geometry: ' + str(kind))


def points(geometry):
    return [point[:2] for line in lines(geometry) for point in line]


def canonical(coordinates):
    vertices = tuple(tuple(point[:2]) for point in coordinates)
    return min(vertices, vertices[::-1])


def frozen(value):
    """Keep structure while comparing numerically equal JSON ints and floats."""
    if isinstance(value, dict):
        return tuple((key, frozen(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(frozen(item) for item in value)
    return value


def bounds(geometries):
    all_points = [point for geometry in geometries for point in points(geometry)]
    return [min(p[0] for p in all_points), min(p[1] for p in all_points),
            max(p[0] for p in all_points), max(p[1] for p in all_points)]


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run(directory, raw_root):
    root = directory.resolve()
    source_bytes = (root / 'result.json').read_bytes()
    source = json.loads(source_bytes)
    checks, errors, requests = [], [], []
    report = {'status': 'FAIL', 'checks': checks, 'page_errors': errors,
              'failed_requests': requests, 'result_sha256': sha256(source_bytes).hexdigest()}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    def verify_sources(view):
        contexts = {name: dataset['map_context'] for name, dataset in view['datasets'].items()}
        for name, context in contexts.items():
            check(name + ' embedded map equals the exported reference context',
                  context == json.loads((root / (name + '_map_context.json')).read_text()))
            check(name + ' map context explicitly excludes current evaluation maps',
                  context['provenance']['gt_current_map_used'] is False)
        original_path = raw_root / 'maps/expansion/singapore-onenorth.json'
        original = json.loads(original_path.read_text())
        check('nuScenes base map is the exact frozen original map file',
              contexts['nuscenes']['provenance']['map_sha256'] == sha256(original_path.read_bytes()).hexdigest()
              == source['datasets']['nuscenes']['provenance']['map_sha256'])
        nodes = {row['token']: [row['x'], row['y']] for row in original['node']}
        original_lines = {row['token']: row for row in original['line']}
        polygons = {row['token']: row for row in original['polygon']}

        def ring(tokens):
            result = [nodes[token] for token in tokens]
            return result if result[0] == result[-1] else result + [result[0]]

        for element in contexts['nuscenes']['elements']:
            layer, token = element['element_id'].split(':', 1)
            record = next(row for row in original[layer] if row['token'] == token)
            if 'line_token' in record:
                expected = {'type': 'LineString', 'coordinates': [nodes[token] for token in original_lines[record['line_token']]['node_tokens']]}
            else:
                polygon = polygons[record['polygon_token']]
                expected = {'type': 'Polygon', 'coordinates': [ring(polygon['exterior_node_tokens'])] + [ring(hole['node_tokens']) for hole in polygon['holes']]}
            assert element['geometry_city'] == expected, element['element_id']
        check('all 157 nuScenes base features retain original node coordinates and polygon holes',
              len(contexts['nuscenes']['elements']) == 157
              and Counter(e['layer'] for e in contexts['nuscenes']['elements']) == {
                  'lane_connector': 49, 'lane': 43, 'road_segment': 34,
                  'lane_divider': 20, 'ped_crossing': 10, 'road_divider': 1})

        prior_paths = list((raw_root / 'deliveries').glob('*_M2_v1/prior_map.json'))
        assert len(prior_paths) == 1, 'Expected one frozen M2 prior map'
        prior = json.loads(prior_paths[0].read_text())
        check('Argoverse uses the frozen M2 prior rather than current ground truth',
              contexts['argoverse']['provenance']['map_sha256'] == sha256(prior_paths[0].read_bytes()).hexdigest()
              and any(item['path'].endswith('/prior_map.json')
                      and item['role'] == 'frozen_prediction_input'
                      and item['sha256'] == contexts['argoverse']['provenance']['map_sha256']
                      for item in source['sources']))
        expected_boundaries, expected_lanes = set(), []
        for lane in prior['lane_segments']:
            left, right = ([ [p['x'], p['y']] for p in lane[side + '_lane_boundary'] ] for side in ['left', 'right'])
            expected_boundaries.update([canonical(left), canonical(right)])
            polygon = left + right[::-1]
            if polygon[0] != polygon[-1]:
                polygon += [polygon[0]]
            expected_lanes.append({'type': 'Polygon', 'coordinates': [polygon]})
        argo_elements = contexts['argoverse']['elements']
        actual_boundaries = [element['geometry_city']['coordinates'] for element in argo_elements if element['geometry_city']['type'] == 'LineString']
        check('all 343 Argoverse physical boundaries are exact M2 vertices, deduplicated only by reversal',
              len(actual_boundaries) == len(expected_boundaries) == 343
              and {canonical(line) for line in actual_boundaries} == expected_boundaries)
        expected_polygons = expected_lanes + [{'type': 'Polygon', 'coordinates': [crossing['points']]} for crossing in prior['pedestrian_crossings']]
        check('all 219 lane fills and 27 crossings use actual prior-boundary coordinates',
              Counter(frozen(e['geometry_city']) for e in argo_elements if e['geometry_city']['type'] == 'Polygon')
              == Counter(frozen(g) for g in expected_polygons)
              and len(argo_elements) == 589)
        for name, context in contexts.items():
            extent = bounds([element['geometry_city'] for element in context['elements']])
            report.setdefault('source_bounds_xyxy', {})[name] = extent
            check(name + ' reference bounds equal independently measured source extents',
                  all(abs(value) < 100000 for value in extent)
                  and extent[2] > extent[0] and extent[3] > extent[1]
                  and context['bounds_xyxy'] == extent)
        guide = view['perception_guide']['datasets']
        with (raw_root / 'processed/objects.csv').open(encoding='utf-8-sig') as handle:
            objects = list(csv.DictReader(handle))
        actual_counts = Counter(row['category'] for row in objects)
        actual_unique = {category: len({row['instance_token'] for row in objects if row['category'] == category})
                         for category in actual_counts}
        check('nuScenes perception guide exactly reports 14 source categories, 2278 frame records and 154 instances',
              {row['code']: row['frame_annotations'] for row in guide['nuscenes']['object_classes']} == actual_counts
              and {row['code']: row['unique_instances'] for row in guide['nuscenes']['object_classes']} == actual_unique
              and len(actual_counts) == 14 and len(objects) == 2278
              and len({row['instance_token'] for row in objects}) == 154)
        detection_path = prior_paths[0].parent / 'tables/detections.jsonl'
        with detection_path.open() as handle:
            detections = [json.loads(line) for line in handle if line.strip()]
        actual_detection_counts = Counter(row['class_name'] for row in detections)
        check('Argoverse guide reports 4976 coarse vehicle boxes, without invented object classes or GT labels',
              actual_detection_counts == {'vehicle': 4976}
              and {row['code']: row['frame_annotations'] for row in guide['argoverse']['object_classes']} == actual_detection_counts
              and guide['argoverse']['object_classes'][0]['count_kind'] == 'per_view_2d_detections'
              and 'not unique objects or ground-truth annotations' in guide['argoverse']['object_classes'][0]['unit']['en'])
        return contexts

    def settle(page):
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def raster(page):
        return page.evaluate('''() => {
          const canvas=document.querySelector('#composite-map');
          const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let hash=2166136261, red=0, yellow=0, green=0;
          for(let i=0;i<pixels.length;i+=4){
            const [r,g,b]=pixels.slice(i,i+3);
            hash=Math.imul(hash ^ r,16777619); hash=Math.imul(hash ^ g,16777619); hash=Math.imul(hash ^ b,16777619);
            if(r>110 && r>g*1.32 && r>b*1.25) red++;
            if(r>130 && g>100 && b<r*.65 && Math.abs(r-g)<115) yellow++;
            if(g>100 && g>r*1.3 && g>b*1.15) green++;
          }
          return {hash:hash>>>0,red,yellow,green,width:canvas.width,height:canvas.height};
        }''')

    def fit_contains(page, geometries):
        extent = bounds(geometries)
        info = page.evaluate('''() => ({center:PIPELINE_STUDIO.map.state.center,
          scale:PIPELINE_STUDIO.map.state.scale,
          width:document.querySelector('#composite-map').getBoundingClientRect().width,
          height:document.querySelector('#composite-map').getBoundingClientRect().height})''')
        x0, y0, x1, y1 = extent
        cx, cy = info['center']
        scale = info['scale']
        return all(-1 <= x <= info['width'] + 1 and -1 <= y <= info['height'] + 1
                   for x, y in [((x0-cx)*scale+info['width']/2, (cy-y0)*scale+info['height']/2),
                                ((x1-cx)*scale+info['width']/2, (cy-y1)*scale+info['height']/2)])

    def source_pixel_hits(page, geometries, color):
        # Sample source line segments, then independently apply the documented
        # north-up metric transform. No renderer feature/hit coordinates are used.
        paths = [line for geometry in geometries for line in lines(geometry)]
        return page.evaluate('''({paths,color}) => {
          const canvas=document.querySelector('#composite-map'),box=canvas.getBoundingClientRect();
          const state=PIPELINE_STUDIO.map.state, ratio=canvas.width/box.width;
          const image=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let tested=0,hits=0;
          function colored(r,g,b){return color==='green' ? g>100&&g>r*1.3&&g>b*1.15 : r>130&&g>100&&b<r*.65&&Math.abs(r-g)<115;}
          for(const path of paths)for(let j=1;j<path.length;j++){
            const a=path[j-1],b=path[j],steps=Math.max(1,Math.ceil(Math.hypot(a[0]-b[0],a[1]-b[1])*state.scale/3));
            for(let n=0;n<=Math.min(steps,300);n++){
              const t=n/Math.min(steps,300),wx=a[0]+(b[0]-a[0])*t,wy=a[1]+(b[1]-a[1])*t;
              const x=Math.round(((wx-state.center[0])*state.scale+box.width/2)*ratio),y=Math.round(((state.center[1]-wy)*state.scale+box.height/2)*ratio);
              if(x<4||y<4||x>=canvas.width-4||y>=canvas.height-4)continue;
              tested++; let found=false;
              for(let dx=-3;dx<=3&&!found;dx++)for(let dy=-3;dy<=3;dy++){
                const k=((y+dy)*canvas.width+x+dx)*4;
                if(colored(image[k],image[k+1],image[k+2])){found=true;break;}
              }
              if(found)hits++;
            }
          }
          return {tested,hits,fraction:tested?hits/tested:0};
        }''', {'paths': paths, 'color': color})

    def swipe_pixels(page):
        """Compare whole canvas regions, rather than trusting UI percentages."""
        # Center the selected real barrier so the divider cuts actual overlay
        # geometry. The whole-scene view happens to place every event to its right.
        page.locator('#map-fit-selected').click()
        settle(page)
        def snapshot(name):
            page.evaluate('''name=>{
              const c=document.querySelector('#composite-map');
              window.__swipeCheck ||= {};
              window.__swipeCheck[name]=c.getContext('2d').getImageData(0,0,c.width,c.height);
            }''', name)

        view_before = page.evaluate('''() => ({center:[...PIPELINE_STUDIO.map.state.center],
          scale:PIPELINE_STUDIO.map.state.scale,point:PIPELINE_STUDIO.map.project([400,1200])})''')
        page.locator('#map-preset-after').click()
        settle(page)
        snapshot('updated')
        page.locator('#map-layer-state').uncheck()
        page.locator('#map-layer-structure').uncheck()
        settle(page)
        snapshot('base')
        for selector in ['#map-preset-before', '#map-preset-compare', '#map-preset-after']:
            page.locator(selector).click()
            settle(page)
            check('base geometry remains pixel-identical at ' + selector.removeprefix('#map-preset-'),
                  page.evaluate('''()=>{
                    const c=document.querySelector('#composite-map'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                    return a.every((value,i)=>value===__swipeCheck.base.data[i]);
                  }'''))
        page.locator('#map-layer-state').check()
        page.locator('#map-layer-structure').check()
        for selector, fraction in [('#map-preset-before', 1), ('#map-preset-compare', .5), ('#map-preset-after', 0)]:
            page.locator(selector).click()
            settle(page)
            comparison = page.evaluate('''fraction=>{
              const c=document.querySelector('#composite-map'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
              const cut=c.width*fraction; let checked=0,mismatch=0,originalMismatch=0,changed=0,maxChannelDelta=0;const samples=[];
              for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++){
                if(fraction>.0&&fraction<1&&Math.abs(x-cut)<2)continue;
                const expected=x<cut?__swipeCheck.base.data:__swipeCheck.updated.data;
                const i=(y*c.width+x)*4; checked++;
                if(a[i]!==expected[i]||a[i+1]!==expected[i+1]||a[i+2]!==expected[i+2]||a[i+3]!==expected[i+3]){
                  mismatch++;maxChannelDelta=Math.max(maxChannelDelta,...[0,1,2,3].map(k=>Math.abs(a[i+k]-expected[i+k])));
                  if(x<cut)originalMismatch++;
                  if(samples.length<12)samples.push({x,y,actual:[...a.slice(i,i+4)],expected:[...expected.slice(i,i+4)]});
                }
                if(a[i]!==__swipeCheck.base.data[i]||a[i+1]!==__swipeCheck.base.data[i+1]||a[i+2]!==__swipeCheck.base.data[i+2])changed++;
              }
              return {checked,mismatch,originalMismatch,changed,maxChannelDelta,samples};
            }''', fraction)
            report.setdefault('swipe_pixel_comparisons', {})[str(fraction)] = comparison
            # Chromium's clipped path rasterizer changes a few right-side
            # antialiased edge channels by 1–2/255; original pixels stay exact.
            pixel_match = comparison['mismatch'] == 0 if fraction in {0, 1} else (
                comparison['originalMismatch'] == 0 and comparison['maxChannelDelta'] <= 2
                and comparison['mismatch'] < comparison['checked'] * .001)
            check('swipe ' + str(int(fraction*100)) + '% preserves original pixels and clips updated source geometry',
                  pixel_match and comparison['checked'] > 10000
                  and (comparison['changed'] == 0 if fraction == 1 else comparison['changed'] > 50)
                  and page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == fraction)
            page.locator('.composite-panel').screenshot(path=str(root / ('swipe_nuscenes_' + selector.removeprefix('#map-preset-') + '.png')))
        check('divider positions preserve one shared metric transform for both maps',
              page.evaluate('''() => ({center:[...PIPELINE_STUDIO.map.state.center],
                scale:PIPELINE_STUDIO.map.state.scale,point:PIPELINE_STUDIO.map.project([400,1200])})''') == view_before)
        changes = report['swipe_pixel_comparisons']
        check('the half comparison actually hides part of the selected source geometry',
              0 < changes['0.5']['changed'] < changes['0']['changed'])

    def drag_divider(page, right, touch=False):
        page.locator('#map-preset-compare').click()
        page.locator('#map-divider').scroll_into_view_if_needed()
        settle(page)
        handle, canvas = page.locator('#map-divider').bounding_box(), page.locator('#composite-map').bounding_box()
        x, y = handle['x'] + handle['width']/2, handle['y'] + handle['height']/2
        destination = canvas['x'] + canvas['width'] + 7 if right else canvas['x'] - 7
        before = page.evaluate('({center:[...PIPELINE_STUDIO.map.state.center],scale:PIPELINE_STUDIO.map.state.scale})')
        if touch:
            session = page.context.new_cdp_session(page)
            session.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
            session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
            for step in range(1, 6):
                session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x+(destination-x)*step/5, 'y': y}]})
            session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
            session.detach()
        else:
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(destination, y, steps=7)
            page.mouse.up()
        settle(page)
        check(('touch' if touch else 'mouse') + ' dragging divider to ' + ('right shows all original' if right else 'left shows all updated'),
              page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == (1 if right else 0)
              and page.evaluate('({center:[...PIPELINE_STUDIO.map.state.center],scale:PIPELINE_STUDIO.map.state.scale})') == before)
        end_handle = page.locator('#map-divider').bounding_box()
        check(('touch' if touch else 'mouse') + ' endpoint keeps the entire divider grip reachable on screen ' + ('right' if right else 'left'),
              end_handle['x'] >= canvas['x'] - 1
              and end_handle['x'] + end_handle['width'] <= canvas['x'] + canvas['width'] + 1)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1500, 'height': 1150})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: requests.append([response.url, response.status]) if response.status >= 400 else None)
            page.goto(base + 'en/index.html')
            page.wait_for_function('window.PIPELINE_STUDIO && PIPELINE_STUDIO.map')
            settle(page)
            view = page.evaluate('PIPELINE_STUDIO.data')
            immutable = page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)')
            contexts = verify_sources(view)
            check('the prominent map is visible before the detailed evidence panels',
                  page.locator('#composite-map').is_visible()
                  and page.locator('#composite-map').bounding_box()['y'] < 900)
            legend_box = page.locator('#map-legend').bounding_box()
            check('the yellow, red and green meaning is visible above the canvas in the first viewport',
                  legend_box['y'] + legend_box['height'] <= page.locator('#composite-map').bounding_box()['y'] + 1
                  and legend_box['y'] + legend_box['height'] < 1150
                  and all(word in page.locator('#map-legend').inner_text().lower() for word in ['state', 'confirmed', 'unconfirmed']))
            check('first view shows the comparison while technical analysis starts collapsed',
                  page.locator('#advanced-analysis').get_attribute('open') is None
                  and page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == .5)
            check('selected map card identifies a barrier in plain language rather than only its source ID',
                  'barrier' in page.locator('#map-selected-title').inner_text().lower())
            check('layer options start collapsed to keep the comparison readable', page.locator('.map-options').get_attribute('open') is None)
            page.locator('.map-options > summary').click()
            fixture = page.evaluate('''()=>{
              const map=PIPELINE_STUDIO.map;
              const base={event_id:'RENDER_TEST_FIXTURE_ONLY',map_layer:{predicted_layer:'base_map_structure'},
                reliability:{status:'supported_conflict'},decision:{classification_applied:true,base_map_write_eligible:false,write_gate:{eligible:false,map_patch_executed:false}}};
              const check=(decision,layer='base_map_structure')=>map.isConfirmedStructure({...base,map_layer:{predicted_layer:layer},decision});
              const cases=[map.isConfirmedStructure(base),map.isConfirmedStructure({}),
                check({...base.decision,base_map_write_eligible:true}),
                check({...base.decision,write_gate:{eligible:true,map_patch_executed:false}}),
                check({...base.decision,base_map_write_eligible:true,write_gate:{eligible:true,map_patch_executed:false}}),
                check({...base.decision,write_gate:{map_patch_executed:true}}),
                check({...base.decision,write_gate:{map_patch_executed:true}},'map_state_event')];
              const canvas=document.createElement('canvas');canvas.width=canvas.height=20;
              const ctx=canvas.getContext('2d'),style=map.style({layer:'structure',confirmed:true,supported:false,event_id:base.event_id});
              ctx.strokeStyle=style.color;ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(2,10);ctx.lineTo(18,10);ctx.stroke();
              return {cases,color:style.color,pixel:[...ctx.getImageData(10,10,1,1).data],fixture_scope:'detached render test only; no source-data mutation'};
            }''')
            report['confirmed_structure_render_fixture'] = fixture
            check('red confirmation needs an actual patch or both write gates, never support or class assignment alone',
                  fixture['cases'] == [False, False, False, False, True, True, False])
            check('an explicitly synthetic detached render fixture uses red for a confirmed structure',
                  fixture['pixel'] == [255, 92, 101, 255])
            check('perception details stay collapsed until requested', page.locator('#perception-guide').get_attribute('open') is None)
            page.locator('#perception-guide > summary').click()
            check('English perception details render all 14 actual source labels and their annotation provenance',
                  all(row['code'] in page.locator('#perception-guide-content').inner_text()
                      for row in view['perception_guide']['datasets']['nuscenes']['object_classes'])
                  and 'ground-truth annotations' in page.locator('#perception-guide-content').inner_text())
            page.locator('#perception-guide > summary').click()
            nu = source['datasets']['nuscenes']
            state_geometries = [feature['geometry'] for feature in nu['state_event_layer']['features']]
            structure = [event for event in nu['events'] if event['map_layer']['predicted_layer'] == 'base_map_structure']
            check('nuScenes opens all three layers with 157 base features, 23 states and 6 structure hypotheses',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn') == {'base': 157, 'state': 23, 'structure': 6})
            check('all state and structural overlay geometries preserve their frozen prediction values',
                  view['datasets']['nuscenes']['state_event_layer'] == nu['state_event_layer']
                  and all(event['geometry_city'] == original['geometry_city']
                          for event, original in zip(view['datasets']['nuscenes']['events'], nu['events'])))
            ld004 = next(event for event in structure if event['event_id'] == 'S0980-LD-004')
            check('LD004 local support is saved separately from its unresolved structural operation',
                  nu['summary']['reliability_counts'] == {'supported_conflict': 24, 'insufficient_geometry': 5}
                  and ld004['reliability']['status'] == 'supported_conflict'
                  and ld004['structural_hypothesis']['operation'] == 'unresolved_parallel_marking_or_insertion'
                  and ld004['structural_hypothesis']['confirmed'] is False
                  and ld004['decision']['base_map_write_eligible'] is False)
            for name in ['nuscenes', 'argoverse']:
                page.locator('#dataset').select_option(name)
                settle(page)
                rendered_sources = page.evaluate('PIPELINE_STUDIO.map.features(false)')
                dataset = source['datasets'][name]
                expected = ([('base', e['geometry_city']) for e in contexts[name]['elements']]
                            + [('state', f['geometry']) for f in dataset['state_event_layer']['features']]
                            + [('structure', e['geometry_city']) for e in dataset['events']
                               if e['map_layer']['predicted_layer'] == 'base_map_structure'])
                key = lambda layer, geometry: (layer, frozen(geometry))
                check(name + ' drawable geometry is exactly the reference, state export and predicted structure; no GT geometry',
                      Counter(key(f['layer'], f['geometry_city']) for f in rendered_sources)
                      == Counter(key(layer, geometry) for layer, geometry in expected)
                      and all(f['source'] in {'map_context', 'state_event_layer', 'event'} for f in rendered_sources))
            page.locator('#dataset').select_option('nuscenes')
            settle(page)
            page.locator('#map-preset-after').click()
            settle(page)
            initial = raster(page)
            check('real nuScenes pixels use yellow states and green unconfirmed structures, with no red confirmation',
                  initial['yellow'] > 15 and initial['green'] > 15 and initial['red'] == 0
                  and not any(feature.get('confirmed') for feature in page.evaluate('PIPELINE_STUDIO.map.features(false)')))
            page.locator('#map-fit-all').click()
            settle(page)
            check('fit all contains actual source extents without confusing the two city coordinate systems',
                  fit_contains(page, [element['geometry_city'] for element in contexts['nuscenes']['elements']] + state_geometries + [event['geometry_city'] for event in structure]))
            swipe_pixels(page)
            page.locator('#map-fit-all').click()
            settle(page)
            drag_divider(page, right=True)
            drag_divider(page, right=False)
            page.locator('#map-divider-range').focus()
            page.keyboard.press('End')
            settle(page)
            check('range keyboard End shows all original with no overlay pixels or hit targets',
                  page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == 1
                  and raster(page)['yellow'] == raster(page)['green'] == raster(page)['red'] == 0
                  and page.evaluate('PIPELINE_STUDIO.map.state.hits.length') == 0)
            page.keyboard.press('Home')
            settle(page)
            check('range keyboard Home restores all updated overlays',
                  page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == 0
                  and raster(page)['yellow'] > 15 and raster(page)['green'] > 15)
            for layer in ['base', 'state', 'structure']:
                before = raster(page)
                page.locator('#map-layer-' + layer).uncheck()
                settle(page)
                check(layer + ' toggle changes actual map pixels and hides its rendered features',
                      raster(page)['hash'] != before['hash']
                      and page.evaluate('name=>PIPELINE_STUDIO.map.state.drawn[name]', layer) == 0)
                page.locator('#map-layer-' + layer).check()
                settle(page)
            check('layer switches leave all predictions and histories unchanged', page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)') == immutable)
            page.locator('#map-layer-structure').uncheck()
            page.locator('#map-fit-selected').click()
            settle(page)
            yellow_hits = source_pixel_hits(page, state_geometries, 'yellow')
            report['yellow_state_source_coordinate_pixels'] = yellow_hits
            check('yellow pixels coincide with projected last-observed state source geometry', yellow_hits['hits'] > 10 and yellow_hits['fraction'] > .15)
            page.locator('#map-layer-state').uncheck()
            page.locator('#map-layer-structure').check()
            page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', structure[0]['event_id'])
            page.locator('#map-fit-selected').click()
            settle(page)
            green_hits = source_pixel_hits(page, [event['geometry_city'] for event in structure], 'green')
            report['green_structure_source_coordinate_pixels'] = green_hits
            check('green pixels coincide with projected unconfirmed structural source geometry', green_hits['hits'] > 10 and green_hits['fraction'] > .15)
            old_scale = page.evaluate('PIPELINE_STUDIO.map.state.scale')
            old_raster = raster(page)
            page.locator('#map-zoom-in').click()
            settle(page)
            check('zoom increases the metric scale and changes the rendered view', page.evaluate('PIPELINE_STUDIO.map.state.scale') > old_scale and raster(page)['hash'] != old_raster['hash'])
            page.locator('#map-zoom-out').click()
            settle(page)
            check('zoom out reverses the scale change', abs(page.evaluate('PIPELINE_STUDIO.map.state.scale') - old_scale) < old_scale * .001)
            canvas = page.locator('#composite-map')
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            old_center = page.evaluate('PIPELINE_STUDIO.map.state.center.slice()')
            old_split = page.evaluate('PIPELINE_STUDIO.map.state.splitFraction')
            x, y = box['x'] + box['width'] * .4, box['y'] + box['height'] * .4
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 45, y + 30, steps=5)
            page.mouse.up()
            settle(page)
            center = page.evaluate('PIPELINE_STUDIO.map.state.center')
            check('drag pans in metric coordinates with the expected north-up direction',
                  abs((center[0]-old_center[0])*old_scale + 45) < 2
                  and abs((center[1]-old_center[1])*old_scale - 30) < 2
                  and page.evaluate('PIPELINE_STUDIO.map.state.splitFraction') == old_split)
            page.locator('#map-layer-state').check()
            page.locator('#map-fit-all').click()
            settle(page)
            candidate = page.evaluate('''() => {
              const m=PIPELINE_STUDIO.map, box=document.querySelector('#composite-map').getBoundingClientRect();
              return m.state.hits.find(h => h.event_id!==PIPELINE_STUDIO.state.eventId
                && h.screen_xy[0]>12 && h.screen_xy[1]>12
                && h.screen_xy[0]<box.width-12 && h.screen_xy[1]<box.height-12
                && m.state.hits.every(other=>other.event_id===h.event_id
                  || Math.hypot(other.screen_xy[0]-h.screen_xy[0],other.screen_xy[1]-h.screen_xy[1])>15));
            }''')
            assert candidate is not None, 'Expected a separately clickable source-event marker'
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            selected_before_click = page.evaluate('PIPELINE_STUDIO.state.eventId')
            page.evaluate('fraction=>PIPELINE_STUDIO.map.setSplit(fraction)', min(.95, max(.5, candidate['screen_xy'][0]/box['width']+.05)))
            settle(page)
            page.mouse.click(box['x'] + candidate['screen_xy'][0], box['y'] + candidate['screen_xy'][1])
            settle(page)
            check('an overlay hidden behind the original side cannot be selected through the swipe clip',
                  page.evaluate('PIPELINE_STUDIO.state.eventId') == selected_before_click)
            page.locator('#map-preset-after').click()
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            page.mouse.click(box['x'] + candidate['screen_xy'][0], box['y'] + candidate['screen_xy'][1])
            settle(page)
            report['map_click_regression'] = {'expected': candidate, 'selected': page.evaluate('PIPELINE_STUDIO.state.eventId'),
                'canvas_box': box, 'split': page.evaluate('PIPELINE_STUDIO.map.state.splitFraction'),
                'element_at_click': page.evaluate('''({x,y})=>document.elementFromPoint(x,y)?.outerHTML.slice(0,500)''',
                    {'x': box['x']+candidate['screen_xy'][0], 'y': box['y']+candidate['screen_xy'][1]})}
            check('clicking a visible map event selects the matching source event in its map card',
                  page.evaluate('PIPELINE_STUDIO.state.eventId') == candidate['event_id']
                  and page.locator('#map-selected code').inner_text() == candidate['event_id'])
            page.locator('#map-view-evidence').click()
            check('opening evidence after a map click shows the same selected source event',
                  page.locator('#selected-id').is_visible()
                  and page.locator('#selected-id').inner_text() == candidate['event_id'])
            page.locator('#advanced-analysis > summary').click()
            page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', ld004['event_id'])
            page.locator('#map-fit-selected').click()
            page.locator('#map-preset-after').click()
            settle(page)
            ld_pixels = source_pixel_hits(page, [ld004['geometry_city']], 'green')
            report['ld004_source_coordinate_pixels'] = ld_pixels
            check('newly supported LD004 still renders its source curve as green, never confirmed red',
                  ld_pixels['hits'] > 10 and ld_pixels['fraction'] > .15
                  and raster(page)['red'] == 0
                  and page.locator('#map-count-confirmed').inner_text() == '0'
                  and 'unconfirmed' in page.locator('#map-selected-title').inner_text().lower())
            page.locator('#map-view-evidence').click()
            check('LD004 map selection opens the exact-window reference counterevidence',
                  page.locator('#same-window-evidence').is_visible()
                  and page.locator('#same-window-evidence tbody tr').count() == 6
                  and 'does not support a claim that the old marking disappeared or moved' in page.locator('#same-window-evidence').inner_text())
            page.locator('#advanced-analysis > summary').click()
            page.locator('#map-preset-compare').click()
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_nuscenes_en.png'))

            page.locator('#dataset').select_option('argoverse')
            settle(page)
            check('Argoverse opens 589 prior features, no states and all 71 unconfirmed structural candidates',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn') == {'base': 589, 'state': 0, 'structure': 71}
                  and page.locator('#map-structure-scope').input_value() == 'all'
                  and page.locator('#map-count-confirmed').inner_text() == '0')
            page.locator('#perception-guide > summary').click()
            check('Argoverse perception view distinguishes vehicle detections from unique objects and ground truth',
                  '4,976' in page.locator('#perception-guide-content').inner_text()
                  and 'not unique objects or ground-truth annotations' in page.locator('#perception-guide-content').inner_text()
                  and 'All curve marking_pattern values are unknown' in page.locator('#perception-guide-content').inner_text())
            page.locator('#perception-guide > summary').click()
            check('the selected supported Argoverse claim has a visible false-positive warning',
                  page.locator('#map-note').is_visible()
                  and 'false positive' in page.locator('#map-note').inner_text().lower()
                  and '0 structural patches applied' in page.locator('#map-status').inner_text())
            page.locator('#map-structure-scope').select_option('supported')
            settle(page)
            check('local-support scope isolates one candidate without upgrading it to confirmed structure',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn.structure') == 1
                  and page.locator('#map-count-confirmed').inner_text() == '0')
            page.locator('#map-preset-after').click()
            settle(page)
            check('Argoverse supported false positive remains visibly green with no red confirmed structure',
                  raster(page)['green'] > 15 and raster(page)['red'] == 0
                  and page.evaluate('PIPELINE_STUDIO.current().decision.classification_applied') is True
                  and page.evaluate('PIPELINE_STUDIO.current().reliability.status') == 'supported_conflict'
                  and page.evaluate('PIPELINE_STUDIO.map.isConfirmedStructure(PIPELINE_STUDIO.current())') is False)
            page.locator('#map-view-evidence').click()
            check('the map evidence action opens the detailed source analysis and selected evaluation',
                  page.locator('#advanced-analysis').get_attribute('open') is not None
                  and page.locator('#selected-evaluation').is_visible()
                  and 'false positive' in page.locator('#selected-evaluation').inner_text().lower())
            page.locator('#advanced-analysis > summary').click()
            page.locator('#map-fit-all').click()
            settle(page)
            check('Argoverse fit uses its own actual M2 prior bounds',
                  fit_contains(page, [element['geometry_city'] for element in contexts['argoverse']['elements']]))
            page.locator('#map-structure-scope').select_option('all')
            settle(page)
            check('all-hypotheses scope reveals all 71 structural claims without relabeling the 70 insufficient cases',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn.structure') == 71
                  and sum(event['reliability']['status'] == 'insufficient_geometry' for event in view['datasets']['argoverse']['events']) == 70
                  and raster(page)['red'] == 0
                  and not any(feature.get('confirmed') for feature in page.evaluate('PIPELINE_STUDIO.map.features(false)')))
            paint_candidates = [event for event in source['datasets']['argoverse']['events']
                                if event.get('prior_marking_type') == 'NONE']
            check('all 64 unpainted-reference hypotheses have explicit paint-presence semantics',
                  len(paint_candidates) == 64
                  and all(event['structural_hypothesis']['target_property'] == 'paint_presence'
                          and event['structural_hypothesis']['confirmed'] is False for event in paint_candidates))
            prior_selection = page.evaluate('PIPELINE_STUDIO.state.eventId')
            paint = next(event for event in paint_candidates if event['observation_evidence']['geometry_measurement']['status'] == 'available')
            page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', paint['event_id'])
            page.locator('#map-fit-selected').click()
            page.locator('#map-preset-after').click()
            settle(page)
            paint_pixels = source_pixel_hits(page, [paint['geometry_city']], 'green')
            report['paint_presence_source_coordinate_pixels'] = paint_pixels
            check('paint-presence map card and green source geometry do not claim a confirmed old-line displacement',
                  page.locator('#map-selected-title').inner_text() == 'Paint-presence hypothesis'
                  and paint_pixels['hits'] > 10 and paint_pixels['fraction'] > .15
                  and raster(page)['red'] == 0
                  and page.evaluate('PIPELINE_STUDIO.map.isConfirmedStructure(PIPELINE_STUDIO.current())') is False)
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_paint_presence_en.png'))
            page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', prior_selection)
            page.locator('#map-fit-all').click()
            check('cross-dataset interactions preserve data and do not turn overlays into base-map edits',
                  page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)') == immutable
                  and all(dataset['summary']['base_map_updates'] == 0 for dataset in source['datasets'].values()))
            page.locator('#map-preset-compare').click()
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_argoverse_en.png'))
            selected = page.evaluate('PIPELINE_STUDIO.state.eventId')
            page.locator('#language-link').click()
            page.wait_for_url('**/zh/index.html?*')
            settle(page)
            check('Chinese map keeps the same dataset, selected source event and exact data',
                  page.evaluate('PIPELINE_STUDIO.state.dataset') == 'argoverse'
                  and page.evaluate('PIPELINE_STUDIO.state.eventId') == selected
                  and page.evaluate('PIPELINE_STUDIO.data') == view)
            check('Chinese selected card gives the marking meaning and unconfirmed status',
                  '标线' in page.locator('#map-selected-title').inner_text()
                  and '未确认' in page.locator('#map-selected-status').inner_text())
            page.locator('.map-options > summary').click()
            check('Chinese map controls describe state and structure separately',
                  '状态' in page.locator('#map-layer-state').locator('..').inner_text()
                  and '结构' in page.locator('#map-layer-structure').locator('..').inner_text())
            page.set_viewport_size({'width': 390, 'height': 844})
            settle(page)
            check('mobile map and controls fit without horizontal document overflow',
                  page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                  and page.locator('#composite-map').bounding_box()['width'] > 250)
            page.locator('#map-fit-selected').click()
            settle(page)
            check('mobile fit-selected retains visible selected-source geometry',
                  fit_contains(page, [page.evaluate('PIPELINE_STUDIO.current().geometry_city')]))
            drag_divider(page, right=True, touch=True)
            drag_divider(page, right=False, touch=True)
            page.locator('#map-preset-compare').click()
            check('mobile divider gestures preserve all immutable source predictions', page.evaluate('PIPELINE_STUDIO.data') == view)
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_mobile_zh.png'))
            check('all assets load and all map interactions have no uncaught browser errors', not errors and not requests)
            browser.close()
        report['status'] = 'PASS'
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        report['count'] = len(checks)
        (root / 'composite_map_checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        server.shutdown()
    print('PASS ' + str(len(checks)) + ' source-geometry composite-map checks')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--raw-root', type=Path, default=REPO / 'AIE1903 原始资料')
    args = parser.parse_args()
    run(args.directory, args.raw_root.resolve())
