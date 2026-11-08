"""Layer routing concerns the changed subject, not its duration or truth label."""

from copy import deepcopy
import unittest

from map_update.map_layers import classify_map_layer


class MapLayerClassificationTests(unittest.TestCase):
    def test_nuscenes_occupancy_schema_routes_state_without_marking_reliability(self):
        event = {'change_type': 'occupancy', 'semantic_kind': 'movable_occupancy',
                 'evidence': {'categories': ['movable_object.barrier']},
                 'reliability': {'status': 'supported_conflict', 'score': 1.}}
        out = classify_map_layer(event)
        self.assertEqual(out['predicted_layer'], 'map_state_event')
        self.assertEqual(out['observation_reliability']['status'], 'supported_conflict')
        self.assertFalse(out['change_adopted'])
        self.assertFalse(out['base_map_write_eligible'])

    def test_existing_marking_and_argo_schemas_route_structural_hypotheses(self):
        for change in ['line_discrepancy', 'geometry_change']:
            out = classify_map_layer({'semantic_kind': 'structural_marking', 'change_type': change,
                                      'reliability': {'status': 'insufficient_geometry'}})
            self.assertEqual(out['predicted_layer'], 'base_map_structure')
            self.assertTrue(out['conditional_on_change'])
            self.assertEqual(out['observation_reliability']['status'], 'insufficient_geometry')
            self.assertFalse(out['change_adopted'])
            self.assertFalse(out['base_map_write_eligible'])

    def test_operational_effects_remain_state_even_when_long_lived(self):
        for kind in ['congestion', 'vehicle_occupancy', 'construction_closure',
                     'road_closure', 'landslide_impact', 'lane_closure']:
            with self.subTest(kind=kind):
                base = {'change_type': kind}
                expected = classify_map_layer(base)
                self.assertEqual(expected['predicted_layer'], 'map_state_event')
                for duration in ['temporary', 'long_term', 'permanent', 10000000]:
                    self.assertEqual(classify_map_layer({**base, 'duration': duration,
                                                        'predicted_duration': duration, 'persistence': duration}), expected)

    def test_structural_subjects_do_not_need_a_duration_label_to_route(self):
        for change in ['new_road', 'new_building', 'road_geometry_change',
                       'lane_topology_change', 'building_geometry_change']:
            out = classify_map_layer({'change_type': change, 'duration': 'temporary'})
            self.assertEqual(out['predicted_layer'], 'base_map_structure')

    def test_dataset_names_gt_and_duration_labels_do_not_affect_any_output(self):
        event = {'semantic_kind': 'structural_marking', 'change_type': 'line_discrepancy'}
        expected = classify_map_layer(event)
        changed = {**event, 'dataset_id': 'a-new-dataset', 'scene_id': 'different',
                   'ground_truth': 'map_state_event', 'duration_label': 'temporary',
                   'class_belief': {'long_term': 0}, 'duration': 3, 'temporal_label': 'short'}
        self.assertEqual(classify_map_layer(changed), expected)

    def test_explicit_material_and_operational_claims_support_new_semantic_subtypes(self):
        material = classify_map_layer({'material_change': {'kind': 'bridge_deck_geometry'}})
        operational = classify_map_layer({'operational_status': 'flood_access_restriction'})
        self.assertEqual(material['predicted_layer'], 'base_map_structure')
        self.assertEqual(operational['predicted_layer'], 'map_state_event')
        self.assertEqual(material['classification_basis'], 'explicit_typed_claim')
        self.assertEqual(classify_map_layer({'material_change': True})['predicted_layer'], 'base_map_structure')

    def test_mixed_material_damage_and_landslide_impact_require_separate_claims(self):
        out = classify_map_layer({'material_change': 'road_geometry_destroyed',
                                  'operational_status': 'landslide_impact'})
        self.assertEqual(out['predicted_layer'], 'unresolved')
        self.assertEqual(set(out['candidate_layers']), {'base_map_structure', 'map_state_event'})
        self.assertTrue(out['requires_claim_split'])
        self.assertTrue(out['mixed_layers'])

    def test_separate_claims_route_both_layers_without_merging_subjects(self):
        out = classify_map_layer({'semantic_kind': 'movable_occupancy', 'claims': [
            {'claim_id': 'road', 'material_change': 'road_geometry_destroyed'},
            {'claim_id': 'traffic', 'operational_status': 'landslide_impact'}]})
        self.assertEqual(out['predicted_layer'], 'unresolved')
        self.assertEqual(out['classification_status'], 'split_claims_routed')
        self.assertFalse(out['requires_claim_split'])
        self.assertEqual([c['predicted_layer'] for c in out['claims']],
                         ['base_map_structure', 'map_state_event'])
        self.assertTrue(all(not c['change_adopted'] for c in out['claims']))

    def test_conflicting_unsplit_semantics_are_not_resolved_by_arbitrary_precedence(self):
        out = classify_map_layer({'semantic_kind': 'structural_marking',
                                  'source_categories': ['vehicle.car']})
        self.assertEqual(out['predicted_layer'], 'unresolved')
        self.assertTrue(out['requires_claim_split'])

    def test_unknown_subject_is_not_deduced_from_geometry_or_duration(self):
        for event in [{}, {'change_type': 'add'}, {'change_type': 'delete'},
                      {'semantic_kind': 'unknown', 'predicted_duration': 'long_term'},
                      {'geometry_city': {'type': 'LineString', 'coordinates': [[0, 0], [1, 1]]}}]:
            self.assertEqual(classify_map_layer(event)['predicted_layer'], 'unresolved')

    def test_reliability_does_not_change_semantic_routing_or_adopt_any_change(self):
        for status in ['supported_conflict', 'consistent_with_map', 'insufficient_geometry']:
            out = classify_map_layer({'semantic_kind': 'structural_marking',
                                      'reliability': {'status': status}})
            self.assertEqual(out['predicted_layer'], 'base_map_structure')
            self.assertEqual(out['observation_reliability']['status'], status)
            self.assertFalse(out['change_adopted'])
            self.assertFalse(out['observation_reliability']['assessed_by_classifier'])

    def test_confidence_is_honest_ontology_match_not_probability_or_training(self):
        out = classify_map_layer({'change_type': 'occupancy'})
        self.assertIsNone(out['confidence']['value'])
        self.assertFalse(out['confidence']['calibrated'])
        self.assertFalse(out['parameters_learned_from_data'])
        self.assertFalse(out['classification_accuracy_measured'])

    def test_inputs_preserved_and_duplicate_categories_do_not_add_confidence(self):
        event = {'source_categories': ['vehicle.car', 'vehicle.car']}
        before = deepcopy(event)
        out = classify_map_layer(event)
        self.assertEqual(event, before)
        self.assertEqual(out, classify_map_layer({'source_categories': ['vehicle.car']}))

    def test_unresolved_explicit_claim_is_not_dropped(self):
        out = classify_map_layer({'claims': [{'change_type': 'new_building'}, {'semantic_kind': 'unknown'}]})
        self.assertEqual(out['predicted_layer'], 'unresolved')
        self.assertEqual(out['classification_status'], 'partially_routed')
        self.assertEqual(len(out['claims']), 2)

    def test_invalid_explicit_claim_contracts_fail(self):
        for event in [{'claims': []}, {'claims': ['road']},
                      {'claims': [{'claim_id': 'x'}, {'claim_id': 'x'}]},
                      {'claims': [{'claims': [{}]}]}, {'material_change': {'foo': 'bar'}},
                      {'operational_status': 8}, {'source_categories': [False]},
                      {'semantic_kind': []}]:
            with self.subTest(event=event), self.assertRaises(ValueError):
                classify_map_layer(event)


if __name__ == '__main__':
    unittest.main()
