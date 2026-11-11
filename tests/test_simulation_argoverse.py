"""Reference changes modify actual roads; ego/state data keep their own identity."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    import shapely
except ImportError:
    np = None

from map_update.simulation_argoverse import (
    COORDINATE, MAP_COORDINATES, _converted_patch_log, convert_ego_track, convert_map_features,
    export_argoverse_simulation, validate_reference_replay,
)
from map_update.simulation_bridge import feature_sha256, load_scenario


SCENE = 'argoverse-same-scene'


def lane(identifier, start, end, predecessors=None, successors=None):
    return {'id': 'ls-' + str(identifier), 'lane_type': 'VEHICLE', 'is_intersection': False,
            'left_lane_boundary': [{'x': start, 'y': 2.}, {'x': end, 'y': 2.}],
            'right_lane_boundary': [{'x': start, 'y': -2.}, {'x': end, 'y': -2.}],
            'left_lane_mark_type': 'SOLID_WHITE', 'right_lane_mark_type': 'NONE',
            'predecessors': predecessors or [], 'successors': successors or []}


def source_maps():
    prior = {'scene_id': SCENE, 'coordinates': MAP_COORDINATES,
             'role': 'stale_prior_from_official_loader',
             'lane_segments': [lane(1, 0, 10, successors=[2]), lane(2, 10, 20, predecessors=[1])],
             'pedestrian_crossings': []}
    updated = deepcopy(prior)
    updated['role'] = 'trusted_reference_replay_copy_from_delivered_ground_truth'
    updated['lane_segments'][0]['lane_type'] = 'BUS'
    updated['lane_segments'][0]['right_lane_mark_type'] = 'DASHED_WHITE'
    for point in updated['lane_segments'][0]['right_lane_boundary']:
        point['y'] = -1.
    updated['lane_segments'][1]['successors'] = [3]
    updated['lane_segments'].append(lane(3, 20, 30, predecessors=[2]))
    updated['pedestrian_crossings'] = [{'id': 'pc-1', 'points': [[21, -2], [22, -2], [22, 2], [21, 2], [21, -2]]}]
    patches = []
    for layer, key in [('laneSegments', 'lane_segments'), ('pedCrossings', 'pedestrian_crossings')]:
        for after in updated[key]:
            index = next((i for i, old in enumerate(prior[key]) if old['id'] == after['id']), None)
            before = prior[key][index] if index is not None else None
            if before == after:
                continue
            patches.append({'patch_id': 'reference-' + after['id'], 'element_id': after['id'], 'layer': layer,
                            'operation': 'replace' if before else 'add', 'before_index': index,
                            'before_state': deepcopy(before), 'after_state': deepcopy(after),
                            'before_sha256': feature_sha256(before), 'after_sha256': feature_sha256(after),
                            'executed': True, 'execution_target': 'isolated_reference_copy',
                            'confirmation_source': 'reference', 'source': 'delivered_ground_truth'})
    replay = {'schema': 'trusted-reference-map-replay-v1', 'scene_id': SCENE,
              'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
              'prediction_input': False, 'updated_map': updated, 'rollback_map': prior, 'patch_log': patches,
              'summary': {'executed_patches': len(patches)},
              'changes': [{'element_id': patch['element_id'], 'change_id': 'reference:' + patch['element_id'],
                           'change_types': ['added'] if patch['operation'] == 'add' else ['geometry_change']}
                          for patch in patches],
              'version': {'prior_map_sha256': feature_sha256(prior),
                          'rollback_map_sha256': feature_sha256(prior),
                          'updated_map_sha256': feature_sha256(updated)}}
    return prior, updated, patches, replay


def poses():
    frames = []
    for index, (timestamp, x) in enumerate([(1_000_000_000, 1.), (1_099_000_000, 1.99), (1_201_000_000, 3.01)]):
        pose = np.eye(4)
        pose[:3, 3] = [x, 0., 17. + index]
        frames.append({'scene_id': SCENE, 'clip_id': SCENE + '__clip000', 'frame_id': str(index),
                       'timestamp_ns': str(timestamp), 'city_from_ego_at_lidar': pose.tolist()})
    return frames


@unittest.skipIf(np is None, 'NumPy and Shapely are optional simulation export dependencies')
class ArgoverseSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / (SCENE + '_M2_v1')
        (self.package / 'tables').mkdir(parents=True)
        self.prior, self.updated, self.patches, self.replay = source_maps()
        self.pipeline = {'datasets': {'argoverse': {
            'scene_id': SCENE, 'material_reference_replay': self.replay,
            'events': [], 'state_event_layer': {'type': 'FeatureCollection', 'features': []},
        }}, 'sources': []}
        self.result = self.root / 'result.json'
        self.write_json(self.package / 'prior_map.json', self.prior)
        self.write_json(self.package / 'run.json', {'scene_id': SCENE, 'inference_gt_read': False})
        (self.package / 'tables/frames.jsonl').write_text(''.join(json.dumps(frame) + '\n' for frame in poses()))
        for relative in ('prior_map.json', 'run.json', 'tables/frames.jsonl'):
            self.pipeline['sources'].append({'path': 'deliveries/' + self.package.name + '/' + relative,
                                             'sha256': sha256((self.package / relative).read_bytes()).hexdigest()})
        for filename, value in [('argoverse_material_reference_updated_map.json', self.updated),
                                ('argoverse_material_reference_rollback_map.json', self.prior),
                                ('argoverse_material_reference_patch_log.json', self.patches)]:
            self.write_json(self.root / filename, value)
        self.write_json(self.result, self.pipeline)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def test_real_reference_changes_update_roads_and_graph_without_changing_ego(self):
        source_bytes = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        out = export_argoverse_simulation(self.result, self.package, self.root / 'export')
        original_manifest = self.read_json(out / 'original/simulation_manifest.json')
        manifest = self.read_json(out / 'updated/simulation_manifest.json')
        original = load_scenario(out / 'original' / manifest['scenario_file'])
        updated = load_scenario(out / 'updated' / manifest['scenario_file'])
        self.assertEqual(original_manifest['summary']['structural_operations'], 0)
        self.assertEqual(manifest['summary']['reference_structural_operations'], len(self.patches))
        self.assertGreater(manifest['summary']['structural_operations'], len(self.patches))
        self.assertEqual(updated['metadata']['dataset'], 'argoverse')
        self.assertEqual(updated['metadata']['map'], SCENE)
        self.assertFalse(manifest['provenance']['synthetic'])
        self.assertFalse(manifest['provenance']['model_prediction'])
        self.assertEqual(manifest['provenance']['update_source'], 'reference_update')
        self.assertEqual(set(updated['tracks']), {'ego'})
        self.assertEqual(feature_sha256(original['tracks']), feature_sha256(updated['tracks']))
        self.assertEqual(updated['map_features']['ls-1']['vehicle_access'], ['bus'])
        self.assertEqual(original['map_features']['ls-1']['vehicle_access'], ['vehicle', 'bus'])
        self.assertEqual(updated['map_features']['ls-2']['exit_lanes'], ['ls-3'])
        self.assertIn('pc-1', updated['map_features'])
        np.testing.assert_allclose(updated['map_features']['ls-1']['polyline'][:, 1], .5)
        self.assertEqual(updated['map_features']['ls-1:right']['type'], 'ROAD_LINE_BROKEN_SINGLE_WHITE')
        self.assertEqual(updated['map_features']['ls-1']['source_lane_type'], 'BUS')
        self.assertFalse(updated['map_features']['ls-1']['centerline_measured'])
        self.assertEqual(feature_sha256(updated['map_features']), manifest['provenance']['map_features_sha256'])
        log = self.read_json(out / 'updated/map_patch_log.json')
        self.assertEqual({row['source_reference_patch_id'] for row in log}, {row['patch_id'] for row in self.patches})
        self.assertEqual({row['source_element_id'] for row in log}, {row['element_id'] for row in self.patches})
        self.assertEqual(feature_sha256(log), manifest['provenance']['map_patch_log_sha256'])
        self.assertEqual(feature_sha256(self.patches), manifest['provenance']['reference_patch_log_sha256'])
        self.assertEqual(updated['metadata']['map_update_bridge']['map_patch_log_sha256'], feature_sha256(log))
        self.assertEqual(sha256((out / 'updated' / manifest['scenario_file']).read_bytes()).hexdigest(), manifest['exported_scenario_sha256'])
        self.assertEqual([row['distance_to_initial_ego_m'] for row in log], sorted(row['distance_to_initial_ego_m'] for row in log))
        planning = self.read_json(out / 'updated/planning_map.json')
        self.assertEqual(planning['coordinate'], COORDINATE)
        self.assertEqual(planning['state_event_layer']['features'], [])
        self.assertEqual(planning['obstacle_manifest']['objects'], [])
        self.assertEqual(feature_sha256(planning['map_features']), feature_sha256(updated['map_features']))
        for path, content in source_bytes.items():
            self.assertEqual(Path(path).read_bytes(), content)
        with self.assertRaises(FileExistsError):
            export_argoverse_simulation(self.result, self.package, out)

    def test_pose_resampling_retains_city_axes_and_reports_flattening_and_dimensions(self):
        track, times, report = convert_ego_track(poses(), SCENE, ego_dimensions=(4.7, 2., 1.6))
        np.testing.assert_allclose(times, [0, .1, .2])
        np.testing.assert_allclose(track['state']['position'], [[1, 0, 0], [2, 0, 0], [3, 0, 0]])
        np.testing.assert_allclose(track['state']['velocity'], [[10, 0]] * 3)
        self.assertEqual(report['discarded_source_ego_z_range_m'], [17., 19.])
        self.assertFalse(track['metadata']['dimensions_observed'])
        self.assertEqual(report['ego_dimensions_m']['length'], 4.7)
        self.assertLessEqual(report['simulation_duration_s'], report['source_duration_s'])
        self.assertTrue(all(0 <= value <= 1 for value in report['interpolation_fraction']))

    def test_pose_heading_interpolates_across_angle_wrap(self):
        frames = poses()
        for frame, degrees in zip(frames, [179, -179, -177]):
            angle = np.deg2rad(degrees)
            frame['city_from_ego_at_lidar'][0][:2] = [np.cos(angle), -np.sin(angle)]
            frame['city_from_ego_at_lidar'][1][:2] = [np.sin(angle), np.cos(angle)]
        track, _, _ = convert_ego_track(frames, SCENE)
        self.assertTrue(np.all(np.abs(track['state']['heading']) > 3.))

    def test_pose_rejects_foreign_scene_reflection_gaps_and_bad_dimensions(self):
        for mutation in ('foreign', 'reflection', 'gap', 'duplicate', 'clip'):
            frames = poses()
            if mutation == 'foreign':
                frames[0]['scene_id'] = 'nuScenes-singapore'
            elif mutation == 'reflection':
                frames[0]['city_from_ego_at_lidar'][0][0] = -1
            elif mutation == 'gap':
                frames[-1]['timestamp_ns'] = '2000000000'
            elif mutation == 'duplicate':
                frames[-1]['timestamp_ns'] = frames[0]['timestamp_ns']
            else:
                frames[-1]['clip_id'] = SCENE + '__clip001'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                convert_ego_track(frames, SCENE)
        with self.assertRaises(ValueError):
            convert_ego_track(poses(), SCENE, ego_dimensions=(0, 2, 1))

    def test_modified_ego_file_is_not_silently_paired_with_another_map(self):
        (self.package / 'tables/frames.jsonl').write_text('[]\n')
        with self.assertRaisesRegex(ValueError, 'not bound'):
            export_argoverse_simulation(self.result, self.package, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())

    def test_sibling_reference_file_cannot_override_bound_pipeline(self):
        changed = deepcopy(self.updated)
        changed['scene_id'] = 'foreign-city'
        self.write_json(self.root / 'argoverse_material_reference_updated_map.json', changed)
        with self.assertRaisesRegex(ValueError, 'differs from the completed pipeline'):
            export_argoverse_simulation(self.result, self.package, self.root / 'bad')

    def test_reference_validation_rejects_missing_and_forged_operations(self):
        validate_reference_replay(self.prior, self.updated, self.patches, self.replay)
        for mutation in ('missing', 'hash', 'before', 'id', 'source', 'synthetic', 'duplicate'):
            patches = deepcopy(self.patches)
            if mutation == 'missing':
                patches.pop()
            elif mutation == 'hash':
                patches[0]['after_sha256'] = '0' * 64
            elif mutation == 'before':
                patches[0]['before_state']['lane_type'] = 'BIKE'
                patches[0]['before_sha256'] = feature_sha256(patches[0]['before_state'])
            elif mutation == 'id':
                patches[0]['element_id'] = 'ls-123'
            elif mutation == 'source':
                patches[0]['source'] = 'model_prediction'
            elif mutation == 'synthetic':
                patches[0]['synthetic'] = True
            else:
                patches[1] = deepcopy(patches[0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_reference_replay(self.prior, self.updated, patches, self.replay)

    def test_all_changes_are_covered_even_if_changed_file_hash_is_recomputed(self):
        updated = deepcopy(self.updated)
        updated['lane_segments'][0]['is_intersection'] = True
        replay = deepcopy(self.replay)
        replay['version']['updated_map_sha256'] = feature_sha256(updated)
        with self.assertRaisesRegex(ValueError, 'reconstruct'):
            validate_reference_replay(self.prior, updated, self.patches, replay)

    def test_atomic_lane_deletion_updates_its_neighbor_and_all_rendered_features(self):
        prior = deepcopy(self.prior)
        updated = deepcopy(prior)
        updated['role'] = 'trusted_reference_replay_copy_from_delivered_ground_truth'
        removed = updated['lane_segments'].pop()
        updated['lane_segments'][0]['successors'] = []
        patches = []
        for index, before in enumerate(prior['lane_segments']):
            after = updated['lane_segments'][index] if index == 0 else None
            patches.append({'patch_id': 'delete-contract-' + str(index), 'element_id': before['id'],
                            'layer': 'laneSegments', 'operation': 'replace' if after else 'delete',
                            'before_index': index, 'before_state': before, 'after_state': after,
                            'before_sha256': feature_sha256(before), 'after_sha256': feature_sha256(after),
                            'source': 'delivered_ground_truth', 'confirmation_source': 'reference', 'executed': True})
        replay = {'scene_id': SCENE, 'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
                  'prediction_input': False, 'summary': {'executed_patches': 2},
                  'version': {'prior_map_sha256': feature_sha256(prior),
                              'rollback_map_sha256': feature_sha256(prior),
                              'updated_map_sha256': feature_sha256(updated)}}
        validate_reference_replay(prior, updated, patches, replay)
        before_features, _ = convert_map_features(prior)
        after_features, external = convert_map_features(updated)
        converted = _converted_patch_log(before_features, after_features, patches, [])
        self.assertEqual(after_features['ls-1']['exit_lanes'], [])
        self.assertEqual(external, [])
        self.assertEqual({row['feature_id'] for row in converted if row['operation'] == 'delete'},
                         {removed['id'], removed['id'] + ':left', removed['id'] + ':right'})
        self.assertEqual({row['source_reference_patch_id'] for row in converted},
                         {row['patch_id'] for row in patches})

    def test_external_references_are_reported_but_never_invented(self):
        prior = deepcopy(self.prior)
        prior['lane_segments'][-1]['successors'] = [999]
        features, external = convert_map_features(prior)
        self.assertEqual(features['ls-2']['exit_lanes'], [])
        self.assertEqual(features['ls-2']['source_successors'], ['ls-999'])
        self.assertEqual(external[0]['target_id'], 'ls-999')
        self.assertNotIn('ls-999', features)

    def test_map_rejects_asymmetric_graph_unknown_access_and_invalid_geometry(self):
        for mutation in ('topology', 'access', 'polygon', 'zero_length', 'coordinate'):
            prior = deepcopy(self.prior)
            if mutation == 'topology':
                prior['lane_segments'][1]['predecessors'] = []
            elif mutation == 'access':
                prior['lane_segments'][0]['lane_type'] = 'UNKNOWN'
            elif mutation == 'polygon':
                prior['lane_segments'][0]['right_lane_boundary'][-1]['y'] = 3.
            elif mutation == 'zero_length':
                prior['lane_segments'][0]['left_lane_boundary'][1] = prior['lane_segments'][0]['left_lane_boundary'][0]
            else:
                prior['coordinates'] = 'nuScenes_global'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                convert_map_features(prior)

    def test_live_state_layer_is_not_silently_discarded(self):
        self.pipeline['datasets']['argoverse']['state_event_layer']['features'] = [{'id': 'unjoined-state'}]
        self.write_json(self.result, self.pipeline)
        with self.assertRaisesRegex(ValueError, 'unjoined 3D state'):
            export_argoverse_simulation(self.result, self.package, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())


if __name__ == '__main__':
    unittest.main()
