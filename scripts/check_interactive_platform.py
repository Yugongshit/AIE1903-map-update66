#!/usr/bin/env python3
"""Exercise a real local MetaDrive room from independent Chromium clients.

No requests, simulation state or physics are mocked. Run the platform server
first, then: python scripts/check_interactive_platform.py --base-url http://127.0.0.1:8100
Requires Playwright and Chromium. Reports deliberately exclude credentials.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8100')
    parser.add_argument('--output', default='outputs/interactive-platform-check')
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--control-key-env', default='SIMULATION_CONTROL_KEY')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    key = os.environ.get(args.control_key_env, '')
    secrets = [key] if key else []
    report = {'live_engine': True, 'api_mocked': False, 'physics_mocked': False, 'checks': [], 'metrics': {}}
    identity = {}
    pages = []
    contexts = []
    errors = []
    requests = []

    def clean(value):
        value = str(value)
        for token in secrets:
            if token:
                value = value.replace(token, '[redacted]')
        return value

    def check(name, condition, **metrics):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)
        report['metrics'].update(metrics)
        print('PASS:', name, flush=True)

    def api(path, body=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method='POST' if body is not None else 'GET')
        try:
            with urlopen(request, timeout=120) as response:
                status, data = response.status, json.load(response)
        except HTTPError as error:
            status, data = error.code, json.load(error)
        if isinstance(expected, tuple):
            valid = status in expected
        else:
            valid = status == expected
        if not valid:
            raise AssertionError(f'{path.split("?")[0]} returned {status}: {clean(data.get("error", data))}')
        return data

    def state():
        return api('/api/interactive/state?' + urlencode({'session_id': identity['session_id']}))

    def host_control(action):
        return api('/api/interactive/control', {**identity, 'action': action})

    def native_state():
        return state()['engine']['last_result']

    def wait_until(predicate, timeout=90, message='native state condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            if pages:
                pages[0].wait_for_timeout(150)
            else:
                time.sleep(.15)
        raise AssertionError('Timed out: ' + message)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'])
        try:
            def new_page(url):
                context = browser.new_context(viewport={'width': 1440, 'height': 1100})
                contexts.append(context)
                page = context.new_page()
                pages.append(page)
                page.on('pageerror', lambda error: errors.append(clean(error)))
                page.on('request', lambda request: requests.append(request.url.split('?', 1)[0]))
                page.goto(url)
                page.wait_for_function('window.MAP_PLATFORM?.state?.snapshot != null', timeout=120000)
                return page

            host = new_page(base + '/simulation_platform.html?scene=nuscenes-scene0980')
            host.locator('#interactive-panel > summary').click()
            if key:
                host.locator('#simulation-control-key').fill(key)
            with host.expect_response(lambda response: response.url.endswith('/api/interactive/open')) as opened:
                host.locator('#start-interactive').click()
            body = opened.value.json()
            credentials = body['room']
            identity.update({name: credentials[name] for name in ('session_id', 'epoch', 'host_token')})
            join = credentials['join_token']
            secrets.extend([identity['host_token'], join])
            host.wait_for_function('MAP_PLATFORM.interactive.frame?.interactive?.enabled === true', timeout=180000)
            check('host opens a real six-vehicle room', host.locator('.interactive-vehicle').count() == 6)
            initial = native_state()
            check('native result is physical and omits recorded moving traffic', initial['live_engine'] is True and
                  initial['interactive']['recorded_moving_traffic'] is False and
                  set(initial['interactive']['vehicles']) == {'ego', 'P1', 'P2', 'P3', 'P4', 'P5'})
            check('ego starts waiting for external control', initial['interactive']['vehicles']['ego']['mode'] == 'external' and
                  initial['interactive']['vehicles']['ego']['speed_m_s'] < .1)

            query = urlencode({'scene': 'nuscenes-scene0980', 'session': identity['session_id']})
            guest = new_page(base + '/simulation_platform.html?' + query + '#join=' + join)
            guest.wait_for_function('MAP_PLATFORM.interactive.frame != null', timeout=120000)
            spectator = new_page(base + '/simulation_platform.html?' + query)
            spectator.wait_for_function('MAP_PLATFORM.interactive.frame != null', timeout=120000)
            check('spectator is read-only', spectator.locator('#interactive-host-controls').is_hidden() and
                  spectator.locator('[data-vehicle="P3"] [data-action="claim"]').is_disabled() and
                  spectator.locator('#play-pause').is_disabled())
            guest.locator('[data-vehicle="P1"] [data-action="claim"]').click()
            guest.wait_for_function('MAP_PLATFORM.interactive.vehicle_id === "P1"', timeout=30000)
            host.locator('[data-vehicle="P2"] [data-action="claim"]').click()
            host.wait_for_function('MAP_PLATFORM.interactive.vehicle_id === "P2"', timeout=30000)
            check('players retain independent follow cameras', guest.evaluate('MAP_PLATFORM.camera.target') == 'interactive:P1'
                  and host.evaluate('MAP_PLATFORM.camera.target') == 'interactive:P2')
            api('/api/interactive/claim', {**identity, 'vehicle_id': 'P1', 'kind': 'manual'}, expected=409)
            check('an occupied vehicle rejects a second controller with HTTP 409', True)

            request_mark = len(requests)
            start = native_state()['interactive']['vehicles']['P1']['position_city_m']
            guest.bring_to_front()
            hold_started = time.monotonic()
            with guest.expect_response(lambda response: response.url.endswith('/api/interactive/input') and
                    (response.request.post_data_json or {}).get('actions', {}).get('P1') == [0, 1]) as driving:
                guest.keyboard.down('w')
            check('foreground keyboard acceleration is accepted before clock starts', driving.value.status == 200)
            start_step = native_state()['step']
            host_control('play')
            guest.wait_for_timeout(3000)
            wait_until(lambda: native_state()['step'] >= start_step + 2, timeout=45,
                       message='at least two real physics ticks during keyboard hold')
            moved = native_state()
            held_action = moved['interactive']['vehicles']['P1'].get('requested_action')
            guest.keyboard.up('w')
            report['metrics']['keyboard_hold_wall_s'] = round(time.monotonic() - hold_started, 3)
            report['metrics']['keyboard_physics_steps'] = moved['step'] - start_step
            finish = moved['interactive']['vehicles']['P1']['position_city_m']
            distance = math.dist(start[:2], finish[:2])
            report['metrics'].update(keyboard_start=start, keyboard_finish=finish, keyboard_requested_action=held_action, keyboard_displacement_m=round(distance, 5), keyboard_step=moved['step'])
            check('three-second keyboard hold moves the physical P1 vehicle', distance > .05,
                  keyboard_displacement_m=round(distance, 4), keyboard_end_step=moved['step'])
            wait_until(lambda: native_state()['interactive']['vehicles']['P1']['speed_m_s'] < .2,
                       timeout=60, message='key release braking')
            check('key release brakes the physical vehicle', True)
            guest.bring_to_front()
            guest.keyboard.down('w')
            guest.wait_for_timeout(1500)
            guest.evaluate('window.dispatchEvent(new Event("blur"))')
            guest.keyboard.up('w')
            wait_until(lambda: native_state()['interactive']['vehicles']['P1']['speed_m_s'] < .2,
                       timeout=60, message='blur braking')
            check('window blur brakes the physical vehicle', True)
            host_control('pause')
            wait_until(lambda: not api('/api/engine/status').get('busy'), message='pause finishes current native tick')
            paused = native_state()
            step = paused['step']
            for page in (host, guest, spectator):
                page.wait_for_function('(step)=>MAP_PLATFORM.state.step === step', arg=step, timeout=60000)
            pose = paused['interactive']['vehicles']['P1']['position_city_m']
            check('all clients show the same committed native step and pose', all(
                page.evaluate('MAP_PLATFORM.interactive.frame.interactive.vehicles.P1.position_city_m') == pose
                for page in (host, guest, spectator)), synchronized_step=step)
            for _ in range(20):
                api('/api/interactive/state?' + urlencode({'session_id': identity['session_id']}))
                api('/api/interactive/observation?' + urlencode({'session_id': identity['session_id'], 'vehicle_id': 'ego'}))
            check('paused spectator GET requests never advance physics', native_state()['step'] == step)
            api('/api/engine/command', {'command': 'advance', 'steps': 1}, expected=(403, 409))
            check('legacy client step command cannot advance the shared room', native_state()['step'] == step)
            review_requests = requests[request_mark:]
            check('live review uses no recorded snapshots, PNG polling or legacy commands', not any(
                path.endswith('/snapshot') or path.endswith('/api/engine/frame.png') or path.endswith('/api/engine/command')
                for path in review_requests))

            guest.locator('[data-vehicle="P1"] [data-action="release"]').click()
            guest.wait_for_function('MAP_PLATFORM.interactive.vehicle_id === null')
            host.locator('[data-vehicle="P2"] [data-action="release"]').click()
            host.wait_for_function('MAP_PLATFORM.interactive.vehicle_id === null')
            batch = api('/api/interactive/claim', {**identity, 'vehicle_ids': ['P1', 'P2', 'P3', 'P4', 'P5'],
                'kind': 'external', 'client_label': 'native-integration-check'})
            token = batch['lease_token']
            secrets.append(token)
            observation = api('/api/interactive/observation?' + urlencode({'session_id': identity['session_id'], 'vehicle_id': 'ego'}))
            check('external observation includes current physical P1 without recorded future tracks',
                  observation['vehicles']['P1']['position_city_m'] == pose and not observation['recorded_future_tracks_included'])
            seq = 1
            actions = {vehicle: [0, .2] for vehicle in ['P1', 'P2', 'P3', 'P4', 'P5']}
            api('/api/interactive/input', {**identity, 'token': token, 'seq': seq,
                'observation_step': observation['step'], 'actions': actions})
            slots = state()['room']['vehicles']
            check('one external client atomically controls all five traffic vehicles', all(
                slots[vehicle]['claimed'] and slots[vehicle]['mode'] == 'external' and
                slots[vehicle]['last_action_seq'] == 1 and slots[vehicle]['observation_step'] == observation['step']
                for vehicle in actions))
            host_control('play')
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                latest = native_state()
                if latest['step'] >= step + 3:
                    break
                seq += 1
                api('/api/interactive/input', {**identity, 'token': token, 'seq': seq,
                    'observation_step': latest['step'], 'actions': actions})
                host.wait_for_timeout(100)
            else:
                raise AssertionError('External batch actions did not reach physics')
            host_control('pause')
            wait_until(lambda: not api('/api/engine/status').get('busy'))
            latest = native_state()
            check('native engine applies external modes to all five actual cars', all(
                latest['interactive']['vehicles'][vehicle]['mode'] == 'external' for vehicle in actions))
            api('/api/interactive/release', {**identity, 'token': token})
            report['metrics']['last_step'] = latest['step']
            report['metrics']['positions_city_m'] = {vehicle: row['position_city_m'] for vehicle, row in latest['interactive']['vehicles'].items()}
            report['metrics']['physical_vehicle_labels'] = {vehicle: row.get('label', vehicle) for vehicle, row in latest['interactive']['vehicles'].items()}
            host.evaluate('MAP_PLATFORM.setCameraPreset("overview")')
            host.locator('#interactive-panel').evaluate('(node)=>{node.open=true;node.scrollTop=node.scrollHeight;}')
            host.screenshot(path=str(output / 'interactive-six-cars.png'))
            check('browser clients emit no JavaScript errors', not errors)
            report['ok'] = True
        except Exception as error:
            report['ok'] = False
            report['error'] = clean(error)
            print('FAIL:', clean(error), flush=True)
            if pages:
                try:
                    pages[0].screenshot(path=str(output / 'failure.png'))
                except Exception:
                    pass
        finally:
            if identity:
                try:
                    host_control('pause')
                    wait_until(lambda: not api('/api/engine/status').get('busy'), timeout=60)
                    host_control('close')
                except Exception as error:
                    report['cleanup_error'] = clean(error)
            for context in contexts:
                context.close()
            browser.close()
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Report:', output / 'report.json', flush=True)
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
