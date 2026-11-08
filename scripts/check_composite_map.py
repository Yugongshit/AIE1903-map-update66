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
          let hash=2166136261, red=0, yellow=0;
          for(let i=0;i<pixels.length;i+=4){
            const [r,g,b]=pixels.slice(i,i+3);
            hash=Math.imul(hash ^ r,16777619); hash=Math.imul(hash ^ g,16777619); hash=Math.imul(hash ^ b,16777619);
            if(r>110 && r>g*1.32 && r>b*1.25) red++;
            if(r>130 && g>100 && b<r*.65 && Math.abs(r-g)<115) yellow++;
          }
          return {hash:hash>>>0,red,yellow,width:canvas.width,height:canvas.height};
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
          function colored(r,g,b){return color==='red' ? r>110&&r>g*1.32&&r>b*1.25 : r>130&&g>100&&b<r*.65&&Math.abs(r-g)<115;}
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
            initial = raster(page)
            check('the composite image actually contains red states and yellow structure geometry',
                  initial['red'] > 15 and initial['yellow'] > 15)
            page.locator('#map-fit-all').click()
            settle(page)
            check('fit all contains actual source extents without confusing the two city coordinate systems',
                  fit_contains(page, [element['geometry_city'] for element in contexts['nuscenes']['elements']] + state_geometries + [event['geometry_city'] for event in structure]))
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
            red_hits = source_pixel_hits(page, state_geometries, 'red')
            report['red_source_coordinate_pixels'] = red_hits
            check('red pixels coincide with projected last-observed state source geometry', red_hits['hits'] > 10 and red_hits['fraction'] > .15)
            page.locator('#map-layer-state').uncheck()
            page.locator('#map-layer-structure').check()
            page.evaluate('(id)=>PIPELINE_STUDIO.pickEvent(id)', structure[0]['event_id'])
            page.locator('#map-fit-selected').click()
            settle(page)
            yellow_hits = source_pixel_hits(page, [event['geometry_city'] for event in structure], 'yellow')
            report['yellow_source_coordinate_pixels'] = yellow_hits
            check('yellow pixels coincide with projected structural source geometry', yellow_hits['hits'] > 10 and yellow_hits['fraction'] > .15)
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
            x, y = box['x'] + box['width'] * .4, box['y'] + box['height'] * .4
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 45, y + 30, steps=5)
            page.mouse.up()
            settle(page)
            center = page.evaluate('PIPELINE_STUDIO.map.state.center')
            check('drag pans in metric coordinates with the expected north-up direction',
                  abs((center[0]-old_center[0])*old_scale + 45) < 2
                  and abs((center[1]-old_center[1])*old_scale - 30) < 2)
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
            page.mouse.click(box['x'] + candidate['screen_xy'][0], box['y'] + candidate['screen_xy'][1])
            settle(page)
            check('clicking a map event selects the matching source event in the evidence panel',
                  page.evaluate('PIPELINE_STUDIO.state.eventId') == candidate['event_id']
                  and page.locator('#selected-id').inner_text() == candidate['event_id'])
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_nuscenes_en.png'))

            page.locator('#dataset').select_option('argoverse')
            settle(page)
            check('Argoverse opens 589 prior features, no states and one supported structural claim',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn') == {'base': 589, 'state': 0, 'structure': 1}
                  and page.locator('#map-structure-scope').input_value() == 'supported')
            page.locator('#perception-guide > summary').click()
            check('Argoverse perception view distinguishes vehicle detections from unique objects and ground truth',
                  '4,976' in page.locator('#perception-guide-content').inner_text()
                  and 'not unique objects or ground-truth annotations' in page.locator('#perception-guide-content').inner_text()
                  and 'All curve marking_pattern values are unknown' in page.locator('#perception-guide-content').inner_text())
            page.locator('#perception-guide > summary').click()
            check('the selected supported Argoverse claim has a visible false-positive warning',
                  page.locator('#selected-evaluation').is_visible()
                  and 'false positive' in page.locator('#selected-evaluation').inner_text().lower()
                  and 'false positive' in page.locator('#map-note').inner_text().lower()
                  and '0 applied structural edits' in page.locator('#map-status').inner_text())
            page.locator('#map-fit-all').click()
            settle(page)
            check('Argoverse fit uses its own actual M2 prior bounds',
                  fit_contains(page, [element['geometry_city'] for element in contexts['argoverse']['elements']]))
            page.locator('#map-structure-scope').select_option('all')
            settle(page)
            check('all-hypotheses scope reveals all 71 structural claims without relabeling the 70 insufficient cases',
                  page.evaluate('PIPELINE_STUDIO.map.state.drawn.structure') == 71
                  and sum(event['reliability']['status'] == 'insufficient_geometry' for event in view['datasets']['argoverse']['events']) == 70)
            check('cross-dataset interactions preserve data and do not turn overlays into base-map edits',
                  page.evaluate('JSON.stringify(PIPELINE_STUDIO.data)') == immutable
                  and all(dataset['summary']['base_map_updates'] == 0 for dataset in source['datasets'].values()))
            page.locator('.composite-panel').screenshot(path=str(root / 'composite_argoverse_en.png'))
            selected = page.evaluate('PIPELINE_STUDIO.state.eventId')
            page.locator('#language-link').click()
            page.wait_for_url('**/zh/index.html?*')
            settle(page)
            check('Chinese map keeps the same dataset, selected source event and exact data',
                  page.evaluate('PIPELINE_STUDIO.state.dataset') == 'argoverse'
                  and page.evaluate('PIPELINE_STUDIO.state.eventId') == selected
                  and page.evaluate('PIPELINE_STUDIO.data') == view)
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
