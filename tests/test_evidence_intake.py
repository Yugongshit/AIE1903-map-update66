from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from map_update.recorded_service import RecordedService
from test_recorded_service import World

ROOT = Path(__file__).resolve().parents[1]


def synthetic_event():
    event = json.loads((ROOT/'examples/road-state-intake/synthetic-event.json').read_text())
    event['coordinate_reference']['frame'] = 'XY'
    return event


class EvidenceIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = open(Path(self.temp.name)/'events.jsonl', 'x+')
        self.addCleanup(self.log.close)
        self.world = World()
        self.service = RecordedService(self.world, self.log)

    def envelope(self, event=None, submission_id='s1'):
        current = self.service.snapshot()
        return dict(session_id=current['session_id'], scene_id=current['scene_id'],
                    map_version=current['map_version'], state_version=current['state_version'],
                    observation_step=current['step'], time_basis='simulation_time_ns',
                    submission_id=submission_id, events=[event or synthetic_event()])

    def advance(self):
        current = self.service.snapshot()
        return self.service.advance(dict(session_id=current['session_id'],
            map_version=current['map_version'], state_version=current['state_version'],
            observation_step=current['step'], actions={}, control_tokens={}))

    def test_publish_is_bound_and_does_not_mutate_physics_or_map(self):
        before = self.service.snapshot()
        original_map = self.world.map_input()
        result = self.service.evidence_update(self.envelope())['observation']
        self.assertEqual(result['step'], 0)
        self.assertEqual(result['objects'], before['objects'])
        self.assertEqual(result['map_version'], before['map_version'])
        self.assertNotEqual(result['state_version'], before['state_version'])
        self.assertNotEqual(result['road_state_version'], before['road_state_version'])
        self.assertEqual(self.world.map_input(), original_map)
        props = result['road_states']['features'][0]['properties']
        self.assertEqual(props['state'], 'observed_active')
        self.assertFalse(props['score_is_probability'])
        self.assertIn('uncalibrated', props['score_semantics'])
        self.assertEqual(props['source_object_id'], 'synthetic-cone-group-1')

    def test_identical_retry_is_idempotent_even_after_world_advances(self):
        request = self.envelope()
        result = self.service.evidence_update(request)
        self.advance()
        version = self.service.snapshot()['state_version']
        self.assertEqual(self.service.evidence_update(request), result)
        self.assertEqual(self.service.snapshot()['state_version'], version)
        changed = deepcopy(request)
        changed['events'][0]['samples'][0]['quality'] = .5
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.service.evidence_update(changed)

    def test_entire_batch_is_rejected_before_publication(self):
        request = self.envelope()
        invalid = synthetic_event()
        invalid['event_id'] = 'B'
        invalid['samples'][0]['quality'] = float('nan')
        request['events'].append(invalid)
        before = self.service.snapshot()
        with self.assertRaises(ValueError):
            self.service.evidence_update(request)
        self.assertEqual(self.service.evidence, {})
        self.assertEqual(self.service.snapshot()['state_version'], before['state_version'])

    def test_future_coordinates_missing_source_and_bad_aliases_are_rejected(self):
        for field, value in [('coordinate_reference', {'frame':'wrong','units':'m'}),
                             ('map_aliases', [{'map_element_id':'missing-lane'}]),
                             ('score_semantics', ''), ('unknown_fields', None),
                             ('map_layer', {'predicted_layer':'base_map'})]:
            with self.subTest(field=field):
                event = synthetic_event()
                event[field] = value
                with self.assertRaises(ValueError):
                    self.service.evidence_update(self.envelope(event))
        for field, value in [('timestamp_ns', '100000000'), ('source_ids', []),
                             ('quality', True), ('future_tracks', [])]:
            event = synthetic_event()
            event['samples'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.service.evidence_update(self.envelope(event))
        self.assertEqual(self.service.evidence, {})

    def test_expiry_uses_simulation_clock_and_does_not_clear(self):
        self.service.evidence_update(self.envelope())
        self.assertEqual(self.advance()['road_states']['summary']['unknown'], 1)
        result = self.advance()
        self.assertEqual(result['road_states']['summary']['stale'], 1)
        self.assertEqual(result['road_states']['cleared_events'], [])
        self.assertEqual(self.service.read('/road-states')['step'], 2)

    def test_clearance_requires_explicit_full_observation_and_preserves_history(self):
        event = synthetic_event()
        self.service.evidence_update(self.envelope(event))
        self.advance()
        sample = dict(timestamp_ns='100000000', observation='supports_prior',
                      quality=.8, fully_observed=False, source_ids=['synthetic-recovery'])
        event['samples'].append(sample)
        result = self.service.evidence_update(self.envelope(event, 's2'))
        self.assertEqual(result['observation']['road_states']['summary']['unknown'], 1)
        self.advance()
        event['samples'].append(dict(sample, timestamp_ns='200000000', fully_observed=True))
        result = self.service.evidence_update(self.envelope(event, 's3'))
        layer = result['observation']['road_states']
        self.assertEqual(layer['features'], [])
        self.assertEqual(layer['cleared_events'][0]['event_id'], event['event_id'])
        self.assertEqual(layer['cleared_events'][0]['source_object_id'], event['source_object_id'])
        old = self.service.read('/history')['observations'][0]['road_states']
        self.assertEqual(old['summary']['observed_active'], 1)

    def test_published_metadata_and_observations_cannot_be_rewritten(self):
        event = synthetic_event()
        self.service.evidence_update(self.envelope(event))
        for field, value in [('source_object_id','other'), ('ttl_s', 100)]:
            changed = deepcopy(event)
            changed[field] = value
            with self.assertRaisesRegex(ValueError, 'immutable'):
                self.service.evidence_update(self.envelope(changed, 's2'))
        event['samples'][0]['quality'] = .9
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.service.evidence_update(self.envelope(event, 's2'))

    def test_journal_failure_prevents_publication(self):
        request = self.envelope()
        self.log.close()
        with self.assertRaises(ValueError):
            self.service.evidence_update(request)
        self.assertEqual(self.service.evidence, {})
        self.assertEqual(self.service.fault, 'journal_write_failed')

    def test_scene_and_time_basis_are_explicit(self):
        for field, value in [('scene_id','wrong'), ('time_basis','unix_time_ns')]:
            request = self.envelope()
            request[field] = value
            with self.assertRaises(ValueError):
                self.service.evidence_update(request)

    def test_input_and_read_results_are_detached(self):
        request = self.envelope()
        self.service.evidence_update(request)
        request['events'][0]['samples'].clear()
        result = self.service.read('/road-states')
        result['road_states']['features'].clear()
        self.assertEqual(len(self.service.read('/road-states')['road_states']['features']), 1)


if __name__ == '__main__':
    unittest.main()
