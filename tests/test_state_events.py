from copy import deepcopy
from pathlib import Path
import unittest

from map_update.state_events import build_state_event_layer


def sample(seconds, observation='supports_change', geometry=True, **extra):
    row = {'timestamp_ns': str(seconds*1_000_000_000), 'frame_id': str(seconds), 'visit_id': 'visit',
           'observation': observation, 'quality': 1., 'fully_observed': False, 'source_ids': ['frame:'+str(seconds)]}
    if geometry:
        row['geometry_city'] = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    return dict(row, **extra)


def event(eid='A', samples=None, **extra):
    return dict({'event_id': eid, 'change_type': 'occupancy',
                 'map_layer': {'predicted_layer': 'map_state_event'},
                 'reliability': {'status': 'supported_conflict'},
                 'map_aliases': [{'map_element_id': 'same-lane'}],
                 'coordinate_reference': {'frame': 'City', 'units': 'm'},
                 'samples': samples if samples is not None else [sample(0)]}, **extra)


class StateEventTests(unittest.TestCase):
    def test_observed_state_uses_exact_source_geometry_without_mutation(self):
        e = event(samples=[sample(0), sample(2)])
        e['samples'][1]['geometry_city']['coordinates'][0][1][0] = 2
        before = deepcopy(e)
        layer = build_state_event_layer([e, deepcopy(e)])
        feature = layer['features'][0]
        self.assertEqual(feature['geometry'], e['samples'][1]['geometry_city'])
        self.assertEqual(feature['properties']['state'], 'observed_active')
        self.assertIsNone(feature['properties']['expires_at_timestamp_ns'])
        self.assertEqual(layer['coordinate_reference'], e['coordinate_reference'])
        self.assertEqual(layer['summary']['features'], 1)
        self.assertEqual(e, before)

    def test_end_confirmation_removes_only_identified_event_from_shared_lane(self):
        a = event(samples=[sample(0), sample(2, 'supports_prior', geometry=False, fully_observed=True)])
        b = event('B', [sample(0)])
        layer = build_state_event_layer([a, b])
        self.assertEqual([f['id'] for f in layer['features']], ['B'])
        self.assertEqual(layer['features'][0]['properties']['state'], 'unknown')
        self.assertEqual(layer['cleared_events'][0]['event_id'], 'A')
        self.assertTrue(layer['cleared_events'][0]['clearance_observed'])
        self.assertEqual(layer['cleared_events'][0]['clearance_source_ids'], ['frame:2'])
        self.assertEqual(layer['summary']['cleared'], 1)

    def test_missing_occluded_and_partial_recovery_never_clear(self):
        for last in (sample(2, 'unknown', geometry=False),
                     sample(2, 'supports_prior', geometry=False),
                     sample(2, 'supports_prior', geometry=False, fully_observed=True, quality=0)):
            layer = build_state_event_layer([event(samples=[sample(0), last])])
            p = layer['features'][0]['properties']
            self.assertEqual(p['state'], 'unknown')
            self.assertFalse(p['clearance_observed'])
        p = build_state_event_layer([event()], '2000000000')['features'][0]['properties']
        self.assertEqual(p['state'], 'unknown')
        self.assertEqual(len(p['history']), 1)  # no fabricated missing-frame row

    def test_explicit_ttl_expires_quality_but_does_not_observe_clearance(self):
        e = event(ttl_s=3)
        p = build_state_event_layer([e], '3000000000')['features'][0]['properties']
        self.assertEqual(p['state'], 'stale')
        self.assertEqual(p['observed_quality'], 1.)
        self.assertIsNone(p['current_quality'])
        self.assertFalse(p['clearance_observed'])
        no_ttl = build_state_event_layer([event()], '999999999999999')['features'][0]['properties']
        self.assertEqual(no_ttl['state'], 'unknown')
        self.assertIsNone(no_ttl['expires_at_timestamp_ns'])

    def test_new_observation_refreshes_ttl_and_can_reopen_cleared_state(self):
        rows = [sample(0), sample(2, 'supports_prior', geometry=False, fully_observed=True), sample(5)]
        p = build_state_event_layer([event(samples=rows, ttl_s=3)], '6000000000')['features'][0]['properties']
        self.assertEqual(p['expires_at_timestamp_ns'], '8000000000')
        self.assertEqual(p['last_observed_state'], 'active')
        self.assertFalse(p['clearance_observed'])

    def test_replay_cutoff_does_not_import_future_geometry_or_clearance(self):
        e = event(samples=[sample(5), sample(8, 'supports_prior', geometry=False, fully_observed=True)])
        layer = build_state_event_layer([e], '4000000000')
        self.assertEqual(layer['features'], [])
        self.assertEqual(layer['cleared_events'], [])
        layer = build_state_event_layer([e], '5000000000')
        self.assertEqual(len(layer['features']), 1)
        self.assertEqual(layer['cleared_events'], [])

    def test_contradictory_same_time_evidence_cannot_silently_clear(self):
        rows = [sample(0), sample(0, 'supports_prior', geometry=False, fully_observed=True)]
        p = build_state_event_layer([event(samples=rows)])['features'][0]['properties']
        self.assertEqual(p['state'], 'unknown')
        self.assertFalse(p['clearance_observed'])

    def test_wrong_identity_bad_geometry_and_mixed_coordinates_are_rejected(self):
        for bad in (event(samples=[sample(0, event_id='B')]),
                    event(samples=[sample(0, geometry=False)]), event(ttl_s=-1)):
            with self.assertRaises(ValueError):
                build_state_event_layer([bad])
        with self.assertRaises(ValueError):
            build_state_event_layer([event(), event('B', coordinate_reference={'frame': 'different'})])
        with self.assertRaises(ValueError):
            build_state_event_layer([event(), event('B', coordinate_reference=None)])

    def test_real_annotation_geometry_collections_preserve_all_23_state_events(self):
        from map_update.nuscenes_v2 import evaluate_nuscenes
        from map_update.reliability import integrate_m3
        root = Path(__file__).resolve().parents[1]
        raw = root/'AIE1903 原始资料'
        scene = integrate_m3(raw, root/'external/perception_delivery', raw/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
        events = evaluate_nuscenes(scene)['events']
        for e in events:
            e['map_layer'] = {'predicted_layer': 'map_state_event' if e['change_type'] == 'occupancy' else 'base_map_structure'}
            e['coordinate_reference'] = scene['reference_map']['coordinate_reference']
        layer = build_state_event_layer(events)
        self.assertEqual(layer['summary']['features'], 23)
        self.assertEqual(layer['summary']['cleared'], 0)
        self.assertEqual(layer['summary']['stale'], 0)
        self.assertEqual(layer['summary']['excluded'], 6)
        by_id = {e['event_id']: e for e in events}
        for feature in layer['features']:
            self.assertEqual(feature['geometry'], by_id[feature['id']]['samples'][-1]['geometry_city'])
            self.assertFalse(feature['properties']['clearance_observed'])


if __name__ == '__main__':
    unittest.main()
