"""Native HTTP/browser acceptance using explicitly labelled development fixtures."""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    key = secrets.token_urlsafe(32)
    env = dict(os.environ, RECORDED_SERVICE_KEY=key)
    server_log = (args.out/'server.log').open('x')
    command = [sys.executable,str(ROOT/'scripts/run_recorded_service.py'),'--database',str(args.database),
               '--out',str(args.out/'server'),'--port',str(port),'--action-timeout','120']
    process = subprocess.Popen(command,env=env,cwd=ROOT,stdout=server_log,stderr=server_log)
    base = f'http://127.0.0.1:{port}'
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print('PASS '+name, flush=True)

    def call(path, body=None, token=key, status=200):
        request = urllib.request.Request(base+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                code, data = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            code, data = error.code, json.load(error)
        expected = status if isinstance(status, tuple) else (status,)
        if code not in expected:
            raise AssertionError((path, code, data))
        return data

    def bound(state):
        return dict(session_id=state['session_id'],map_version=state['map_version'],
                    state_version=state['state_version'],observation_step=state['step'])

    def proposal(state, actor, identity):
        row=state['objects'][actor]
        return dict(actor_id=actor,plan_id=identity,coordinate_frame=state['coordinate_frame'],
            points=[dict(time_offset_s=t,position_m=[p+v*t for p,v in zip(row['position_m'],row['velocity_m_s'])],
                         heading_rad=row['heading_rad'],speed_m_s=math.hypot(*row['velocity_m_s'])) for t in (0.,.1)])

    result = dict(passed=False, checks=checks, source='installed development dataset plus synthetic map/evidence fixtures')
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError('Server exited before readiness')
            try:
                state=call('/observation')
                break
            except OSError:
                time.sleep(.25)
        else:
            raise TimeoutError('Server readiness timeout')
        catalog=call('/catalog')
        check('catalog exposes trusted local scenario indices',len(catalog['scenes'])>0)
        call('/observation',token='invalid',status=403)
        call('/open',dict(session_id=state['session_id'],index=0,step=100000),status=409)
        check('invalid replay target preserves the active world',call('/observation')['session_id']==state['session_id'])
        for _ in range(10):
            state=call('/advance',dict(bound(state),actions={},control_tokens={}))
        initial=deepcopy(state)
        features=call('/map')['features']
        points=[p for feature in features.values() for p in feature.get('polyline',feature.get('polygon',[]))]
        bounds=[(min(p[i] for p in points),max(p[i] for p in points)) for i in (0,1)]
        candidates=sorted(key for key,row in state['objects'].items() if row['type']=='VEHICLE'
                          and all(low<=row['position_m'][i]<=high for i,(low,high) in enumerate(bounds)))
        check('at least four valid vehicles are available',len(candidates)>=4)
        chosen=candidates[:4]
        claim=call('/takeover',dict(bound(state),actor_ids=chosen,observation_scope={'kind':'radius','radius_m':5}))
        state=claim['observation'];tokens=claim['control_tokens']
        check('four-vehicle takeover preserves native identities and positions',all(
            state['objects'][actor]['native_id']==initial['objects'][actor]['native_id'] and
            state['objects'][actor]['position_m']==initial['objects'][actor]['position_m'] for actor in chosen))
        scoped=call('/actor-observation?actor_id='+chosen[0],token=tokens[chosen[0]])
        check('actor observation applies its radius and omits unresolved-position signals',
              all(math.dist(scoped['objects'][chosen[0]]['position_m'],row['position_m'])<=5
                  for row in scoped['objects'].values()) and scoped['signals']=={})
        call('/map-versions',token=tokens[chosen[0]],status=403)
        original_bound=bound(state)
        for index,actor in enumerate(chosen):
            trajectory=proposal(state,actor,'native-'+actor)
            call('/plans',dict(original_bound,plan=trajectory,control_token=tokens[actor]),token=tokens[actor])
            reply=call('/actor-action',dict(original_bound,actor_id=actor,control_token=tokens[actor],
                       action=[0,-1],plan_id=trajectory['plan_id']),token=tokens[actor])
            check('four-client barrier '+str(index),reply['advanced']==(index==3))
        state=call('/observation')
        check('exactly one shared physics step executed',state['step']==11)
        call('/actor-action',dict(original_bound,actor_id=chosen[0],control_token=tokens[chosen[0]],
                                 action=[0,-1]),token=tokens[chosen[0]],status=409)
        patch=json.loads((ROOT/'examples/recorded-preparation/synthetic-map-patch.json').read_text())
        old_map=state['map_version'];physical=state['physical_map_version']
        body=dict(bound(state),request_id='native-patch',**patch)
        receipt=call('/map-patch',body)
        check('map patch retry is idempotent',call('/map-patch',body)==receipt)
        state=receipt['observation']
        check('planning patch preserves physical map and frame',state['physical_map_version']==physical and state['step']==11)
        rollback=call('/map-rollback',dict(bound(state),request_id='native-rollback',
                      target_version=old_map,provenance=patch['provenance']))
        state=rollback['observation']
        check('rollback publishes a new revision with original content',
              state['map_version']!=old_map and call('/map')['content_sha256']==physical)
        reopened=call('/open',dict(session_id=state['session_id'],index=0,step=10))
        from map_update.recorded_recovery import physical_state
        check('independent original branch reconstructs the same starting world',physical_state(reopened)==physical_state(initial))
        state=reopened
        all_claim=call('/takeover',dict(bound(state),actor_ids=candidates))
        advanced=call('/advance',dict(bound(all_claim['observation']),actions={actor:[0,-1] for actor in candidates},
                                     control_tokens=all_claim['control_tokens']),status=(200,409))
        state=call('/observation')
        check('all eligible vehicles retain external control',all(
            state['objects'][actor]['mode']=='external' for actor in candidates))
        if 'error' in advanced:
            check('all-car batch stops before a source validity gap without advancing',
                  advanced['error']=='controlled_or_sdc_track_end_or_gap' and state['step']==10)
            result['all_vehicle_advance']='stopped_before_source_validity_gap'
        else:
            check('all eligible vehicles advance atomically',state['step']==11)
            result['all_vehicle_advance']='one_step_completed'
        result['all_vehicle_count']=len(candidates)
        call('/open',dict(session_id=state['session_id'],index=0,step=10))
        if args.browser:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',
                    args=['--no-sandbox','--enable-unsafe-swiftshader','--disable-dev-shm-usage'])
                page=browser.new_page(viewport={'width':1600,'height':1050})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base,wait_until='networkidle')
                page.fill('#access-key',key)
                page.locator('#connect-form button').click()
                page.wait_for_function('window.RECORDED_WORKBENCH_STATE?.step===10')
                page.fill('#actor-search',chosen[0])
                page.locator('#actors input').first.check()
                page.click('#takeover')
                page.wait_for_function('RECORDED_WORKBENCH_STATE.controlled.length===1')
                page.click('#step')
                page.wait_for_function('RECORDED_WORKBENCH_STATE.step===11')
                check('browser takes over a source actor and executes a live step',True)
                before=call('/observation')
                page.fill('#payload',json.dumps(patch))
                page.click('#patch')
                page.wait_for_function('(old)=>RECORDED_WORKBENCH_STATE.map_version!==old',arg=before['map_version'])
                event=json.loads((ROOT/'examples/road-state-intake/synthetic-event.json').read_text())
                event['samples'][0]['timestamp_ns']='1100000000'
                page.fill('#payload',json.dumps(event))
                page.click('#evidence')
                page.wait_for_function('RECORDED_WORKBENCH_STATE.road_state_count===1')
                check('browser publishes structure and road-state evidence',True)
                page.select_option('#map-version',before['map_version'])
                prior_version=call('/observation')['map_version']
                page.click('#rollback')
                page.wait_for_function('(old)=>RECORDED_WORKBENCH_STATE.map_version!==old',arg=prior_version)
                check('browser rollback restores original planning-map content',call('/map')['content_sha256']==physical)
                current=call('/observation')
                page.fill('#payload',json.dumps(proposal(current,chosen[0],'browser-plan')))
                page.click('#submit-plan')
                page.wait_for_timeout(300)
                check('browser submits an actual trajectory intent',call('/plans')[chosen[0]]['plan']['plan_id']=='browser-plan')
                with page.expect_download() as download:
                    page.click('#report')
                download.value.save_as(str(args.out/'browser-report.json'))
                check('browser exports an executed-session report',True)
                page.screenshot(path=str(args.out/'workbench.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                check('workbench mobile layout fits the viewport',page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'))
                if len(catalog['scenes'])>1:
                    old_session=call('/observation')['session_id']
                    page.select_option('#scene','1')
                    page.click('#open')
                    page.wait_for_function('(old)=>RECORDED_WORKBENCH_STATE.session_id!==old && RECORDED_WORKBENCH_STATE.step===0',arg=old_session)
                    check('browser switches to another source recording',call('/observation')['scene_id']!=initial['scene_id'])
                check('browser has no uncaught JavaScript errors',not errors)
                browser.close()
        result['passed']=True
    except Exception as error:
        result['error']=str(error)
        raise
    finally:
        process.terminate()
        process.wait(timeout=15)
        server_log.close()
        (args.out/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,checks=len(checks),out=str(args.out))))


if __name__ == '__main__':
    main()
