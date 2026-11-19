import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import urllib.error
import unittest

from map_update.recorded_service import RecordedService, RecordedHTTPServer


class World:
    def __init__(self):
        self.step = 0
        self.controlled = set()
        self.stopped = None
        self.scenario = {'id': 'test-scene'}

    def map_input(self):
        return {'lane': {'polyline': [[0., 0.], [100., 0.]]}}

    def observe(self):
        return dict(step=self.step, objects={'car': {'position_m': [self.step, 0],
                    'mode': 'external' if self.controlled else 'recorded_replay'}},
                    signals={}, coordinate_frame='XY', simulation_time_s=self.step * .1)

    def takeover(self, ids):
        if ids != ['car']:
            raise ValueError('Invalid vehicle')
        self.controlled.update(ids)

    def advance(self, actions, *, observation_step):
        self.step += 1


class RecordedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = open(Path(self.temp.name) / 'events.jsonl', 'x+')
        self.addCleanup(self.journal.close)
        self.now = 0.
        self.world = World()
        self.service = RecordedService(self.world, self.journal, history_frames=2,
                                       action_timeout=10., clock=lambda: self.now)

    def body(self, **extra):
        state = self.service.snapshot()
        return dict(session_id=state['session_id'], map_version=state['map_version'],
                    state_version=state['state_version'], observation_step=state['step'], **extra)

    def claim(self):
        return self.service.takeover(self.body(actor_ids=['car']))['control_tokens']

    def test_claim_preserves_state_and_rejects_duplicate_ownership(self):
        initial = self.service.snapshot()
        self.claim()
        self.assertEqual(self.world.step, 0)
        self.assertNotEqual(initial['state_version'], self.service.snapshot()['state_version'])
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.claim()

    def test_atomic_batch_requires_identity_frame_map_state_and_tokens(self):
        tokens = self.claim()
        body = self.body(actions={'car': [0., -.5]}, control_tokens=tokens)
        for field, value in [('session_id', 'old'), ('map_version', 'wrong'),
                             ('state_version', 'old'), ('observation_step', True),
                             ('actions', {}), ('actions', {'car': [float('nan'), 0]})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.service.advance(dict(body, **{field:value}))
        with self.assertRaises(PermissionError):
            self.service.advance(dict(body, control_tokens={'car':'wrong'}))
        self.assertEqual(self.world.step, 0)
        result = self.service.advance(body)
        self.assertEqual(result['step'], 1)
        with self.assertRaises(ValueError):
            self.service.advance(body)
        self.assertEqual(self.world.step, 1)

    def test_history_is_bounded_detached_and_current_only(self):
        for _ in range(4):
            self.service.advance(self.body(actions={}, control_tokens={}))
        history = self.service.read('/history')
        self.assertEqual([row['step'] for row in history['observations']], [3, 4])
        history['observations'][0]['objects'].clear()
        self.assertTrue(self.service.read('/history')['observations'][0]['objects'])
        self.assertNotIn('tracks', self.service.read('/map'))

    def test_timeout_freezes_world_and_reads_do_not_extend_deadline(self):
        tokens = self.claim()
        self.now = 9
        self.service.snapshot()
        self.now = 10
        with self.assertRaisesRegex(ValueError, 'action_timeout'):
            self.service.advance(self.body(actions={'car':[0,0]}, control_tokens=tokens))
        self.assertEqual(self.world.step, 0)
        self.assertEqual(self.service.snapshot()['objects']['car']['mode'], 'external')

    def test_journal_keeps_actual_history_and_excludes_credentials(self):
        tokens = self.claim()
        self.service.advance(self.body(actions={'car':[0,0]}, control_tokens=tokens))
        self.journal.seek(0)
        content = self.journal.read()
        self.assertNotIn(tokens['car'], content)
        events = [json.loads(line) for line in content.splitlines()]
        self.assertEqual(events[-1]['event'], 'advance')
        self.assertEqual(events[-1]['data']['step'], 1)

    def test_journal_failure_prevents_physics_and_latches_stop(self):
        self.journal.close()
        with self.assertRaises(ValueError):
            self.service.advance(self.body(actions={}, control_tokens={}))
        self.assertEqual(self.world.step, 0)
        self.assertEqual(self.service.snapshot()['service_stopped'], 'journal_write_failed')

    def test_http_entrypoint_authentication_and_replayed_batch(self):
        with RecordedHTTPServer(('127.0.0.1', 0), self.service, 'test-access') as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                def request(path, body=None, token='test-access'):
                    data = None if body is None else json.dumps(body).encode()
                    req = urllib.request.Request(
                        f'http://127.0.0.1:{server.server_port}' + path, data=data,
                        headers={'Authorization': 'Bearer ' + token})
                    try:
                        with urllib.request.urlopen(req) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        return error.code, json.load(error)

                self.assertEqual(request('/observation', token='wrong')[0], 403)
                self.assertEqual(request('/missing')[0], 404)
                self.assertEqual(request('/observation')[1]['step'], 0)
                body = self.body(actions={}, control_tokens={})
                self.assertEqual(request('/advance', body)[1]['step'], 1)
                self.assertEqual(request('/advance', body)[0], 409)
                self.assertEqual(request('/observation')[1]['step'], 1)
                event_path = Path(__file__).resolve().parents[1] / 'examples/road-state-intake/synthetic-event.json'
                event = json.loads(event_path.read_text())
                event['coordinate_reference']['frame'] = 'XY'
                evidence = self.body(events=[event], scene_id='test-scene',
                                     time_basis='simulation_time_ns', submission_id='http-evidence')
                stale_action = self.body(actions={}, control_tokens={})
                status, accepted = request('/evidence', evidence)
                self.assertEqual(status, 200)
                self.assertEqual(request('/evidence', evidence), (status, accepted))
                layer = request('/road-states')[1]
                self.assertEqual(len(layer['road_states']['features']), 1)
                self.assertEqual(layer['road_state_version'],
                                 request('/observation')[1]['road_state_version'])
                self.assertEqual(request('/advance', stale_action)[0], 409)
            finally:
                server.shutdown()
                thread.join()

    def test_partial_physics_failure_stops_retries(self):
        def fail(*args, **kwargs):
            self.world.step += 1
            raise RuntimeError('native failure')
        self.world.advance = fail
        body = self.body(actions={}, control_tokens={})
        with self.assertRaises(RuntimeError):
            self.service.advance(body)
        with self.assertRaisesRegex(ValueError, 'advance_failed'):
            self.service.advance(body)
        self.assertEqual(self.world.step, 1)


if __name__ == '__main__':
    unittest.main()
