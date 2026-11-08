"""Duration beliefs must adapt without fabricating independence or map authority."""

from copy import deepcopy
import math
import unittest

from map_update.persistence import PersistencePolicy, predict_persistence


def sample(seconds, observation='supports_change', quality=1., visit='one', **extra):
    return {'timestamp_ns': str(round(seconds * 1e9)), 'frame_id': str(seconds),
            'visit_id': visit, 'observation': observation, 'quality': quality, **extra}


def event(samples=None, semantic='movable_occupancy', **extra):
    return {'semantic_kind': semantic, 'samples': samples or [], **extra}


class PersistencePredictionTests(unittest.TestCase):
    def test_single_clip_predictions_disclose_semantic_prior_and_assumptions(self):
        for semantic, prediction, temporary in [('movable_occupancy', 'temporary', .8),
                                                 ('structural_marking', 'long_term', .2),
                                                 ('unknown', 'undecided', .5)]:
            out = predict_persistence(event([sample(0)], semantic))
            self.assertEqual(out['predicted_duration'], prediction)
            self.assertAlmostEqual(out['class_belief']['temporary'], temporary)
            self.assertEqual(out['class_prior']['temporary'], temporary)
            self.assertTrue(out['conditional_on_change'])
            self.assertFalse(out['calibrated'])
            self.assertFalse(out['belief_is_calibrated_probability'])
            self.assertFalse(out['parameters_learned_from_data'])
            self.assertFalse(out['base_map_write_eligible'])
            self.assertEqual(out['duration_verification'], 'not_verified_by_this_predictor')

    def test_sparse_and_high_fps_same_block_have_same_final_belief(self):
        sparse = predict_persistence(event([sample(0), sample(20)]))
        dense = predict_persistence(event([sample(i / 10) for i in range(201)]))
        self.assertEqual(dense['class_belief'], sparse['class_belief'])
        self.assertEqual(dense['joint_state_belief'], sparse['joint_state_belief'])
        self.assertEqual(dense['evidence_count'], 1)
        self.assertEqual(dense['independent_visit_count'], 1)
        self.assertTrue(dense['semantic_prior_dominance'])
        self.assertTrue(dense['posterior_trace'][-1]['replaces_within_block_evidence'])

    def test_duplicate_frame_cameras_do_not_multiply_evidence(self):
        rows = [sample(0), sample(31)]
        original = predict_persistence(event(rows))
        duplicated = deepcopy(rows)
        for camera in ['left', 'right', 'front']:
            duplicated.extend([{**row, 'camera': camera} for row in rows])
        self.assertEqual(predict_persistence(event(duplicated)), original)

    def test_simultaneous_different_frame_ids_do_not_multiply_camera_evidence(self):
        rows = [sample(0), sample(31)]
        baseline = predict_persistence(event(rows))
        cameras = rows + [{**row, 'frame_id': row['frame_id'] + ':camera'} for row in rows]
        out = predict_persistence(event(cameras))
        self.assertEqual(out['class_belief'], baseline['class_belief'])
        self.assertEqual(out['joint_state_belief'], baseline['joint_state_belief'])
        self.assertEqual(out['evidence_count'], 2)

    def test_dataset_name_ground_truth_and_unused_metadata_do_not_affect_prediction(self):
        source = event([sample(0), sample(100)])
        changed = deepcopy(source)
        changed.update(dataset_id='made_up_new_dataset', scene_id='other',
                       gt_duration='long_term', ground_truth={'permanent': True})
        for row in changed['samples']:
            row.update(dataset_name='unseen', gt='ignored', label=777)
        self.assertEqual(predict_persistence(source), predict_persistence(changed))
        before = deepcopy(changed)
        predict_persistence(changed)
        self.assertEqual(changed, before)

    def test_three_day_revisits_shift_movable_prior_toward_long_term(self):
        rows = [sample(day * 86400, visit='visit-' + str(day)) for day in range(3)]
        out = predict_persistence(event(rows))
        self.assertEqual(out['predicted_duration'], 'long_term')
        self.assertGreater(out['class_belief']['long_term'], .999)
        self.assertFalse(out['semantic_prior_dominance'])
        self.assertEqual(out['prediction_basis'], 'temporal_evidence')
        self.assertEqual(out['independent_visit_count'], 3)
        self.assertEqual(out['evidence_count'], 3)
        self.assertFalse(out['base_map_write_eligible'])

    def test_many_blocks_or_visit_labels_are_not_independent_revisits(self):
        same_visit = predict_persistence(event([sample(i * 31) for i in range(20)]))
        self.assertEqual(same_visit['evidence_count'], 20)
        self.assertEqual(same_visit['independent_visit_count'], 1)
        many_names = predict_persistence(event([sample(i * 31, visit=str(i)) for i in range(20)]))
        self.assertEqual(many_names['observed_visit_count'], 20)
        self.assertEqual(many_names['independent_visit_count'], 1)

    def test_unknown_observations_decay_active_state_but_do_not_observe_clearance(self):
        before = predict_persistence(event([sample(0)]))
        after = predict_persistence(event([sample(0), sample(10 * 86400, 'unknown')]))
        self.assertFalse(after['clearance_observed'])
        self.assertEqual(after['evidence_count'], 1)
        self.assertAlmostEqual(after['class_belief']['temporary'], before['class_belief']['temporary'])
        self.assertLess(after['active_probability'], before['active_probability'])
        self.assertFalse(after['posterior_trace'][-1]['emission_applied'])

    def test_recovery_requires_explicit_full_observation_and_nonzero_quality(self):
        for recovery in [sample(100, 'supports_prior'),
                         sample(100, 'supports_prior', fully_observed=False),
                         sample(100, 'supports_prior', quality=0, fully_observed=True)]:
            out = predict_persistence(event([sample(0), recovery]))
            self.assertFalse(out['clearance_observed'])
            self.assertEqual(out['evidence_count'], 1)
        out = predict_persistence(event([sample(0), sample(100, 'supports_prior', fully_observed=True)]))
        self.assertTrue(out['clearance_observed'])
        self.assertLess(out['active_probability'], .001)
        self.assertEqual(out['evidence_count'], 2)
        self.assertGreater(out['class_belief']['temporary'], .8)

    def test_recovery_within_same_block_replaces_previous_emission(self):
        out = predict_persistence(event([sample(0), sample(20, 'supports_prior', fully_observed=True)]))
        self.assertTrue(out['clearance_observed'])
        self.assertEqual(out['evidence_count'], 1)
        self.assertLess(out['active_probability'], .001)
        self.assertTrue(out['posterior_trace'][-1]['replaces_within_block_evidence'])

    def test_conflicting_simultaneous_observations_supply_no_directional_evidence(self):
        rows = [sample(0), sample(0, 'supports_prior', fully_observed=True)]
        out = predict_persistence(event(rows))
        self.assertEqual(out['evidence_count'], 0)
        self.assertFalse(out['clearance_observed'])
        self.assertTrue(out['posterior_trace'][0]['conflicting_frame_observations'])

    def test_as_of_snapshots_exclude_future_even_within_a_frame_interval(self):
        rows = [sample(0), sample(5), sample(5.5, 'supports_prior', fully_observed=True),
                sample(86400, visit='tomorrow')]
        for cutoff in ['0', '5250000000', '5500000000', '86400000000000']:
            full = event(rows, as_of_timestamp_ns=cutoff)
            prefix = event([r for r in rows if int(r['timestamp_ns']) <= int(cutoff)],
                           as_of_timestamp_ns=cutoff)
            self.assertEqual(predict_persistence(full), predict_persistence(prefix))
        self.assertFalse(predict_persistence(event(rows, as_of_timestamp_ns='5250000000'))['clearance_observed'])

    def test_each_posterior_trace_is_a_causal_prefix_prediction(self):
        rows = [sample(0), sample(10), sample(20, 'unknown'), sample(31),
                sample(86400, visit='day2')]
        full = predict_persistence(event(rows))
        for index, row in enumerate(rows):
            prefix = predict_persistence(event(rows[:index + 1]))
            self.assertEqual(prefix['posterior_trace'], full['posterior_trace'][:index + 1])
            self.assertEqual(prefix['class_belief'], full['posterior_trace'][index]['class_belief'])

    def test_unknown_marking_is_conditional_prior_not_verified_change(self):
        out = predict_persistence(event([sample(0, 'unknown'), sample(20, 'unknown')],
                                        'structural_marking', reliability={'status': 'insufficient_geometry'}))
        self.assertEqual(out['predicted_duration'], 'long_term')
        self.assertTrue(out['semantic_prior_dominance'])
        self.assertEqual(out['evidence_count'], 0)
        self.assertEqual(out['reliability_status'], 'insufficient_geometry')
        self.assertFalse(out['base_map_write_eligible'])

    def test_forecast_matches_state_weighted_survival_and_probabilities_normalize(self):
        out = predict_persistence(event([sample(0), sample(86400, visit='two')]))
        state = out['joint_state_belief']
        expected = state['temporary_active'] * math.exp(-24) + state['long_term_active'] * math.exp(-1 / 30)
        self.assertAlmostEqual(out['horizon_survival_probability'], expected)
        self.assertLessEqual(out['horizon_survival_probability'], out['active_probability'])
        self.assertAlmostEqual(sum(state.values()), 1.)
        self.assertAlmostEqual(sum(out['class_belief'].values()), 1.)
        for frame in out['posterior_trace']:
            self.assertAlmostEqual(sum(frame['joint_state_belief'].values()), 1.)

    def test_time_without_samples_is_not_an_emission_and_empty_input_is_explicit(self):
        out = predict_persistence(event([sample(0)], as_of_timestamp_ns=str(86400 * 10**9)))
        self.assertEqual(out['evidence_count'], 1)
        self.assertFalse(out['clearance_observed'])
        empty = predict_persistence(event([], 'unknown'))
        self.assertEqual(empty['predicted_duration'], 'undecided')
        self.assertEqual(empty['evidence_count'], 0)
        self.assertEqual(empty['posterior_trace'], [])
        self.assertIsNone(empty['as_of_timestamp_ns'])

    def test_invalid_quality_timestamp_identity_and_policy_fail(self):
        for patch in [{'quality': -1}, {'quality': 2}, {'quality': float('nan')},
                      {'quality': True}, {'timestamp_ns': 0}, {'timestamp_ns': '-1'},
                      {'observation': 'missing'}, {'fully_observed': 'yes'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                predict_persistence(event([{**sample(0), **patch}]))
        with self.assertRaisesRegex(ValueError, 'conflicting timestamps'):
            predict_persistence(event([sample(0), sample(1, frame_id='0')]))
        with self.assertRaisesRegex(ValueError, 'multiple independent visits'):
            predict_persistence(event([sample(0), sample(0, visit='two')]))
        for kwargs in [{'temporary_mean_s': -1}, {'emission_floor': 0},
                       {'movable_temporary_prior': 1}, {'long_term_mean_s': 10},
                       {'evidence_block_s': 1e-12}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PersistencePolicy(**kwargs)


if __name__ == '__main__':
    unittest.main()
