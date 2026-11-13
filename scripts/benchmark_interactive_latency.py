"""Measure real MetaDrive input latency under a repeatable HTTP viewer load.

Run with the simulation Python interpreter. This creates its own loopback-only
server and room, then always closes them; it never contacts an existing room.
Compare --compact with the default full transport, or run the same script on
an earlier source revision. Results describe this host's backend, not browser
FPS or performance measured on a user's Mac. Never run beside another native
engine benchmark when comparing results.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_platform import PlatformServer


def summarize(values):
    if not values:
        return None
    ordered = sorted(values)
    return {'count': len(values), 'mean': round(statistics.mean(values), 3),
            'p50': round(statistics.median(values), 3),
            'p95': round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3),
            'max': round(max(values), 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--compact', action='store_true')
    parser.add_argument('--viewers', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120 or not 1 <= args.viewers <= 10:
        parser.error('Use 1..120 seconds and 1..10 viewers')
    args.out.mkdir(parents=True, exist_ok=True)
    server = PlatformServer(('127.0.0.1', 0), ROOT)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    stop, guard = threading.Event(), threading.Lock()
    stats = {key: [] for key in ('input_ms', 'state_ms', 'state_bytes', 'jobs_ms',
                                'broker_status_ms', 'last_input_age_ms', 'errors')}
    physical_reasons, physical_steps = {}, set()
    identity, clients = {}, []

    def api(path, body=None):
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=60) as response:
            data = response.read()
        return json.loads(data), len(data)

    # Instrument real operations without replacing native physics or responses.
    original_publish = server.engine._publish_result
    def publish(result):
        original_publish(result)
        vehicle = (result.get('interactive') or {}).get('vehicles', {}).get('P1')
        step = result.get('step', 0)
        if vehicle and step > 0:
            with guard:
                if step not in physical_steps:
                    physical_steps.add(step)
                    reason = vehicle.get('stopped_reason') or 'none'
                    physical_reasons[reason] = physical_reasons.get(reason, 0) + 1
    server.engine._publish_result = publish

    original_status = server.engine.status
    def status(*positional, **keywords):
        started = time.perf_counter()
        result = original_status(*positional, **keywords)
        with guard:
            stats['broker_status_ms'].append((time.perf_counter()-started)*1000)
        return result
    server.engine.status = status

    original_execute = server.engine._execute
    def execute(identifier, command):
        started = time.perf_counter()
        original_execute(identifier, command)
        if command['command'] == 'interactive_tick':
            with guard:
                stats['jobs_ms'].append((time.perf_counter()-started)*1000)
    server.engine._execute = execute

    def failed(error):
        with guard:
            stats['errors'].append(type(error).__name__ + ': ' + str(error))

    try:
        opened, _ = api('/api/interactive/open', {'scene_id': 'nuscenes-scene0980', 'version': 'updated'})
        identity = opened['room']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state, _ = api('/api/interactive/state')
            if state['phase'] == 'paused':
                break
            if state['phase'] == 'error':
                raise RuntimeError(state['error'])
            time.sleep(.1)
        else:
            raise RuntimeError('Native startup timeout')
        (args.out/'sample-state.json').write_text(json.dumps(state))
        lease, _ = api('/api/interactive/claim', {**identity, 'vehicle_id': 'P1', 'kind': 'manual'})

        def driver():
            sequence = 0
            while not stop.is_set():
                started = time.perf_counter()
                sequence += 1
                try:
                    api('/api/interactive/input', {'session_id': identity['session_id'],
                        'epoch': identity['epoch'], 'lease_token': lease['lease_token'],
                        'seq': sequence, 'actions': {'P1': [0, .2]}})
                except Exception as error:
                    failed(error)
                with guard:
                    stats['input_ms'].append((time.perf_counter()-started)*1000)
                stop.wait(max(0, .1-(time.perf_counter()-started)))

        def viewer():
            since = ''
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    suffix = ('?compact=1&since_state_id='+since) if args.compact else ''
                    row, size = api('/api/interactive/state'+suffix)
                    result = (row.get('engine') or {}).get('last_result') or {}
                    since = result.get('state_id', since)
                    age = (row.get('room') or {}).get('vehicles', {}).get('P1', {}).get('last_input_age_ms')
                    with guard:
                        stats['state_ms'].append((time.perf_counter()-started)*1000)
                        stats['state_bytes'].append(size)
                        if age is not None:
                            stats['last_input_age_ms'].append(age)
                except Exception as error:
                    failed(error)
                stop.wait(max(0, .15-(time.perf_counter()-started)))

        clients = [threading.Thread(target=driver)] + [threading.Thread(target=viewer) for _ in range(args.viewers)]
        for client in clients:
            client.start()
        api('/api/interactive/control', {**identity, 'action': 'play'})
        started = time.monotonic()
        stop.wait(args.seconds)
        elapsed = time.monotonic()-started
        api('/api/interactive/control', {**identity, 'action': 'pause'})
        stop.set()
        for client in clients:
            client.join(timeout=5)
        while server.engine.status()['busy']:
            time.sleep(.05)
        final, full_bytes = api('/api/interactive/state')
        compact_state, compact_bytes = api('/api/interactive/state?compact=1')
        (args.out/'final-state.json').write_text(json.dumps(final))
        report = {
            'seconds': elapsed, 'step': final['room']['step'],
            'steps_per_wall_second': final['room']['step']/elapsed,
            'metrics': {key: summarize(values) for key, values in stats.items() if key != 'errors'},
            'errors': stats['errors'], 'compact': args.compact, 'phase': final['phase'], 'viewers': args.viewers,
            'native_input_stopped_reasons': physical_reasons, 'native_physics_steps_sampled': len(physical_steps),
            'same_physics_state_payload_bytes': {'full': full_bytes, 'compact': compact_bytes,
                'matching_state_id': (final['engine']['last_result']['state_id'] ==
                                      compact_state['engine']['last_result']['state_id']),
                'full_actor_count': len(final['engine']['last_result']['snapshot']['actors']),
                'compact_actor_count': len(compact_state['engine']['last_result']['snapshot']['actors'])},
            'source': 'real MetaDrive physics with independent HTTP viewers and 10Hz input',
            'method': {'viewer_interval_s': .15, 'driver_interval_s': .1, 'action': [0, .2],
                       'browser_rendering_included': False, 'measurements_are_local_host_only': True},
            'environment': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
            'source_sha256': {name: sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                'map_update/simulation_platform.py', 'map_update/simulation_interactive_server.py',
                'map_update/simulation_room.py', 'scripts/simulation_session_worker.py',
                'map_update/simulation_session.py', 'map_update/simulation_interactive.py',
                'map_update/simulation_obstacle_runtime.py')},
        }
        (args.out/'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
        if report['errors'] or report['phase'] == 'error' or report['step'] == 0:
            raise RuntimeError('Benchmark did not complete a valid driving run; inspect report.json')
    finally:
        stop.set()
        for client in clients:
            client.join(timeout=3)
        if identity:
            try:
                api('/api/interactive/control', {**identity, 'action': 'close'})
                deadline = time.monotonic()+30
                while time.monotonic() < deadline and api('/api/interactive/state')[0]['active']:
                    time.sleep(.1)
            except Exception:
                pass
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
