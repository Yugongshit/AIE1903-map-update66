"""Export checks never turn reference replay or known source gaps into accuracy."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pickle
import shutil
import tempfile
import unittest

try:
    import numpy
    import shapely
except ImportError:
    numpy = shapely = None

from map_update.simulation_bridge import export_simulation_map, feature_sha256, json_value, load_scenario
from map_update.simulation_validation import _metrics, load_detection_metrics, validate_database
from tests.test_simulation_bridge import scenario, pipeline, patch_for, replacement


@unittest.skipIf(numpy is None or shapely is None, 'Optional simulation NumPy/Shapely dependencies are required')
class SimulationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.source.mkdir()
        self.filename = 'sd_example.pkl'
        self.original = scenario()
        self.result = self.root/'result.json'
        self.result.write_text(json.dumps(pipeline()))
        self.write_source()

    def write_source(self):
        (self.source/self.filename).write_bytes(pickle.dumps(self.original, protocol=4))
        (self.source/'dataset_summary.pkl').write_bytes(pickle.dumps({self.filename: self.original['metadata']}))
        (self.source/'dataset_mapping.pkl').write_bytes(pickle.dumps({self.filename: ''}))

    def export(self, changed=False):
        patch = None
        if changed:
            line = {'type': 'ROAD_LINE_SOLID_SINGLE_YELLOW', 'polyline': [[0., 3.], [20., 3.]]}
            patch = patch_for(sha256((self.source/self.filename).read_bytes()).hexdigest(),
                              [replacement(self.original, 'line', line)])
        self.out = export_simulation_map(self.source, self.result, self.root/'export', patch=patch)
        return self.out

    def check_by_name(self, report, name):
        return next(row for row in report['integrity']['checks'] if row['name'] == name)

    def rewrite_after(self, callback):
        scene = load_scenario(self.out/self.filename)
        callback(scene['map_features'])
        manifest = json.loads((self.out/'simulation_manifest.json').read_text())
        digest = feature_sha256(scene['map_features'])
        manifest['provenance']['map_features_sha256'] = digest
        scene['metadata']['map_update_bridge']['map_features_sha256'] = digest
        payload = pickle.dumps(scene, protocol=4)
        (self.out/self.filename).write_bytes(payload)
        (self.out/'dataset_summary.pkl').write_bytes(pickle.dumps({self.filename: scene['metadata']}))
        manifest['exported_scenario_sha256'] = sha256(payload).hexdigest()
        (self.out/'simulation_manifest.json').write_text(json.dumps(manifest))
        for filename in ('map_after.json', 'planning_map.json'):
            value = json.loads((self.out/filename).read_text())
            value['map_features'] = json_value(scene['map_features'])
            (self.out/filename).write_text(json.dumps(value))

    def test_applied_change_is_reconstructed_without_claiming_detector_accuracy(self):
        report = validate_database(self.export(changed=True))
        self.assertTrue(report['integrity']['passed'])
        self.assertEqual(report['integrity']['summary']['applied_feature_operations'], 1)
        self.assertFalse(report['detection']['available'])
        self.assertTrue(report['detection']['displayed_map_contains_controlled_edits'])
        self.assertFalse(report['detection']['controlled_edits_counted_as_model_prediction'])

    def test_corrupt_scenario_file_fails_before_unpickling(self):
        self.export()
        with (self.out/self.filename).open('ab') as stream:
            stream.write(b'corruption')
        report = validate_database(self.out)
        self.assertFalse(report['integrity']['passed'])
        self.assertEqual(len(report['integrity']['checks']), 1)
        self.assertIn('hash mismatch', report['integrity']['checks'][0]['details']['error'])

    def test_valid_hashes_do_not_hide_unlogged_map_change(self):
        self.export()
        self.rewrite_after(lambda features: features['line']['polyline'].__setitem__((0, 1), 7.))
        report = validate_database(self.out)
        self.assertEqual(self.check_by_name(report, '地图、规划与日志绑定')['status'], 'pass')
        self.assertEqual(self.check_by_name(report, '逐项重放修改前后地图')['status'], 'fail')

    def test_new_dangling_topology_is_rejected(self):
        self.export()
        self.rewrite_after(lambda features: features['a']['exit_lanes'].append('missing'))
        report = validate_database(self.out)
        self.assertEqual(self.check_by_name(report, '道路连接关系')['status'], 'fail')

    def test_existing_cropped_reference_is_disclosed_as_warning(self):
        self.original['map_features']['a']['entry_lanes'] = ['cropped-source']
        self.write_source()
        report = validate_database(self.export())
        check = self.check_by_name(report, '道路连接关系')
        self.assertTrue(report['integrity']['passed'])
        self.assertEqual(check['status'], 'warning')
        self.assertEqual(check['details']['preserved_source_issues'], [('a', 'entry_lanes', 'cropped-source', 'missing_lane')])

    def test_invalid_polygon_with_updated_file_hashes_still_fails(self):
        self.export()
        self.rewrite_after(lambda features: features['a'].__setitem__('polygon', [[0, 0], [10, 3], [0, 3], [10, 0], [0, 0]]))
        report = validate_database(self.out)
        self.assertEqual(self.check_by_name(report, '几何有效性与道路覆盖')['status'], 'fail')

    def test_bus_lane_cannot_advertise_normal_vehicle_access(self):
        self.export()
        def mutate(features):
            features['a'].update(source_lane_type='BUS', vehicle_access=['vehicle'])
        self.rewrite_after(mutate)
        report = validate_database(self.out)
        self.assertEqual(self.check_by_name(report, '公交与自行车道通行属性')['status'], 'fail')

    def test_reference_replay_may_not_be_reported_as_model_prediction(self):
        self.export()
        manifest = json.loads((self.out/'simulation_manifest.json').read_text())
        manifest['provenance'].update(update_source='reference_update', model_prediction=True,
                                      reference_patch_log_sha256=feature_sha256([]))
        (self.out/'simulation_manifest.json').write_text(json.dumps(manifest))
        (self.out/'reference_patch_log.json').write_text('[]')
        report = validate_database(self.out)
        check = self.check_by_name(report, '逐项重放修改前后地图')
        self.assertEqual(check['status'], 'fail')
        self.assertIn('claimed as model predictions', check['details']['error'])

    def test_cross_scene_evaluation_is_rejected(self):
        report = validate_database(self.export(), detection_metrics={'available': True, 'scene_id': 'another-scene'})
        self.assertFalse(report['integrity']['passed'])
        self.assertIn('scene mismatch', self.check_by_name(report, '场景与坐标身份')['details']['error'])

    def test_upstream_unavailable_height_statistic_is_not_fake_geometry(self):
        self.original['metadata']['number_summary'] = {'map_height_diff': float('-inf')}
        self.write_source()
        report = validate_database(self.export())
        self.assertTrue(report['integrity']['passed'])

    def test_portable_evaluation_recomputes_scores_and_keeps_scopes_separate(self):
        root = Path(__file__).resolve().parents[1]
        path = root/'demo/simulation/validation/detection_metrics.json'
        if not path.exists():
            self.skipTest('Portable evaluation data absent')
        payload = json.loads(path.read_text())
        scene = payload['datasets']['argoverse']['scene_id']
        report = load_detection_metrics(root, 'argoverse', scene,
                                        pipeline_sha256=payload['provenance']['pipeline_result_sha256'])
        self.assertEqual(report['metrics']['true_positives'], 9)
        self.assertEqual(report['metrics']['false_positives'], 5)
        self.assertEqual(report['metrics']['false_negatives'], 22)
        self.assertEqual(report['final_structure_metrics']['predicted_positives'], 0)
        self.assertEqual(report['reference_updates_available'], 93)
        self.assertEqual(report['final_structure_metrics']['recall'], 0.)
        with self.assertRaisesRegex(ValueError, 'pipeline run'):
            load_detection_metrics(root, 'argoverse', scene, pipeline_sha256='different-run')

    def test_empty_predictions_have_undefined_precision_not_one_hundred_percent(self):
        metrics = _metrics([{'id': 'changed', 'predicted': False, 'truth': True}])
        self.assertIsNone(metrics['precision'])
        self.assertEqual(metrics['recall'], 0.)
        self.assertEqual(metrics['f1'], 0.)
        with self.assertRaisesRegex(ValueError, 'Duplicate evaluation'):
            _metrics([{'id': 'same', 'predicted': False, 'truth': True}]*2)

    def test_rehashed_state_observations_still_require_exact_clock_pose_and_track_identity(self):
        source = Path(__file__).resolve().parents[1]/'demo/simulation/nuscenes-states'
        if not (source/'state_source.json').is_file():
            self.skipTest('Portable nuScenes state fixture absent')
        target = self.root/'real-state-export'
        shutil.copytree(source, target)
        original = json.loads((source/'state_source.json').read_text())
        manifest_original = json.loads((source/'simulation_manifest.json').read_text())
        scene_original = load_scenario(source/manifest_original['scenario_file'])
        for mode in ('clock', 'footprint', 'identity'):
            with self.subTest(mode=mode):
                bundle = deepcopy(original)
                sample = next(row for event in bundle['events'] for row in event['samples']
                              if row.get('observation') == 'supports_change' and row.get('instance_tokens'))
                token = sample['instance_tokens'][0]
                if mode == 'clock':
                    sample['timestamp_ns'] = str(int(sample['timestamp_ns'])+1)
                elif mode == 'footprint':
                    sample['geometry_city']['geometries'][0]['coordinates'][0][0][0] += 2.
                else:
                    bundle['object_sources'][token]['matched_track_ids'] = [scene_original['metadata']['sdc_id']]
                manifest, scene = deepcopy(manifest_original), deepcopy(scene_original)
                digest = feature_sha256(bundle)
                manifest['provenance']['state_source_sha256'] = digest
                scene['metadata']['map_update_bridge']['state_source_sha256'] = digest
                serialized = pickle.dumps(scene, protocol=4)
                manifest['exported_scenario_sha256'] = sha256(serialized).hexdigest()
                (target/manifest['scenario_file']).write_bytes(serialized)
                (target/'dataset_summary.pkl').write_bytes(pickle.dumps({manifest['scenario_file']: scene['metadata']}))
                (target/'state_source.json').write_text(json.dumps(bundle))
                (target/'simulation_manifest.json').write_text(json.dumps(manifest))
                report = validate_database(target)
                self.assertFalse(report['integrity']['passed'])
                self.assertEqual(self.check_by_name(report, '状态实例身份与时间对齐')['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
