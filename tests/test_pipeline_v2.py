"""Structure/state classification and structural edit checks are not duration gates."""
from copy import deepcopy
import unittest
from map_update.engine import Policy
from map_update.pipeline_v2 import write_gate

STRUCTURE = {'predicted_layer': 'base_map_structure'}
STATE = {'predicted_layer': 'event_layer'}


def sample(seconds, observation='supports_change', **extra):
    return {'timestamp_ns': str(round(seconds * 10**9)),
            'visit_id': 'one-drive', 'frame_id': 'frame-' + str(seconds),
            'observation': observation, 'quality': 1., **extra}


def candidate(samples=None, **extra):
    return {'change_type': 'geometry_change',
            'map_aliases': [{'map_element_id': 'line-1'}],
            'geometry_city': {'type': 'LineString', 'coordinates': [[0., 1.], [5., 1.]]},
            'physical_alignment_verified': True, 'effective_visibility_verified': True,
            'replacement_geometry_verified': True,
            'reliability': {'status': 'supported_conflict'},
            'samples': samples if samples is not None else [sample(0), sample(.5), sample(1)],
            **extra}


class StructuralWriteGateTests(unittest.TestCase):
    def test_verified_structural_geometry_can_qualify_after_seconds_in_one_visit(self):
        result = write_gate(candidate(), STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['independent_visits'], 1)
        self.assertEqual(result['span_s'], 1)
        self.assertTrue(result['visits_and_span_are_diagnostics_only'])
        self.assertFalse(result['duration_used'])
        self.assertFalse(result['map_patch_executed'])

    def test_duration_label_and_duration_metadata_cannot_select_write_layer(self):
        event = candidate()
        expected = write_gate(event, STRUCTURE)
        event.update(duration='temporary', predicted_duration='temporary', horizon_s=1.,
                     semantic_kind='movable_occupancy', duration_belief={'temporary': .999})
        self.assertEqual(write_gate(event, {**STRUCTURE, 'predicted_duration': 'temporary'}), expected)
        event.update(duration='long_term', predicted_duration='long_term', horizon_s=10**12)
        self.assertEqual(write_gate(event, {**STRUCTURE, 'predicted_duration': 'long_term'}), expected)

    def test_state_event_cannot_edit_structure_even_after_a_year(self):
        event = candidate([sample(0), sample(365 * 86400, visit_id='second-drive')], change_type='occupancy')
        result = write_gate(event, {**STATE, 'predicted_duration': 'long_term'})
        self.assertFalse(result['eligible'])
        self.assertIn('NO_STRUCTURAL_LAYER_PREDICTION', result['reasons'])
        self.assertIn('NO_STRUCTURAL_EDIT_OPERATION', result['reasons'])

    def test_old_revisit_thresholds_have_no_effect_on_structural_eligibility(self):
        result = write_gate(candidate(), STRUCTURE, Policy(min_visits=1000, min_span_s=10**12))
        self.assertTrue(result['eligible'])

    def test_one_verified_positive_is_sufficient_when_other_contracts_pass(self):
        result = write_gate(candidate([sample(0)]), STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 1)

    def test_future_observation_cannot_authorize_earlier_edit(self):
        result = write_gate(candidate([sample(1)], as_of_timestamp_ns='0'), STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 0)
        self.assertIn('NO_CURRENT_POSITIVE_OBSERVATION', result['reasons'])

    def test_explicit_recovery_revokes_previous_positive_support(self):
        event = candidate()
        event['samples'].append(sample(2, 'supports_prior', fully_observed=True))
        result = write_gate(event, STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 0)
        self.assertIn('EXPLICIT_RECOVERY_AFTER_LAST_SUPPORT', result['reasons'])

    def test_observed_reappearance_may_qualify_without_reusing_old_support(self):
        event = candidate()
        event['samples'] += [sample(2, 'supports_prior', fully_observed=True), sample(3)]
        result = write_gate(event, STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 1)
        self.assertEqual(result['span_s'], 0)

    def test_unknown_or_unverified_recovery_does_not_clear(self):
        for recovery in (sample(2, 'unknown'), sample(2, 'supports_prior'),
                         sample(2, 'supports_prior', fully_observed=True, quality=0.)):
            with self.subTest(recovery=recovery):
                event = candidate()
                event['samples'].append(recovery)
                self.assertTrue(write_gate(event, STRUCTURE)['eligible'])

    def test_future_recovery_does_not_retract_current_eligibility(self):
        event = candidate(as_of_timestamp_ns='1000000000')
        event['samples'].append(sample(2, 'supports_prior', fully_observed=True))
        self.assertTrue(write_gate(event, STRUCTURE)['eligible'])

    def test_duplicate_cameras_do_not_multiply_positive_frames(self):
        result = write_gate(candidate([sample(0), sample(0), sample(0, frame_id='camera-two')]), STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 1)

    def test_conflicting_simultaneous_observations_cannot_count_as_positive(self):
        event = candidate([sample(0), sample(0, 'supports_prior', fully_observed=True)])
        result = write_gate(event, STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 0)

    def test_exact_existing_object_is_required(self):
        for aliases in ([], [{'map_element_id': 'A'}, {'map_element_id': 'B'}],
                        [{}], [{'map_element_id': None}], [{'map_element_id': ''}]):
            with self.subTest(aliases=aliases):
                result = write_gate(candidate(map_aliases=aliases), STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertIn('EXACT_EDIT_OBJECT_UNRESOLVED', result['reasons'])

    def test_structural_classification_does_not_replace_geometry_verification(self):
        for field in ('physical_alignment_verified', 'effective_visibility_verified',
                      'replacement_geometry_verified'):
            with self.subTest(field=field):
                self.assertFalse(write_gate(candidate(**{field: False}), STRUCTURE)['eligible'])
        for geometry in (None, {'type': 'LineString', 'coordinates': [[0., 0.], [0., 0.]]},
                         {'type': 'GeometryCollection', 'geometries': []}):
            with self.subTest(geometry=geometry):
                result = write_gate(candidate(geometry_city=geometry), STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertIn('INVALID_OR_MISSING_EDIT_GEOMETRY', result['reasons'])

    def test_unreliable_conflict_is_not_editable(self):
        for status in ('insufficient_geometry', 'consistent_with_map'):
            result = write_gate(candidate(reliability={'status': status}), STRUCTURE)
            self.assertFalse(result['eligible'])
            self.assertIn('OBSERVED_CONFLICT_NOT_SUPPORTED', result['reasons'])

    def test_addition_requires_explicit_new_identity_and_verified_novelty(self):
        event = candidate(change_type='add', map_aliases=[], new_element_id='new-line', novelty_verified=True)
        self.assertTrue(write_gate(event, STRUCTURE)['eligible'])
        for key, value in (('new_element_id', None), ('novelty_verified', False),
                           ('map_aliases', [{'map_element_id': 'existing-line'}])):
            with self.subTest(key=key):
                changed = deepcopy(event)
                changed[key] = value
                self.assertFalse(write_gate(changed, STRUCTURE)['eligible'])

    def test_property_change_requires_an_actual_defined_property(self):
        event = candidate(change_type='marking_change', marking_type='solid_white', marking_property_verified=True)
        self.assertTrue(write_gate(event, STRUCTURE)['eligible'])
        for value in (None, '', ' ', 'unknown', ' UNKNOWN ', 'NONE', 1, True, []):
            with self.subTest(value=value):
                result = write_gate({**event, 'marking_type': value}, STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertIn('VERIFIED_MARKING_PROPERTY_UNAVAILABLE', result['reasons'])

    def test_verified_property_edit_does_not_require_replacement_geometry(self):
        event = candidate(change_type='marking_change', marking_type='solid_white',
                          marking_property_verified=True, replacement_geometry_verified=False,
                          geometry_city=None)
        result = write_gate(event, STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['edit_scope'], 'marking_attribute_only')
        self.assertIn('marking_property_verified', result['requirements'])
        self.assertNotIn('replacement_geometry_verified', result['requirements'])
        self.assertFalse(result['map_patch_executed'])

    def test_property_string_alone_is_not_verified_property_evidence(self):
        event = candidate(change_type='marking_change', marking_type='solid_white')
        for value in (None, False, 1, 'true'):
            with self.subTest(value=value):
                result = write_gate({**event, 'marking_property_verified': value}, STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertIn('MARKING_PROPERTY_EVIDENCE_UNVERIFIED', result['reasons'])

    def test_verified_deletion_uses_full_observation_not_replacement_geometry(self):
        event = candidate([sample(0, fully_observed=True)], change_type='delete',
                          replacement_geometry_verified=False, geometry_city=None)
        result = write_gate(event, STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['fully_observed_delete_frames'], 1)
        self.assertEqual(result['edit_scope'], 'existing_feature_removal')
        self.assertIn('fully_observed_same_target_causal_deletion_support', result['requirements'])
        self.assertNotIn('replacement_geometry_verified', result['requirements'])
        self.assertFalse(result['map_patch_executed'])

    def test_blind_deletion_is_blocked_despite_global_verification_flags(self):
        for row in (sample(0), sample(0, fully_observed=False),
                    sample(0, fully_observed=True, quality=0),
                    sample(0, 'unknown', fully_observed=True)):
            with self.subTest(row=row):
                result = write_gate(candidate([row], change_type='delete'), STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertEqual(result['fully_observed_delete_frames'], 0)
                self.assertIn('DELETION_REQUIRES_FULLY_OBSERVED_SAME_TARGET_SUPPORT', result['reasons'])

    def test_future_or_recovered_full_observation_cannot_authorize_deletion(self):
        rows = [sample(0), sample(2, fully_observed=True)]
        result = write_gate(candidate(rows, change_type='delete', as_of_timestamp_ns='1000000000'), STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 1)
        self.assertEqual(result['fully_observed_delete_frames'], 0)
        rows = [sample(0, fully_observed=True), sample(1, 'supports_prior', fully_observed=True), sample(2)]
        result = write_gate(candidate(rows, change_type='delete'), STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['positive_observation_frames'], 1)
        self.assertEqual(result['fully_observed_delete_frames'], 0)
        rows[-1]['fully_observed'] = True
        self.assertTrue(write_gate(candidate(rows, change_type='delete'), STRUCTURE)['eligible'])

    def test_simultaneous_contradiction_does_not_authorize_deletion(self):
        rows = [sample(0, fully_observed=True), sample(0, 'supports_prior', fully_observed=True)]
        result = write_gate(candidate(rows, change_type='delete'), STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['fully_observed_delete_frames'], 0)

    def test_deletion_full_observation_must_belong_to_the_same_event_and_target(self):
        target = {'map_element_id': 'line-1', 'boundary_side': 'left', 'physical_boundary_id': 'boundary-1'}
        for wrong in ({'event_id': 'different-event'}, {'map_element_id': 'line-2'},
                      {'boundary_side': 'right'}, {'physical_boundary_id': 'boundary-2'}):
            with self.subTest(wrong=wrong):
                row = sample(0, fully_observed=True, **wrong)
                result = write_gate(candidate([row], event_id='event-1', change_type='delete', map_aliases=[target]), STRUCTURE)
                self.assertFalse(result['eligible'])
                self.assertEqual(result['fully_observed_delete_frames'], 0)
        row = sample(0, fully_observed=True, event_id='event-1', **target)
        self.assertTrue(write_gate(candidate([row], event_id='event-1', change_type='delete', map_aliases=[target]), STRUCTURE)['eligible'])

    def test_deletion_camera_duplicates_do_not_multiply_full_observation_frames(self):
        row = sample(0, fully_observed=True)
        result = write_gate(candidate([row, deepcopy(row), {**row, 'frame_id': 'other-camera'}], change_type='delete'), STRUCTURE)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['fully_observed_delete_frames'], 1)

    def test_property_and_deletion_keep_alignment_visibility_and_positive_support_gates(self):
        for operation, extra in [('marking_change', {'marking_type': 'solid_white', 'marking_property_verified': True}),
                                 ('delete', {})]:
            event = candidate([sample(0, fully_observed=True)], change_type=operation, **extra)
            for field in ('physical_alignment_verified', 'effective_visibility_verified'):
                with self.subTest(operation=operation, field=field):
                    self.assertFalse(write_gate({**event, field: False}, STRUCTURE)['eligible'])
            self.assertFalse(write_gate({**event, 'samples': []}, STRUCTURE)['eligible'])

    def test_shared_physical_aliases_need_synchronized_edit_not_fabricated_ambiguity(self):
        aliases = [{'map_element_id': name, 'boundary_side': 'left', 'physical_boundary_id': 'boundary-1'}
                   for name in ('lane-A', 'lane-B')]
        event = candidate(map_aliases=aliases, canonical_boundary_id='boundary-1', physical_boundary_id='boundary-1',
                          physical_target_unambiguous=True, synchronized_alias_edit_verified=True)
        result = write_gate(event, STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertIn('SYNCHRONIZED_ALIAS_EDIT_UNVERIFIED', result['reasons'])
        self.assertNotIn('EXACT_EDIT_OBJECT_UNRESOLVED', result['reasons'])
        self.assertTrue(result['target_identity']['shared_canonical_physical_boundary'])
        self.assertFalse(result['target_identity']['synchronized_alias_edit_verified'])

    def test_disagreeing_alias_identity_still_has_unresolved_edit_target(self):
        aliases = [{'map_element_id': 'A', 'physical_boundary_id': 'boundary-1'},
                   {'map_element_id': 'B', 'physical_boundary_id': 'boundary-2'}]
        result = write_gate(candidate(map_aliases=aliases, canonical_boundary_id='boundary-1'), STRUCTURE)
        self.assertFalse(result['eligible'])
        self.assertIn('EXACT_EDIT_OBJECT_UNRESOLVED', result['reasons'])
        self.assertFalse(result['target_identity']['shared_canonical_physical_boundary'])

    def test_gate_does_not_modify_inputs(self):
        event, layer = candidate(), deepcopy(STRUCTURE)
        before = deepcopy((event, layer))
        write_gate(event, layer)
        self.assertEqual((event, layer), before)


if __name__ == '__main__':
    unittest.main()
