from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from map_update.datasets import require_active, run_paths
from map_update.delivery import read_csv
from map_update.evaluation import build_dashboard, controlled_metrics, dashboard_data, evaluate_controlled
from map_update.reliability import attach_m3
from map_update.scene import inspect_scene

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/'AIE1903 原始资料'
RESULTS = RAW/'M3_scene0980_proposal_supplement 2/results'


class DatasetIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = inspect_scene(RAW, ROOT/'external/perception_delivery')
        cls.handoff = json.loads((RESULTS/'real_scene_handoff.json').read_text())
        cls.integrated = attach_m3(cls.scene, cls.handoff)
        cls.rows = read_csv(RESULTS/'controlled_case_decisions.csv')
        cls.controlled = evaluate_controlled(cls.rows, read_csv(RESULTS/'controlled_comparison.csv'))

    def test_argoverse_cannot_enter_nuscenes_adapter(self):
        with TemporaryDirectory() as directory, patch('map_update.evaluation.integrate_m3') as integrate:
            dest = Path(directory)/'must-not-exist'
            with self.assertRaisesRegex(ValueError, 'separate adapter'):
                build_dashboard('missing', 'missing', 'missing', dest, dataset_id='argoverse')
            integrate.assert_not_called()
            self.assertFalse(dest.exists())
        with self.assertRaisesRegex(ValueError, 'separate adapter'):
            inspect_scene('missing', 'missing', dataset_id='argoverse')

    def test_output_namespace_and_path_traversal_guard(self):
        paths = run_paths(ROOT, 'nuscenes', 'review-1')
        self.assertEqual(paths['output'], ROOT/'outputs/nuscenes/scene-0980/review-1')
        with self.assertRaises(ValueError):
            run_paths(ROOT, 'nuscenes', '../../argoverse')
        argo = run_paths(ROOT, 'argoverse', 'rerun-1')
        self.assertEqual(argo['output'], ROOT/'outputs/argoverse/0cfNzxkFwK3x3JB9HpcKIqoRCztSAreo__Winter_2021/rerun-1')
        self.assertNotEqual(argo['delivery_root'], paths['delivery_root'])
        with self.assertRaisesRegex(ValueError, 'Unknown dataset'):
            require_active('unknown')

    def test_real_and_controlled_outputs_have_separate_explicit_identity(self):
        data = dashboard_data(self.integrated, self.controlled)
        self.assertEqual(data['real']['dataset_id'], 'nuscenes')
        self.assertEqual(data['real']['scene_key'], 'nuscenes:scene-0980')
        self.assertEqual(data['controlled']['source_dataset_id'], 'nuscenes')
        self.assertEqual(data['controlled']['dataset_type'], 'controlled_perturbation')
        self.assertTrue(data['datasets']['argoverse']['enabled'])
        self.assertEqual(data['datasets']['argoverse']['status'], 'active_rerun')
        self.assertEqual(len(data['real']['frames']), 41)
        self.assertEqual(len(data['real']['candidates']), 29)
        self.assertEqual(len(data['real']['m3']['review_units']), 7)
        self.assertEqual(data['real']['maintenance']['base_map_updates'], [])

    def test_same_scene_id_does_not_allow_cross_dataset_m3(self):
        handoff = {**self.handoff, 'dataset_id': 'argoverse'}
        with self.assertRaisesRegex(ValueError, 'dataset_identity'):
            attach_m3(self.scene, handoff)

    def test_same_scene_id_does_not_allow_cross_dataset_map_or_metrics(self):
        controlled = {**self.controlled, 'source_dataset_id': 'argoverse'}
        with self.assertRaisesRegex(ValueError, 'same source dataset'):
            dashboard_data(self.integrated, controlled)
        scene = deepcopy(self.integrated)
        scene['reference_map']['dataset_id'] = 'argoverse'
        with self.assertRaisesRegex(ValueError, 'same source dataset'):
            dashboard_data(scene, self.controlled)

    def test_mixed_controlled_rows_rejected(self):
        rows = deepcopy(self.rows)
        rows[-1]['source_dataset_id'] = 'argoverse'
        with self.assertRaisesRegex(ValueError, 'Cross-dataset'):
            controlled_metrics(rows, 'geometric_threshold')


if __name__ == '__main__':
    unittest.main()
