from copy import deepcopy
from pathlib import Path
import unittest

from map_update.nuscenes_v2 import evaluate_nuscenes
from map_update.reliability import integrate_m3


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/'AIE1903 原始资料'


class NuScenesV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = integrate_m3(RAW, ROOT/'external/perception_delivery',
            RAW/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
        cls.result = evaluate_nuscenes(cls.scene)

    def test_all_candidates_reach_map_layer_interface_with_separate_quality(self):
        result = self.result
        self.assertEqual((len(result['events']), len(result['observation_decisions'])), (29, 332))
        self.assertEqual(result['summary']['reliability_statuses'], {'supported_conflict': 24, 'insufficient_geometry': 5})
        event = next(e for e in result['events'] if e['event_id'].endswith('O001'))
        self.assertEqual(event['semantic_kind'], 'movable_occupancy')
        self.assertEqual(event['reliability']['score'], 1.)
        self.assertFalse(event['reliability']['score_is_probability'])
        self.assertEqual(event['reliability']['conflict_kind'], 'obstacle_occupies_map_space')
        self.assertEqual(len(event['samples']), 15)
        original = self.scene['replay'][0]['occupancy'][0]
        self.assertEqual(event['geometry_city']['geometries'], [o['geometry_city'] for o in original['objects']])
        self.assertTrue(all(s['observation'] == 'supports_change' for s in event['samples']))
        self.assertFalse(event['base_map_write_eligible'])

    def test_missing_observations_and_zero_overlap_do_not_clear_a_lane(self):
        scene = deepcopy(self.scene)
        scene['replay'][0]['occupancy'][0]['lane_overlap_area_m2'] = 0.
        output = evaluate_nuscenes(scene)
        event = next(e for e in output['events'] if e['event_id'].endswith('O001'))
        self.assertEqual(event['samples'][0]['observation'], 'unknown')
        self.assertEqual(event['samples'][0]['quality'], 0.)
        self.assertNotIn('15', [s['frame_id'] for s in event['samples']])
        self.assertFalse(event['evidence']['clearance_observed'])
        self.assertTrue(all(s['observation'] != 'supports_prior' and not s['fully_observed'] for e in output['events'] for s in e['samples']))

    def test_nearest_tokens_and_later_matching_window_do_not_resolve_marking(self):
        event = next(e for e in self.result['events'] if e['event_id'] == 'S0980-LD-003')
        self.assertEqual(event['semantic_kind'], 'structural_marking')
        self.assertEqual(event['map_aliases'], [])
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')
        self.assertEqual(event['reliability']['score'], 0.)
        self.assertEqual(event['reliability']['geometry_quality'], 1.)
        self.assertEqual([s['frame_id'] for s in event['samples']], [str(i) for i in range(18, 26)])
        self.assertTrue(all(s['observation'] == 'unknown' for s in event['samples']))
        self.assertTrue(event['conditional_on_change'])

    def test_source_and_legacy_m3_are_preserved_and_labels_are_not_features(self):
        scene = deepcopy(self.scene)
        before = deepcopy(scene)
        evaluate_nuscenes(scene)
        self.assertEqual(scene, before)
        self.assertEqual(self.result['diagnostics']['legacy_m3_statuses'], {'Insufficient Evidence': 7})
        scene['duration_labels'] = {'all_candidates': 'long_term'}
        for candidate in scene['candidates']:
            candidate['duration_truth'] = 'long_term'
        self.assertEqual(evaluate_nuscenes(scene), self.result)

    def test_missing_annotation_backing_cannot_receive_full_quality(self):
        scene = deepcopy(self.scene)
        scene['replay'][0]['occupancy'][0]['objects'][0]['source'] = 'unverified detector'
        event = next(e for e in evaluate_nuscenes(scene)['events'] if e['event_id'].endswith('O001'))
        self.assertEqual(event['samples'][0]['quality'], 0.)
        self.assertIn('ANNOTATION_BACKING_UNVERIFIED', event['reliability']['reason_codes'])

    def test_dashboard_aliases_and_reordered_frames_keep_same_events(self):
        scene = deepcopy(self.scene)
        scene['map'] = scene.pop('reference_map')
        scene['frames'] = scene.pop('replay')
        scene['frames'].reverse()
        output = evaluate_nuscenes(scene)
        self.assertEqual(output['events'], self.result['events'])

    def test_malformed_geometry_and_conflicting_time_are_rejected(self):
        for mutation in ('geometry', 'timestamp'):
            scene = deepcopy(self.scene)
            row = scene['replay'][0]['occupancy'][0]
            if mutation == 'geometry':
                row['objects'][0]['geometry_city']['coordinates'][0][0][0] = float('nan')
            else:
                row['timestamp_ns'] = scene['replay'][40]['timestamp_ns']
            with self.assertRaises(ValueError):
                evaluate_nuscenes(scene)


if __name__ == '__main__':
    unittest.main()
