"""Material map actions use the complete available record, not local residuals."""
from copy import deepcopy
import unittest

from map_update.pipeline_v2 import classify_events
from map_update.structural_decision import MaterialityPolicy, geometry_materiality


def line(points):
    return {'type': 'LineString', 'coordinates': points}


def candidate(**overrides):
    event = {'event_id': 'candidate-1', 'change_type': 'geometry_change',
             'semantic_kind': 'structural_marking',
             'map_aliases': [{'map_element_id': 'line-1'}],
             'prior_geometry_city': line([[0., 0.], [10., 0.]]),
             'geometry_city': line([[0., 1.], [10., 1.]]),
             'target_identity_verified': True, 'operation_resolved': True,
             'physical_alignment_verified': True, 'effective_visibility_verified': True,
             'replacement_geometry_verified': True,
             'reliability': {'status': 'supported_conflict'},
             'samples': [{'timestamp_ns': '0', 'visit_id': 'drive', 'frame_id': '0',
                          'observation': 'supports_change', 'quality': 1.}]}
    event.update(overrides)
    return event


def classify(event):
    return classify_events({'events': [event]})['events'][0]


class GeometryMaterialityTests(unittest.TestCase):
    def test_small_offsets_do_not_become_structural_edits_regardless_of_length(self):
        for offset in (.01, .2, .5):
            result = geometry_materiality([[0., 0.], [100., 0.]], [[0., offset], [100., offset]])
            self.assertFalse(result['material'])
            self.assertEqual(result['reason_codes'], ['GEOMETRY_WITHIN_MATERIAL_DISTANCE_TOLERANCE'])

    def test_sustained_displacement_requires_spatial_extent(self):
        for length, expected in ((2.5, False), (3., True), (10., True)):
            result = geometry_materiality([[0., 0.], [length, 0.]], [[0., .8], [length, .8]])
            self.assertEqual(result['material'], expected)
            self.assertAlmostEqual(result['metrics']['max_contiguous_displaced_length_m'], length)

    def test_local_spike_is_not_a_complete_long_displacement(self):
        result = geometry_materiality([[0., 0.], [10., 0.]],
                                     [[0., 0.], [4.9, 0.], [5., 1.], [5.1, 0.], [10., 0.]])
        self.assertFalse(result['material'])
        self.assertGreater(result['metrics']['maximum_offset_m'], .5)
        self.assertLess(result['metrics']['max_contiguous_displaced_length_m'], 3.)

    def test_disconnected_short_offsets_are_not_summed_into_a_change(self):
        result = geometry_materiality([[0., 0.], [20., 0.]],
            [[0., 0.], [2., 0.], [2., 1.], [3., 1.], [3., 0.],
             [10., 0.], [10., 1.], [11., 1.], [11., 0.], [20., 0.]])
        self.assertFalse(result['material'])

    def test_sampling_grid_cannot_hide_intervening_below_tolerance_dips(self):
        prior = [[0., 0.], [10., 0.]]
        oscillating = [[i/4, .6 if i % 2 == 0 else .49] for i in range(41)]
        result = geometry_materiality(prior, oscillating)
        fine = geometry_materiality(prior, oscillating, MaterialityPolicy(sample_spacing_m=.05))
        self.assertFalse(result['material'])
        self.assertFalse(fine['material'])
        self.assertLess(result['metrics']['max_contiguous_displaced_length_m'], 1.)

    def test_direction_and_redundant_sampling_do_not_change_straight_line_result(self):
        original = geometry_materiality([[0., 0.], [10., 0.]], [[0., 1.], [10., 1.]])
        reversed_dense = geometry_materiality([[10., 0.], [5., 0.], [0., 0.]],
                                              [[10., 1.], [8., 1.], [8., 1.], [0., 1.]])
        self.assertEqual(original['material'], reversed_dense['material'])
        self.assertEqual(original['metrics']['max_contiguous_displaced_length_m'],
                         reversed_dense['metrics']['max_contiguous_displaced_length_m'])

    def test_closed_perimeter_joins_the_affected_run_across_start_vertex(self):
        prior = [[0., 0.], [10., 0.], [10., 10.], [0., 10.], [0., 0.]]
        proposed = [[-1., -1.], [2., 0.], [10., 0.], [10., 10.], [0., 10.], [0., 2.], [-1., -1.]]
        result = geometry_materiality(prior, proposed)
        self.assertTrue(result['material'])

    def test_missing_invalid_or_degenerate_geometry_cannot_pass(self):
        for invalid in (None, [], [[0., 0.], [0., 0.]], [[0., 0.], [float('nan'), 0.]],
                        {'type': 'Polygon', 'coordinates': []}):
            result = geometry_materiality(invalid, [[0., 1.], [10., 1.]])
            self.assertFalse(result['material'])
            self.assertIsNone(result['metrics'])

    def test_policy_is_explicit_and_rejects_invalid_units(self):
        for value in (0, -1, True, float('nan')):
            with self.assertRaises(ValueError):
                MaterialityPolicy(distance_tolerance_m=value)
        with self.assertRaises(ValueError):
            MaterialityPolicy(sample_spacing_m=4.)


