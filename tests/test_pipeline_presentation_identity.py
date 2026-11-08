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
            cls.result['datasets'][name] = {'scene_id': context['scene_id'], 'summary': {}, 'events': [],
                'provenance': {'map_sha256': context['provenance']['map_sha256']}}

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
