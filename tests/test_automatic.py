"""Finite-data processing records observations without inventing map edits."""

from copy import deepcopy
from pathlib import Path
import unittest

from map_update.automatic import build_automatic_decisions
from map_update.reliability import integrate_m3


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/'AIE1903 原始资料'


class AutomaticRecordedProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = integrate_m3(RAW, ROOT/'external/perception_delivery',
            RAW/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
        cls.output = build_automatic_decisions(cls.scene)

    def test_real_observations_are_completed_without_pending_review(self):
        out = self.output
        self.assertEqual(out['summary'], {'processed': 29, 'event_records': 23,
            'difference_records': 6, 'no_usable_observation': 0,
            'base_map_updates': 0, 'pending_manual_reviews': 0})
        for result in out['candidates'].values():
            self.assertEqual(result['processing_status'], 'complete')
            self.assertFalse(result['manual_review_required'])
            self.assertEqual(result['persistence'], 'unknown')
            self.assertEqual(result['persistence_classifier_status'], 'not_run')
            self.assertEqual(result['map_action'], 'unchanged')
            self.assertFalse(result['base_map_write_eligible'])

    def test_recorded_barrier_is_distinct_from_unknown_persistence_and_line_m3(self):
        result = self.output['candidates']['S0980-OCC-scene-0980-O001']
        self.assertEqual(result['action'], 'event_recorded')
        self.assertEqual(result['observation_status'], 'observed_occupancy')
        self.assertEqual(result['evidence']['categories'], ['movable_object.barrier'])
        self.assertEqual(result['evidence']['recorded_frames'], 15)
        self.assertEqual(result['evidence']['source_support_frames'], 15)
        self.assertEqual(len(result['evidence']['instance_tokens']), 6)
        self.assertIsNone(result['evidence']['source_m3_decision'])
        self.assertEqual(result['evidence']['source_m3_scope'], 'not_applicable_to_occupancy')

    def test_event_layer_preserves_each_source_footprint_and_timestamp(self):
        events = self.output['event_layer']['events']
        self.assertEqual(sum(len(event['observations']) for event in events), 311)
        frames = {frame['frame_index']: frame for frame in self.scene['replay']}
        for event in events:
            self.assertFalse(event['clearance_confirmed'])
            self.assertEqual(event['duration_class'], 'unknown')
            for observation in event['observations']:
                frame = frames[observation['frame_index']]
                source = next(row for row in frame['occupancy'] if row['candidate_id'] == event['candidate_id'])
                self.assertEqual(observation['timestamp_ns'], frame['timestamp_ns'])
                self.assertEqual(observation['objects'], source['objects'])
                self.assertEqual(observation['lane_overlap_area_m2'], source['lane_overlap_area_m2'])

    def test_line_difference_is_exported_even_without_a_verified_edit_target(self):
        cid = 'S0980-LD-003'
        decision = self.output['candidates'][cid]
        self.assertEqual(decision['action'], 'difference_recorded')
        self.assertEqual(decision['evidence']['frame_indices'], list(range(18, 26)))
        self.assertEqual(decision['evidence']['source_m3_decision'], 'Insufficient Evidence')
        self.assertIn('structural_edit_target_unavailable', decision['reasons'])
        difference = next(r for r in self.output['difference_layer']['differences'] if r['candidate_id'] == cid)
        self.assertIsNone(difference['verified_edit_target'])
        for observation in difference['observations']:
            source = next(line for line in self.scene['replay'][observation['frame_index']]['observed_lines']
                          if line['line_id'] == observation['line_id'])
            self.assertEqual(observation['geometry_city'], source['geometry_city'])
            self.assertEqual(observation['source_file'], source['source_file'])

    def test_missing_frame_does_not_create_geometry_or_clearance(self):
        event = next(r for r in self.output['event_layer']['events'] if r['candidate_id'].endswith('O001'))
        self.assertNotIn(15, [r['frame_index'] for r in event['observations']])
        self.assertFalse(event['clearance_confirmed'])
        self.assertEqual(self.output['policy']['missing_frame'], 'not_observed_not_cleared')

    def test_nested_observation_cannot_override_validated_frame_metadata(self):
        future = self.scene['replay'][40]['timestamp_ns']
        for metadata in ({'frame_index': 40, 'timestamp_ns': future},
                         {'timestamp_ns': future}, {'elapsed_s': 19.85}):
            with self.subTest(metadata=metadata):
                scene = deepcopy(self.scene)
                # O001 belongs to frame 0 here, even if its nested row claims
                # a future timestamp or a frame outside its candidate window.
                observation = scene['replay'][0]['occupancy'][0]
                self.assertEqual(observation['candidate_id'], 'S0980-OCC-scene-0980-O001')
                observation.update(metadata)
                with self.assertRaisesRegex(ValueError, 'frame metadata conflicts'):
                    build_automatic_decisions(scene)

    def test_matching_nested_metadata_is_retained_as_enclosing_frame(self):
        scene = deepcopy(self.scene)
        frame = scene['replay'][0]
        frame['occupancy'][0].update({field: frame[field]
            for field in ('frame_index', 'timestamp_ns', 'elapsed_s')})
        self.assertEqual(build_automatic_decisions(scene), self.output)

    def test_last_observed_time_does_not_claim_online_candidate_availability(self):
        result = self.output['candidates']['S0980-OCC-scene-0980-O001']['evidence']
        last_frame = max(result['frame_indices'])
        self.assertEqual(result['last_observed_timestamp_ns'], self.scene['replay'][last_frame]['timestamp_ns'])
        self.assertNotIn('available_at_timestamp_ns', result)
        self.assertEqual(self.output['policy']['temporal_scope'],
                         'completed_offline_artifact_not_online_availability')

    def test_no_geometry_has_closed_unavailable_outcome_not_a_fake_record(self):
        scene = deepcopy(self.scene)
        for frame in scene['replay']:
            frame['occupancy'] = [row for row in frame['occupancy'] if not row['candidate_id'].endswith('O001')]
        output = build_automatic_decisions(scene)
        result = output['candidates']['S0980-OCC-scene-0980-O001']
        self.assertEqual(result['action'], 'no_usable_observation')
        self.assertIsNone(result['target_layer'])
        self.assertFalse(result['manual_review_required'])
        self.assertEqual(output['summary']['event_records'], 22)
        self.assertEqual(output['summary']['no_usable_observation'], 1)

    def test_source_maps_metrics_and_old_diagnostics_are_not_mutated(self):
        scene = deepcopy(self.scene)
        before = deepcopy(scene)
        build_automatic_decisions(scene)
        self.assertEqual(scene, before)
        self.assertTrue(all(unit['decision'] == 'Insufficient Evidence' for unit in scene['m3']['review_units']))

    def test_dashboard_and_integrated_scene_aliases_have_identical_results(self):
        real = deepcopy(self.scene)
        real['map'] = real.pop('reference_map')
        real['frames'] = real.pop('replay')
        self.assertEqual(build_automatic_decisions(real), self.output)

    def test_reordered_frames_produce_same_geometry_layers(self):
        scene = deepcopy(self.scene)
        scene['replay'].reverse()
        output = build_automatic_decisions(scene)
        self.assertEqual(output['event_layer'], self.output['event_layer'])
        self.assertEqual(output['difference_layer'], self.output['difference_layer'])

    def test_invalid_or_cross_candidate_geometry_is_rejected(self):
        for mutation in ('unknown_id', 'wrong_window', 'bad_polygon', 'wrong_target', 'duplicate_frame', 'duplicate_candidate'):
            with self.subTest(mutation=mutation):
                scene = deepcopy(self.scene)
                if mutation == 'unknown_id':
                    scene['replay'][0]['occupancy'][0]['candidate_id'] = 'invented'
                elif mutation == 'wrong_window':
                    scene['candidates'][0]['frame_window'] = [1, 14]
                elif mutation == 'bad_polygon':
                    scene['replay'][0]['occupancy'][0]['objects'][0]['geometry_city']['coordinates'][0][1][0] = float('nan')
                elif mutation == 'wrong_target':
                    scene['replay'][0]['occupancy'][0]['map_element_id'] = 'some-other-road'
                elif mutation == 'duplicate_frame':
                    scene['replay'].append(deepcopy(scene['replay'][0]))
                else:
                    scene['candidates'].append(deepcopy(scene['candidates'][0]))
                with self.assertRaises(ValueError):
                    build_automatic_decisions(scene)

    def test_other_datasets_or_existing_map_writes_cannot_be_relabelled(self):
        for mutation in ('dataset', 'map_dataset', 'structural_write'):
            scene = deepcopy(self.scene)
            if mutation == 'dataset':
                scene['dataset_id'] = 'argoverse'
            elif mutation == 'map_dataset':
                scene['reference_map']['dataset_id'] = 'argoverse'
            else:
                scene['maintenance']['base_map_updates'] = [{'operation': 'geometry_change'}]
            with self.assertRaises(ValueError):
                build_automatic_decisions(scene)


if __name__ == '__main__':
    unittest.main()
