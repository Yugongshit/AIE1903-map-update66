"""Reject a plausible-looking backdrop or category guide for different inputs."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from map_update.map_context import build_map_contexts
from scripts.build_pipeline_studio import build


ROOT = Path(__file__).resolve().parents[1]


class PresentationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexts = build_map_contexts(ROOT/'AIE1903 原始资料', ROOT/'external/perception_delivery')
        cls.result = {'schema': 'change-pipeline-v3', 'datasets': {}, 'sources': [
            {'role': 'frozen_prediction_input', 'path': 'deliveries/test_M2_v1/prior_map.json',
             'sha256': cls.contexts['argoverse']['provenance']['map_sha256']}]}
        for name, context in cls.contexts.items():
            cls.result['datasets'][name] = {'scene_id': context['scene_id'], 'summary': {
                'final_structural_changes': 0, 'closed_without_structural_update': 0,
                'pending_structural_decisions': 0, 'revisit_requests': 0}, 'events': [],
                'provenance': {'map_sha256': context['provenance']['map_sha256']}}

    @staticmethod
    def closed_decision():
        return {'processing_status': 'complete', 'action': 'no_update',
                'material_change_supported': False, 'structural_scope': True,
                'reason_codes': ['CLOSED_WITHOUT_STRUCTURAL_UPDATE'],
                'revisit_required': False, 'manual_review_required': False,
                'evidence_scope': 'supplied_recording', 'decision_is_ground_truth': False}

    def result_with_closed_candidate(self):
        result = deepcopy(self.result)
        result['datasets']['nuscenes']['events'] = [{
            'event_id': 'structural-1',
            'map_layer': {'predicted_layer': 'base_map_structure'},
            'final_structural_decision': self.closed_decision()}]
        result['datasets']['nuscenes']['summary']['closed_without_structural_update'] = 1
        return result

    def result_with_material_replay(self):
        result = deepcopy(self.result)
        scene = result['datasets']['argoverse']['scene_id']
        replay = {'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
                  'scene_id': scene, 'changes': [{'change_id': 'one'}],
                  'change_layer': {'features': [{'properties': {'change_id': 'one',
                      'confirmed': True, 'confirmation_source': 'reference'}}]},
                  'summary': {'confirmed_changes': 1, 'executed_patches': 1},
                  'updated_map_context': {'scene_id': scene}}
        material = deepcopy(replay)
        material['summary'].update(reference_total_changes=1, accepted_material_changes=1,
                                   skipped_minor_changes=0, revisit_required=False)
        material['materiality_decisions'] = [{'change_id': 'one', 'material': True,
                                             'final_action': 'update_structure'}]
        result['datasets']['argoverse'].update(reference_replay=replay,
                                               material_reference_replay=material)
        return result

    def rejected_before_publication(self, result, message):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'result.json', Path(directory)/'site'
            source.write_text(json.dumps(result))
            with patch('scripts.build_pipeline_studio.build_map_contexts', return_value=self.contexts):
                with self.assertRaisesRegex(ValueError, message):
                    build(source, output)
            self.assertFalse(output.exists())

    def test_same_scene_with_different_reference_map_is_rejected(self):
        result = deepcopy(self.result)
        result['datasets']['nuscenes']['provenance']['map_sha256'] = '0'*64
        self.rejected_before_publication(result, 'map used for prediction')

    def test_a_different_scene_cannot_reuse_the_category_guide(self):
        result = deepcopy(self.result)
        result['datasets']['nuscenes']['scene_id'] = 'different-scene'
        self.rejected_before_publication(result, 'guide and prediction scene differ')

    def test_different_perception_objects_cannot_keep_the_same_category_counts(self):
        result = deepcopy(self.result)
        result['sources'].append({'path': 'processed/objects.csv', 'sha256': '0'*64,
                                  'role': 'nuscenes_prediction_input'})
        self.rejected_before_publication(result, 'guide differs from prediction inputs')

    def test_reference_replay_cannot_borrow_another_scene_or_claim_model_source(self):
        for field, value in [('scene_id', 'different-scene'), ('source', 'model_prediction')]:
            result = deepcopy(self.result)
            replay = {'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
                      'scene_id': result['datasets']['argoverse']['scene_id']}
            replay[field] = value
            result['datasets']['argoverse']['reference_replay'] = replay
            self.rejected_before_publication(result, 'replay provenance or scene identity')

    def test_reference_confirmation_count_must_match_the_actual_change_layer(self):
        result = deepcopy(self.result)
        result['datasets']['argoverse']['reference_replay'] = {
            'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
            'scene_id': result['datasets']['argoverse']['scene_id'],
            'changes': [{'change_id': 'one'}],
            'change_layer': {'features': [{'properties': {'change_id': 'one',
                'confirmed': True, 'confirmation_source': 'reference'}}]},
            'summary': {'confirmed_changes': 98, 'executed_patches': 98},
        }
        self.rejected_before_publication(result, 'changes, layer and execution counts disagree')

    def test_missing_or_pending_final_decisions_cannot_be_published(self):
        result = self.result_with_closed_candidate()
        del result['datasets']['nuscenes']['events'][0]['final_structural_decision']
        self.rejected_before_publication(result, 'requires a completed recording-only map decision')
        for field, value in [('processing_status', 'pending'), ('action', 'uncertain'),
                             ('revisit_required', True), ('manual_review_required', True),
                             ('decision_is_ground_truth', True), ('evidence_scope', 'future_visit')]:
            with self.subTest(field=field):
                result = self.result_with_closed_candidate()
                result['datasets']['nuscenes']['events'][0]['final_structural_decision'][field] = value
                self.rejected_before_publication(result, 'requires a completed recording-only map decision')

    def test_a_raw_detection_cannot_be_published_as_a_material_update(self):
        result = self.result_with_closed_candidate()
        event = result['datasets']['nuscenes']['events'][0]
        event['model_change_decision'] = {'detected': True}
        event['final_structural_decision']['action'] = 'update'
        self.rejected_before_publication(result, 'requires a completed recording-only map decision')

    def test_final_action_summary_must_match_closed_candidate_actions(self):
        for field, value in [('closed_without_structural_update', 0), ('final_structural_changes', 1),
                             ('pending_structural_decisions', 1), ('revisit_requests', 1)]:
            with self.subTest(field=field):
                result = self.result_with_closed_candidate()
                result['datasets']['nuscenes']['summary'][field] = value
                self.rejected_before_publication(result, 'Final decision counts disagree')

    def test_full_reference_audit_cannot_replace_the_material_map_view(self):
        result = self.result_with_material_replay()
        del result['datasets']['argoverse']['material_reference_replay']
        self.rejected_before_publication(result, 'requires a materiality-filtered reference replay')

    def test_material_selection_must_preserve_full_audit_identity_and_action(self):
        for field, value in [('change_id', 'unrelated-object'), ('material', False),
                             ('final_action', 'no_update')]:
            with self.subTest(field=field):
                result = self.result_with_material_replay()
                result['datasets']['argoverse']['material_reference_replay']['materiality_decisions'][0][field] = value
                self.rejected_before_publication(result, 'Material reference decisions disagree')
        result = self.result_with_material_replay()
        result['datasets']['argoverse']['material_reference_replay']['summary']['skipped_minor_changes'] = 1
        self.rejected_before_publication(result, 'Material reference decisions disagree')

    def test_material_reference_replay_cannot_borrow_another_scene_or_claim_model_source(self):
        for field, value in [('scene_id', 'different-scene'), ('source', 'model_prediction')]:
            with self.subTest(field=field):
                result = self.result_with_material_replay()
                result['datasets']['argoverse']['material_reference_replay'][field] = value
                self.rejected_before_publication(result, 'replay provenance or scene identity')

    def test_nuscenes_status_audit_cannot_borrow_another_scene_or_changed_source(self):
        result = deepcopy(self.result)
        result['evaluation'] = {'nuscenes': {'status_audit': {'scene_id': 'different-scene'}}}
        self.rejected_before_publication(result, 'status audit belongs to a different scene')
        result['evaluation']['nuscenes']['status_audit'] = {
            'scene_id': result['datasets']['nuscenes']['scene_id'],
            'source_provenance': [{'root': 'raw_root', 'path': 'processed/objects.csv', 'sha256': '0'*64}]}
        self.rejected_before_publication(result, 'status audit source identity differs')

    def test_nuscenes_status_audit_cannot_reuse_all_events_as_the_structure_denominator(self):
        result = deepcopy(self.result)
        result['evaluation'] = {'nuscenes': {'status_audit': {
            'scene_id': result['datasets']['nuscenes']['scene_id'], 'source_provenance': [],
            'semantic_routing': {'total_candidates': 0, 'state_events': 0, 'structural_candidates': 0, 'unassigned': 0},
            'structural_quality': {'total_candidates': 29, 'detected': 0, 'insufficient_evidence': 0, 'geometrically_consistent': 0},
            'structural_write': {'total_candidates': 0, 'eligible': 0, 'executed': 0},
            'candidate_diagnostics': []}}}
        self.rejected_before_publication(result, 'denominator or decisions disagree')

    def test_geometric_agreement_remains_in_the_structural_candidate_denominator(self):
        result = deepcopy(self.result)
        for dataset in result['datasets'].values():
            dataset['state_event_layer'] = {'type': 'FeatureCollection', 'features': []}
        result['datasets']['nuscenes']['events'] = [{
            'event_id': 'structural-1',
            'map_layer': {'predicted_layer': 'not_applicable', 'conditional_layer_prediction': 'base_map_structure'},
            'reliability': {'status': 'consistent_with_map'},
            'model_change_decision': {'detected': False, 'status': 'no_geometric_discrepancy'},
            'decision': {'write_gate': {'eligible': False, 'map_patch_executed': False}},
            'final_structural_decision': self.closed_decision()}]
        result['datasets']['nuscenes']['summary']['closed_without_structural_update'] = 1
        result['evaluation'] = {'nuscenes': {'status_audit': {
            'scene_id': result['datasets']['nuscenes']['scene_id'], 'source_provenance': [],
            'semantic_routing': {'total_candidates': 1, 'state_events': 0, 'structural_candidates': 1, 'unassigned': 0},
            'structural_quality': {'total_candidates': 1, 'detected': 0, 'insufficient_evidence': 0,
                                   'geometrically_consistent': 1, 'insufficient_fraction': 0.},
            'structural_write': {'total_candidates': 1, 'eligible': 0, 'executed': 0},
            'candidate_diagnostics': [{'candidate_id': 'structural-1'}]}}}
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'result.json', Path(directory)/'site'
            source.write_text(json.dumps(result))
            with patch('scripts.build_pipeline_studio.build_map_contexts', return_value=self.contexts):
                build(source, output)
            self.assertTrue((output/'en/index.html').is_file())