class ClosedStructuralDecisionTests(unittest.TestCase):
    def test_complete_verified_material_geometry_passes_actual_pipeline_write_gate(self):
        result = classify(candidate())
        self.assertTrue(result['decision']['write_gate']['eligible'])
        self.assertEqual(result['final_structural_decision']['action'], 'update')
        self.assertTrue(result['final_structural_decision']['material_change_supported'])
        self.assertFalse(result['final_structural_decision']['map_patch_executed'])

    def test_write_contract_alone_does_not_promote_a_minor_change(self):
        result = classify(candidate(geometry_city=line([[0., .2], [10., .2]])))
        self.assertTrue(result['decision']['write_gate']['eligible'])
        self.assertFalse(result['decision']['base_map_write_eligible'])
        self.assertEqual(result['final_structural_decision']['action'], 'no_update')
        self.assertIn('GEOMETRY_WITHIN_MATERIAL_DISTANCE_TOLERANCE', result['decision']['reason_codes'])

    def test_local_detection_with_unresolved_target_is_diagnostic_not_structure(self):
        event = candidate(target_identity_verified=False, physical_target_unambiguous=True,
                          operation_resolved=False,
                          structural_hypothesis={'operation': 'unresolved_parallel_marking_or_insertion', 'confirmed': True},
                          same_window_reference_observations=6,
                          model_change_decision={'detected': True, 'scope': 'local_discrepancy'})
        result = classify(event)
        self.assertEqual(result['final_structural_decision']['action'], 'no_update')
        self.assertTrue(result['model_change_decision']['detected'])
        self.assertTrue(result['raw_structural_hypothesis']['confirmed'])
        self.assertFalse(result['structural_hypothesis']['confirmed'])
        self.assertIn('PRIOR_REFERENCE_STILL_OBSERVED_LOCAL_DIFFERENCE_NOT_RESOLVED_AS_ADDITION',
                      result['final_structural_decision']['reason_codes'])
        self.assertTrue(event['structural_hypothesis']['confirmed'])

    def test_old_reference_still_observed_blocks_movement(self):
        result = classify(candidate(same_window_reference_observations=3))
        self.assertEqual(result['final_structural_decision']['action'], 'no_update')
        self.assertIn('PRIOR_REFERENCE_STILL_OBSERVED_NO_MOVEMENT_ESTABLISHED', result['decision']['reason_codes'])

    def test_verified_addition_is_not_a_displacement_of_old_reference(self):
        event = candidate(change_type='add', map_aliases=[], new_element_id='new-lane', novelty_verified=True,
                          same_window_reference_observations=6, prior_geometry_city=None,
                          geometry_city=line([[0., 0.], [2., 0.]]))
        result = classify(event)
        self.assertTrue(result['decision']['write_gate']['eligible'])
        self.assertEqual(result['final_structural_decision']['action'], 'update')
        self.assertIsNone(result['final_structural_decision']['geometry_materiality'])

    def test_verified_regulated_attribute_change_does_not_require_displacement(self):
        event = candidate(change_type='marking_change', prior_marking_type='DASHED_WHITE',
                          marking_type='SOLID_WHITE', marking_property_verified=True,
                          geometry_city=None, replacement_geometry_verified=False,
                          same_window_reference_observations=6)
        result = classify(event)
        self.assertTrue(result['decision']['write_gate']['eligible'])
        self.assertEqual(result['final_structural_decision']['action'], 'update')

    def test_marking_property_needs_an_actual_before_after_difference(self):
        for prior in (None, 'UNKNOWN', 'SOLID_WHITE'):
            result = classify(candidate(change_type='marking_change', prior_marking_type=prior,
                marking_type='SOLID_WHITE', marking_property_verified=True))
            self.assertEqual(result['final_structural_decision']['action'], 'no_update')

    def test_gate_and_hypothesis_cannot_authorize_different_operations(self):
        result = classify(candidate(change_type='marking_change', marking_type='SOLID_WHITE',
            prior_marking_type='DASHED_WHITE', marking_property_verified=True,
            structural_hypothesis={'operation': 'geometry_change', 'operation_resolved': True}))
        self.assertTrue(result['decision']['write_gate']['eligible'])
        self.assertEqual(result['final_structural_decision']['action'], 'no_update')
        self.assertIn('STRUCTURAL_OPERATION_DIFFERS_FROM_WRITE_CONTRACT', result['decision']['reason_codes'])

    def test_supported_topology_claim_does_not_invent_an_unimplemented_writer(self):
        result = classify(candidate(change_type='topology_change', topology_change_verified=True))
        self.assertTrue(result['final_structural_decision']['material_change_supported'])
        self.assertEqual(result['final_structural_decision']['action'], 'no_update')
        self.assertIn('EDIT_CONTRACT_NOT_SATISFIED_BY_SUPPLIED_RECORD', result['decision']['reason_codes'])

    def test_missing_evidence_is_closed_action_not_ground_truth_negative(self):
        result = classify(candidate(reliability={'status': 'insufficient_geometry'}, samples=[]))
        final = result['final_structural_decision']
        self.assertEqual(final['processing_status'], 'complete')
        self.assertEqual(final['action'], 'no_update')
        self.assertEqual(final['evidence_scope'], 'supplied_recording')
        for key in ('revisit_required', 'manual_review_required', 'decision_is_ground_truth'):
            self.assertFalse(final[key])

    def test_dataset_name_duration_and_truth_metadata_never_force_a_change(self):
        event = candidate(geometry_city=line([[0., .2], [10., .2]]))
        expected = classify(event)['final_structural_decision']
        for dataset in ('nuscenes', 'argoverse'):
            changed = deepcopy(event)
            changed.update(dataset_id=dataset, duration='permanent', official_changed=True,
                           ground_truth={'label': 'confirmed'}, label='structure')
            self.assertEqual(classify(changed)['final_structural_decision'], expected)

    def test_summary_counts_closed_consistent_structure_and_preserves_state_geometry(self):
        structural = candidate(reliability={'status': 'consistent_with_map'})
        state = candidate(event_id='occupancy', change_type='occupancy', semantic_kind='movable_occupancy')
        state['samples'][0]['geometry_city'] = deepcopy(state['geometry_city'])
        result = classify_events({'events': [structural, state]})
        self.assertEqual(result['summary']['closed_without_structural_update'], 1)
        self.assertEqual(result['summary']['final_structural_changes'], 0)
        self.assertEqual(result['summary']['pending_structural_decisions'], 0)
        self.assertEqual(result['summary']['revisit_requests'], 0)
        self.assertEqual(result['summary']['stored_state_events'], 1)
        self.assertEqual(result['state_event_layer']['features'][0]['geometry'], state['geometry_city'])
        self.assertEqual(result['events'][1]['final_structural_decision']['action'], 'state_event')


if __name__ == '__main__':
    unittest.main()
