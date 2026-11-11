"""Map changes affect exported simulation geometry and route topology together."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pickle
import tempfile
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from map_update.simulation_bridge import (
    apply_patch, export_simulation_map, feature_sha256, json_value, load_scenario,
)


def lane(start, end, entry=None, exits=None):
    return {'type': 'LANE_SURFACE_STREET', 'polyline': [[start, 0.], [end, 0.]],
            'polygon': [[start, -2.], [end, -2.], [end, 2.], [start, 2.], [start, -2.]],
            'entry_lanes': entry or [], 'exit_lanes': exits or [],
            'left_neighbor': [], 'right_neighbor': []}


def scenario():
    return {'id': 'scene-0980', 'version': 'test', 'length': 2,
            'metadata': {'id': 'scene-0980', 'scenario_id': 'scene-0980', 'dataset': 'nuscenes',
                         'map': 'singapore-onenorth', 'coordinate': 'right-handed', 'sdc_id': 'ego',
                         'ts': np.array([0., .1])},
            'map_features': {'line': {'type': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
                                      'polyline': np.array([[0., 2.], [20., 2.]])},
                             'a': lane(0., 10., exits=['b']), 'b': lane(10., 20., entry=['a'])},
            'tracks': {'ego': {'type': 'VEHICLE', 'state': {'position': np.array([[0., 0., 0.], [1., 0., 0.]]),
                                                          'valid': np.ones(2)}},
                       'barrier-id': {'type': 'TRAFFIC_BARRIER', 'state': {'position': np.array([[5., 1., 0.], [5., 1., 0.]]),
                                                                         'heading': np.zeros(2), 'velocity': np.zeros((2, 2)),
                                                                         'length': np.full((2, 1), 2.), 'width': np.full((2, 1), .5),
                                                                         'height': np.ones((2, 1)),
                                                                         'valid': np.ones(2)}}},
            'dynamic_map_states': {}}


def patch_for(source, operations):
    return {'schema': 'simulation-map-patch-1.0', 'dataset': 'nuscenes', 'scene_id': 'scene-0980',
            'map': 'singapore-onenorth', 'coordinate': 'nuScenes_global', 'units': 'm',
            'base_scenario_sha256': source, 'provenance': {'source': 'controlled_test', 'synthetic': True},
            'operations': operations}


def replacement(data, key, value):
    return {'operation': 'replace', 'feature_id': key, 'before_sha256': feature_sha256(data['map_features'][key]),
            'feature': json_value(value)}


def pipeline():
    from map_update.state_events import build_state_event_layer
    reference = {'frame': 'nuScenes_global', 'city_id': 'singapore-onenorth', 'units': 'm'}
    event = {'event_id': 'O1', 'change_type': 'occupancy', 'coordinate_reference': reference,
             'map_layer': {'predicted_layer': 'map_state_event'},
             'reliability': {'status': 'supported_conflict'}, 'samples': [
                 {'timestamp_ns': '1000000000', 'frame_id': '0', 'visit_id': 'v',
                  'observation': 'supports_change', 'quality': 1.,
                  'source_ids': ['O1', 'instance:barrier-id', 'instance:unmapped-debris'],
                  'geometry_city': {'type': 'Polygon', 'coordinates': [[[4., 0.], [6., 0.], [6., 2.], [4., 2.], [4., 0.]]]}},
                 {'timestamp_ns': '1100000000', 'frame_id': '1', 'visit_id': 'v',
                  'observation': 'unknown', 'quality': 0., 'source_ids': ['O1']} ]}
    return {'schema': 'change-pipeline-v3', 'datasets': {'nuscenes': {
        'scene_id': 'scene-0980', 'events': [event], 'provenance': {'coordinate_reference': reference},
        'state_event_layer': build_state_event_layer([event])}}}


def confirmed_event(operation='geometry_change'):
    from map_update.pipeline_v2 import write_gate
    from map_update.structural_decision import final_structural_decision
    event = {'event_id': 'LD1', 'change_type': operation,
             'coordinate_reference': {'frame': 'nuScenes_global', 'city_id': 'singapore-onenorth', 'units': 'm'},
             'map_aliases': [{'map_element_id': 'lane_divider:line'}],
             'geometry_city': {'type': 'LineString', 'coordinates': [[0., 3.], [20., 3.]]},
             'prior_geometry_city': {'type': 'LineString', 'coordinates': [[0., 2.], [20., 2.]]},
             'physical_alignment_verified': True, 'effective_visibility_verified': True,
             'replacement_geometry_verified': True, 'target_identity_verified': True, 'operation_resolved': True,
             'reliability': {'status': 'supported_conflict'},
             'map_layer': {'predicted_layer': 'base_map_structure'},
             'samples': [{'timestamp_ns': '1000000000', 'observation': 'supports_change', 'quality': 1.,
                          'visit_id': 'v', 'frame_id': '0', 'fully_observed': True}]}
    if operation == 'marking_change':
        event.update(marking_type='solid_white', prior_marking_type='broken_white', marking_property_verified=True)
    if operation == 'add':
        event.update(map_aliases=[], new_element_id='lane_divider:new-line', novelty_verified=True, marking_type='solid_white')
    gate = write_gate(event, event['map_layer'])
    event['decision'] = {'write_gate': gate}
    event['final_structural_decision'] = final_structural_decision(event, event['map_layer'], gate)
    assert event['final_structural_decision']['action'] == 'update'
    return event


@unittest.skipIf(np is None, 'Simulation bridge optional NumPy dependency is not installed')
class SimulationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = scenario()
        self.database = self.root / 'database'
        self.database.mkdir()
        self.filename = 'sd_nuscenes_test_scene-0980.pkl'
        self.source_path = self.database / self.filename
        self.source_path.write_bytes(pickle.dumps(self.data, protocol=4))
        self.source_bytes = self.source_path.read_bytes()
        self.source_hash = sha256(self.source_bytes).hexdigest()
        (self.database / 'dataset_summary.pkl').write_bytes(pickle.dumps({self.filename: self.data['metadata']}))
        (self.database / 'dataset_mapping.pkl').write_bytes(pickle.dumps({self.filename: ''}))
        self.result_path = self.root / 'result.json'
        self.result_path.write_text(json.dumps(pipeline()))

    def tearDown(self):
        self.temporary.cleanup()

    def test_export_replaces_real_feature_and_preserves_tracks_and_original(self):
        feature = {'type': 'ROAD_LINE_SOLID_SINGLE_YELLOW', 'polyline': [[0., 3.], [20., 3.]]}
        patch = patch_for(self.source_hash, [replacement(self.data, 'line', feature)])
        out = export_simulation_map(self.database, self.result_path, self.root / 'out', patch)
        changed = load_scenario(out / self.filename)
        np.testing.assert_array_equal(changed['map_features']['line']['polyline'], feature['polyline'])
        self.assertEqual(changed['map_features']['line']['type'], feature['type'])
        self.assertEqual(feature_sha256(self.data['tracks']), feature_sha256(changed['tracks']))
        self.assertEqual(self.source_bytes, self.source_path.read_bytes())
        manifest = json.loads((out / 'simulation_manifest.json').read_text())
        self.assertEqual(manifest['summary']['structural_operations'], 1)
        self.assertTrue(manifest['provenance']['synthetic'])
        patch_log = json.loads((out / 'map_patch_log.json').read_text())
        self.assertEqual(manifest['provenance']['map_patch_log_sha256'], feature_sha256(patch_log))
        self.assertEqual(changed['metadata']['map_update_bridge']['map_patch_log_sha256'], feature_sha256(patch_log))
        self.assertEqual(load_scenario(out / 'dataset_mapping.pkl'), {self.filename: ''})

    def test_state_events_join_existing_tracks_without_rewriting_observations_or_duplicates(self):
        out = export_simulation_map(self.database, self.result_path, self.root / 'out')
        changed = load_scenario(out / self.filename)
        self.assertEqual(len(changed['tracks']), 2)
        planning = json.loads((out / 'planning_map.json').read_text())
        link = planning['state_track_links'][0]
        self.assertEqual(link['state'], 'unknown')
        self.assertEqual(link['last_seen_timestamp_ns'], '1000000000')
        self.assertEqual(link['existing_track_ids'], ['barrier-id'])
        self.assertEqual(link['unmatched_instance_tokens'], ['unmapped-debris'])
        self.assertFalse(link['new_obstacles_spawned'])
        self.assertEqual(planning['map_features'], json_value(changed['map_features']))

    def test_default_export_persists_obstacles_and_records_separate_track_hashes(self):
        self.data['tracks']['barrier-id']['state']['valid'][1] = 0
        self.data['tracks']['barrier-id']['state']['position'][1] = [7., 4., 0.]
        self.source_path.write_bytes(pickle.dumps(self.data, protocol=4))
        before = self.source_path.read_bytes()
        out = export_simulation_map(self.database, self.result_path, self.root / 'persistent')
        changed = load_scenario(out / self.filename)
        state = changed['tracks']['barrier-id']['state']
        np.testing.assert_array_equal(state['valid'], [1, 1])
        np.testing.assert_array_equal(state['position'], [[5., 1., 0.], [5., 1., 0.]])
        self.assertEqual(before, self.source_path.read_bytes())
        np.testing.assert_array_equal(changed['tracks']['ego']['state']['position'], self.data['tracks']['ego']['state']['position'])
        manifest = json.loads((out / 'simulation_manifest.json').read_text())
        self.assertFalse(manifest['provenance']['tracks_unchanged'])
        self.assertTrue(manifest['provenance']['dynamic_tracks_unchanged'])
        self.assertEqual(manifest['provenance']['source_tracks_sha256'], feature_sha256(self.data['tracks']))
        self.assertEqual(manifest['provenance']['exported_tracks_sha256'], feature_sha256(changed['tracks']))
        obstacles = json.loads((out / 'obstacle_manifest.json').read_text())
        self.assertEqual(obstacles['mode'], 'persistent')
        self.assertEqual(obstacles['summary']['persisted_instances'], 1)
        self.assertFalse(obstacles['policy']['observed_permanence_claimed'])
        self.assertEqual(json.loads((out / 'planning_map.json').read_text())['obstacle_manifest'], obstacles)
        recorded = export_simulation_map(self.database, self.result_path, self.root / 'recorded', obstacle_mode='recorded')
        restored = load_scenario(recorded / self.filename)
        self.assertEqual(feature_sha256(restored['tracks']), feature_sha256(self.data['tracks']))

    def test_lane_transaction_changes_route_graph(self):
        old_b = self.data['map_features']['b']
        new_b = {**deepcopy(old_b), 'exit_lanes': ['c']}
        patch = patch_for(self.source_hash, [
            {'operation': 'add', 'feature_id': 'c', 'feature': lane(20., 30., entry=['b'])},
            replacement(self.data, 'b', new_b),
        ])
        out = export_simulation_map(self.database, self.result_path, self.root / 'out', patch)
        planning = json.loads((out / 'planning_map.json').read_text())
        graph = planning['map_features']
        self.assertEqual(graph['b']['exit_lanes'], ['c'])
        self.assertEqual(graph['c']['entry_lanes'], ['b'])
        self.assertEqual(graph['c']['polyline'], [[20., 0.], [30., 0.]])
        self.assertEqual(planning['map_version'], feature_sha256(load_scenario(out / self.filename)['map_features']))

    def test_lane_deletion_requires_atomic_adjacency_repair(self):
        delete = {'operation': 'delete', 'feature_id': 'b',
                  'before_sha256': feature_sha256(self.data['map_features']['b'])}
        with self.assertRaisesRegex(ValueError, 'topology'):
            apply_patch(self.data, patch_for(self.source_hash, [delete]), self.source_hash)
        new_a = {**deepcopy(self.data['map_features']['a']), 'exit_lanes': []}
        updated, changes, issues = apply_patch(self.data,
            patch_for(self.source_hash, [delete, replacement(self.data, 'a', new_a)]), self.source_hash)
        self.assertNotIn('b', updated['map_features'])
        self.assertEqual(updated['map_features']['a']['exit_lanes'], [])
        self.assertFalse(issues)

    def test_new_lane_with_missing_reciprocal_edge_is_rejected(self):
        patch = patch_for(self.source_hash, [{'operation': 'add', 'feature_id': 'c',
                                             'feature': lane(20., 30., entry=['b'])}])
        with self.assertRaisesRegex(ValueError, 'topology'):
            apply_patch(self.data, patch, self.source_hash)

    def test_partial_lane_polygon_cannot_masquerade_as_route_update(self):
        feature = {'type': 'LANE_SURFACE_STREET', 'polygon': lane(0., 10.)['polygon']}
        patch = patch_for(self.source_hash, [replacement(self.data, 'a', feature)])
        with self.assertRaisesRegex(ValueError, 'complete centerline'):
            apply_patch(self.data, patch, self.source_hash)

    def test_centerline_outside_polygon_is_rejected(self):
        feature = lane(0., 10., exits=['b'])
        feature['polyline'] = [[0., 3.], [10., 3.]]
        patch = patch_for(self.source_hash, [replacement(self.data, 'a', feature)])
        with self.assertRaisesRegex(ValueError, 'outside'):
            apply_patch(self.data, patch, self.source_hash)

    def test_preexisting_crop_gaps_are_reported_but_new_ones_fail(self):
        self.data['map_features']['b']['exit_lanes'] = ['outside-crop']
        feature = {'type': 'ROAD_LINE_SOLID_SINGLE_WHITE', 'polyline': [[0., 2.], [20., 2.]]}
        patch = patch_for(self.source_hash, [replacement(self.data, 'line', feature)])
        _, _, issues = apply_patch(self.data, patch, self.source_hash)
        self.assertEqual(issues, [('b', 'exit_lanes', 'outside-crop', 'missing_lane')])

    def test_wrong_identity_units_or_hash_refuses_export_without_output(self):
        feature = {'type': 'ROAD_LINE_SOLID_SINGLE_WHITE', 'polyline': [[0., 2.], [20., 2.]]}
        original = patch_for(self.source_hash, [replacement(self.data, 'line', feature)])
        for field, value in [('dataset', 'argoverse'), ('scene_id', 'other'), ('map', 'other-city'),
                             ('coordinate', 'ego_local'), ('units', 'degrees'), ('base_scenario_sha256', 'bad')]:
            with self.subTest(field=field):
                bad = {**original, field: value}
                with self.assertRaises(ValueError):
                    export_simulation_map(self.database, self.result_path, self.root / 'out', bad)
                self.assertFalse((self.root / 'out').exists())
        original['operations'][0]['before_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'before-state'):
            apply_patch(self.data, original, self.source_hash)

    def test_nonfinite_geometry_duplicate_and_unknown_operations_fail(self):
        feature = {'type': 'ROAD_LINE_SOLID_SINGLE_WHITE', 'polyline': [[0., 2.], [20., 2.]]}
        op = replacement(self.data, 'line', feature)
        cases = [[op, op], [{**op, 'operation': 'move'}],
                 [{**op, 'feature': {**feature, 'polyline': [[0., 0.], [float('nan'), 1.]]}}]]
        for operations in cases:
            with self.subTest(operations=operations):
                with self.assertRaises(ValueError):
                    apply_patch(self.data, patch_for(self.source_hash, operations), self.source_hash)

    def test_pipeline_foreign_coordinates_and_unexported_updates_fail(self):
        for mutation in ('coordinates', 'pending'):
            value = pipeline()
            data = value['datasets']['nuscenes']
            if mutation == 'coordinates':
                data['state_event_layer']['coordinate_reference']['city_id'] = 'other'
            else:
                data['events'] = [{'event_id': 'LD1', 'final_structural_decision': {'action': 'update'}}]
            self.result_path.write_text(json.dumps(value))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                export_simulation_map(self.database, self.result_path, self.root / 'out')

    def test_source_already_centralized_is_rejected(self):
        self.data['metadata']['old_origin_in_current_coordinate'] = [0., 0.]
        with self.assertRaisesRegex(ValueError, 'already centralized'):
            apply_patch(self.data, None, self.source_hash)

    def test_mapping_cannot_escape_database_root(self):
        (self.database / 'dataset_mapping.pkl').write_bytes(pickle.dumps({self.filename: '..'}))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            export_simulation_map(self.database, self.result_path, self.root / 'out')

    def test_restricted_unpickler_rejects_executable_global(self):
        class Executable:
            def __reduce__(self):
                return eval, ('1+1',)
        path = self.root / 'malicious.pkl'
        path.write_bytes(pickle.dumps(Executable()))
        with self.assertRaisesRegex(ValueError, 'Unsupported pickle global'):
            load_scenario(path)

    def test_confirmed_pipeline_line_operations_are_exported_without_manual_patch(self):
        from map_update.state_events import build_state_event_layer
        for operation in ('geometry_change', 'marking_change', 'add', 'delete'):
            with self.subTest(operation=operation):
                value = pipeline()
                data = value['datasets']['nuscenes']
                event = confirmed_event(operation)
                data['events'].append(event)
                data['state_event_layer'] = build_state_event_layer(data['events'])
                self.result_path.write_text(json.dumps(value))
                out = export_simulation_map(self.database, self.result_path, self.root / operation)
                actual = load_scenario(out / self.filename)['map_features']
                if operation == 'delete':
                    self.assertNotIn('line', actual)
                elif operation == 'add':
                    self.assertIn('new-line', actual)
                elif operation == 'geometry_change':
                    np.testing.assert_array_equal(actual['line']['polyline'], [[0., 3.], [20., 3.]])
                else:
                    self.assertEqual(actual['line']['type'], 'ROAD_LINE_SOLID_SINGLE_WHITE')
                    np.testing.assert_array_equal(actual['line']['polyline'], [[0., 2.], [20., 2.]])
                manifest = json.loads((out / 'simulation_manifest.json').read_text())
                self.assertFalse(manifest['provenance']['synthetic'])
                self.assertEqual(json.loads((out / 'map_patch_log.json').read_text())[0]['source_event_id'], 'LD1')

    def test_linked_patch_must_match_exact_confirmed_event(self):
        from map_update.state_events import build_state_event_layer
        value = pipeline()
        data = value['datasets']['nuscenes']
        event = confirmed_event()
        data['events'].append(event)
        data['state_event_layer'] = build_state_event_layer(data['events'])
        self.result_path.write_text(json.dumps(value))
        feature = {'type': 'ROAD_LINE_BROKEN_SINGLE_WHITE', 'polyline': [[0., 3.], [20., 3.]]}
        op = {**replacement(self.data, 'line', feature), 'source_event_id': 'LD1'}
        good = patch_for(self.source_hash, [op])
        good['provenance'] = {'source': 'pipeline_adapter', 'synthetic': False}
        export_simulation_map(self.database, self.result_path, self.root / 'good', good)
        cases = []
        bad = deepcopy(good)
        bad['operations'][0]['feature']['polyline'][0][1] = 4.
        cases.append(bad)
        bad = deepcopy(good)
        bad['operations'][0]['source_event_id'] = 'O1'
        cases.append(bad)
        bad = deepcopy(good)
        bad['provenance']['synthetic'] = True
        cases.append(bad)
        bad = deepcopy(good)
        bad['operations'][0].pop('source_event_id')
        cases.append(bad)
        for bad in cases:
            with self.subTest(patch=bad), self.assertRaises(ValueError):
                export_simulation_map(self.database, self.result_path, self.root / 'bad', bad)

    def test_false_confirmed_flag_does_not_bypass_event_evidence(self):
        from map_update.state_events import build_state_event_layer
        value = pipeline()
        data = value['datasets']['nuscenes']
        event = confirmed_event()
        event['physical_alignment_verified'] = False
        data['events'].append(event)
        data['state_event_layer'] = build_state_event_layer(data['events'])
        self.result_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'write contract'):
            export_simulation_map(self.database, self.result_path, self.root / 'out')

    def test_state_layer_source_identity_cannot_be_changed_without_observation(self):
        value = pipeline()
        value['datasets']['nuscenes']['state_event_layer']['features'][0]['properties']['source_ids'] = ['O1', 'instance:ego']
        self.result_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'source event observations'):
            export_simulation_map(self.database, self.result_path, self.root / 'out')

    def test_output_cannot_overwrite_existing_or_source(self):
        with self.assertRaises(FileExistsError):
            export_simulation_map(self.database, self.result_path, self.database)
        with self.assertRaisesRegex(ValueError, 'inside'):
            export_simulation_map(self.database, self.result_path, self.database / 'derived')
        out = export_simulation_map(self.database, self.result_path, self.root / 'out')
        with self.assertRaises(FileExistsError):
            export_simulation_map(self.database, self.result_path, out)


if __name__ == '__main__':
    unittest.main()
