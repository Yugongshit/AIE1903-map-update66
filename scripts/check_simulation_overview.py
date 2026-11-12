"""Verify fixed full-scene changes in review and actual MetaDrive playback.

Run the local platform first with the simulation interpreter. This developer
check needs Playwright/Chromium and exercises real rendered frames, frozen
physical objects, backward seeks and the separate current-time Planning export.
"""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import shutil
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def event_ids(events):
    return sorted(row.get('properties', {}).get('event_id', row.get('event_id', row.get('id'))) for row in events)


def fixed_annotations(result):
    # Native object names change when a scene resets. Source identities and
    # source geometry must not change with those process-local bindings.
    keys = ('instance_id', 'event_ids', 'status', 'runtime_paths', 'matched_track_ids', 'source_height_m')
    return sorted(({key: row.get(key) for key in keys} for row in result['state_objects']),
                  key=lambda row: row['instance_id'])


def fixed_poses(result):
    poses = result['persistent_obstacles_runtime']
    def rounded(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {key: rounded(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rounded(item) for item in value]
        return value
    return rounded(poses)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    args = parser.parse_args()
    if not args.chromium:
        parser.error('Chromium not found; pass --chromium')
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'scene': 'nuscenes-scene0980', 'checks': [], 'errors': [], 'frames': []}

    def check(name, condition, details=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'details': details})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition:
            raise AssertionError(name)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000}, accept_downloads=True)
        page.set_default_timeout(180000)
        page.on('pageerror', lambda error: report['errors'].append(str(error)))

        def seek(step, engine=False, version='updated'):
            page.evaluate('(step)=>MAP_PLATFORM.seek(step)', step)
            if engine:
                wait_engine(step, version)
            else:
                page.wait_for_function('(step)=>MAP_PLATFORM.state.step===step&&MAP_PLATFORM.state.snapshot?.step===step', arg=step)

        def wait_engine(step=None, version='updated'):
            page.wait_for_function("""({step,version})=>{
              const s=MAP_PLATFORM.state,d=s.engineDisplayedFrame,i=document.getElementById('engine-frame');
              return !s.engineCommandBusy&&!s.engineFrameError&&d&&d.version===version&&
                (step==null||d.step===step)&&s.step===d.step&&s.snapshot?.step===d.step&&
                i.complete&&i.naturalWidth>0&&!i.hidden&&Number(i.dataset.step)===d.step&&
                i.dataset.frameId===d.frame_id&&d.result.map_features_sha256===s.snapshot.map_sha256;
            }""", arg={'step': step, 'version': version})

        def result():
            return page.evaluate('MAP_PLATFORM.state.engineDisplayedFrame.result')

        def planning():
            with page.expect_download() as download_info:
                page.locator('#export-planning').click()
            return json.loads(Path(download_info.value.path()).read_text())

        def observe_native(label):
            row = result()
            report['frames'].append({'label': label, 'step': row['step'],
                'source_timestamp_ns': row['source_timestamp_ns'], 'frame_id': row['frame_id'],
                'display_scope': row.get('display_scope'), 'event_count': len(row['state_events']),
                'instance_count': len(row['state_objects']), 'persistent_obstacles': row['persistent_obstacle_count'],
                'display_state_sha256': row.get('display_state_sha256'),
                'annotation_sha256': digest(fixed_annotations(row)), 'physical_pose_sha256': digest(fixed_poses(row)),
                'ego_position_scenario_m': row['position_scenario_m'], 'map_sha256': row['map_features_sha256']})
            return row

        try:
            with page.expect_response(lambda r: urlparse(r.url).path == '/simulation_platform.js') as script:
                page.goto(args.url.rstrip('/')+'/?scene=nuscenes-scene0980', wait_until='networkidle')
            report['frontend_sha256'] = sha256(script.value.body()).hexdigest()
            page.wait_for_function('MAP_PLATFORM.state.snapshot?.step===0&&MAP_PLATFORM.state.scene?.state_overview')
            payload = page.evaluate('MAP_PLATFORM.state.scene')
            overview = payload['state_overview']
            overview_hash = digest(overview)
            static_hash = digest(payload['static_overview_actors'])
            overview_ids = event_ids(overview['events'])
            current = page.evaluate('MAP_PLATFORM.state.snapshot')
            late_ids = sorted(set(overview_ids)-set(event_ids(current['states']['events'])))
            check('frame zero already contains all 23 frozen events and 74 source instances',
                  overview['scope']=='full_scene_overview' and overview['frozen'] is True
                  and len(overview['events'])==23 and len(overview['objects'])==74)
            check('all 103 static source objects are available in the review from the beginning',
                  len(payload['static_overview_actors'])==103
                  and len({a['id'] for a in payload['static_overview_actors']})==103)
            check('the visible event list includes changes observed later in the recording',
                  page.locator('#change-list .change-item').count()==23 and bool(late_ids))
            late_id = late_ids[-1]
            page.evaluate('(id)=>MAP_PLATFORM.selectItem("state",id)', late_id)
            check('a later-observed event can be selected in review at frame zero',
                  page.evaluate('MAP_PLATFORM.state.selected.id')==late_id
                  and page.locator('#selection-details').is_visible())
            original_planning = planning()
            check('Planning at zero retains 7 current events and 20 observed instances',
                  original_planning['step']==0 and len(original_planning['map_state_snapshot']['events'])==7
                  and len(original_planning['map_state_snapshot']['objects'])==20
                  and late_id not in event_ids(original_planning['map_state_snapshot']['events']))
            check('Planning excludes the frozen overview, future tracks and recorded ego route',
                  'state_overview' not in original_planning and 'static_overview_actors' not in original_planning
                  and 'tracks' not in original_planning and original_planning['route'] is None
                  and len(original_planning['persistent_obstacles'])==29)
            report['planning_at_zero'] = {'events': 7, 'instances': 20,
                'persistent_obstacles': len(original_planning['persistent_obstacles']), 'late_event_excluded': late_id}
            page.screenshot(path=str(args.out/'review-all-at-zero.png'), full_page=True)
            for step in (137, 5):
                seek(step)
                check('review overview and static poses stay fixed at frame '+str(step),
                      digest(page.evaluate('MAP_PLATFORM.state.scene.state_overview'))==overview_hash
                      and digest(page.evaluate('MAP_PLATFORM.state.scene.static_overview_actors'))==static_hash
                      and page.locator('#change-list .change-item').count()==23)
            seek(0)
            page.locator('#start-engine').click()
            wait_engine(0)
            first = observe_native('start')
            check('real MetaDrive opens with every fixed state annotation and static model',
                  first['live_engine'] is True and first['display_scope']=='full_scene_overview'
                  and len(first['state_events'])==23 and len(first['state_objects'])==74
                  and first['persistent_obstacle_count']==103 and not first['missing_expected_obstacles'])
            check('every displayed state is explicitly a scene overview',
                  all(o['status']=='scene_overview' for o in first['state_objects'])
                  and all(e['state']=='scene_overview' for e in first['state_events']))
            page.evaluate('(id)=>MAP_PLATFORM.selectItem("state",id)', late_id)
            wait_engine(0)
            focused = result()
            check('a later-observed event can focus real MetaDrive at frame zero',
                  focused['selected_event_id']==late_id and focused['camera']['target']=='selection')
            page.screenshot(path=str(args.out/'engine-all-at-zero.png'), full_page=True)
            for step in (137, 5):
                seek(step, engine=True)
                moved = observe_native('seek-'+str(step))
                check('native annotation identities and geometry stay frozen at frame '+str(step),
                      fixed_annotations(moved)==fixed_annotations(first)
                      and moved['display_state_sha256']==first['display_state_sha256'])
                check('all 103 physical obstacle identities and poses stay fixed at frame '+str(step),
                      moved['persistent_obstacle_ids']==first['persistent_obstacle_ids']
                      and fixed_poses(moved)==fixed_poses(first) and not moved['missing_expected_obstacles'])
                check('ego and source clock continue to replay at frame '+str(step),
                      moved['position_scenario_m']!=first['position_scenario_m']
                      and moved['source_timestamp_ns']==page.evaluate('MAP_PLATFORM.state.snapshot.source_timestamp_ns'))
            page.locator('#map-version').select_option('original')
            wait_engine(5, 'original')
            original = observe_native('original-map')
            check('road version reload keeps the complete frozen scene and selected time',
                  original['step']==5 and fixed_annotations(original)==fixed_annotations(first)
                  and fixed_poses(original)==fixed_poses(first))
            seek(0, engine=True, version='original')
            after = planning()
            check('native full-scene preview does not contaminate current-time Planning export',
                  after['map_state_snapshot']==original_planning['map_state_snapshot']
                  and after['persistent_obstacles']==original_planning['persistent_obstacles']
                  and after['current_objects']==original_planning['current_objects'])
            page.screenshot(path=str(args.out/'engine-return-zero.png'), full_page=True)
            check('no uncaught browser JavaScript errors', not report['errors'], report['errors'])
            report['passed'] = True
        except Exception as error:
            report['passed'] = False
            report['exception'] = repr(error)
            try:
                page.screenshot(path=str(args.out/'failure.png'), full_page=True)
            except Exception:
                pass
            raise
        finally:
            try:
                page.evaluate('window.MAP_PLATFORM?.stopPlayback()')
            except Exception:
                pass
            (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            browser.close()


if __name__ == '__main__':
    main()
