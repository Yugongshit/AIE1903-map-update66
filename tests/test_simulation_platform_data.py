"""Platform scenes share two layers without mixing cities or leaking futures."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from map_update.simulation_bridge import feature_sha256
from map_update.simulation_platform_data import (
    build_snapshot, catalog, load_scene, planning_bundle, scene_database,
)


ROOT = Path(__file__).resolve().parents[1]


class SimulationPlatformDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import shapely  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('Simulation dependencies are not installed')
        cls.nuscenes = load_scene(ROOT, 'nuscenes-scene0980')
        cls.argoverse = load_scene(ROOT, 'argoverse-winter2021')

    def test_catalog_uses_packaged_scene_identities_and_explicit_sources(self):
        items = {row['key']: row for row in catalog(ROOT)}
        self.assertEqual(items['nuscenes-scene0980']['state_instances'], 74)
        self.assertEqual(items['nuscenes-scene0980']['structural_features'], 0)
        argo = items['argoverse-winter2021']
        self.assertEqual((argo['structure_updates'], argo['structural_features']), (93, 170))
        self.assertEqual(argo['source_kind'], 'reference_update')
        self.assertFalse(argo['synthetic'])
        for key in items:
            self.assertNotIn('/outputs/', str(scene_database(ROOT, key)))

    def test_database_resolution_rejects_paths_and_invalid_versions(self):
        for key in ('../../outside', '/tmp/scene', '', None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                scene_database(ROOT, key)
        with self.assertRaises(ValueError):
            scene_database(ROOT, 'nuscenes-scene0980', '../original')

    def test_real_nuscenes_two_layers_use_one_source_clock(self):
        scene = self.nuscenes
        self.assertEqual(scene['frame_count'], 201)
        snapshot = build_snapshot(scene, 137)
        self.assertEqual(snapshot['structures'], [])
        self.assertEqual(snapshot['source_timestamp_ns'], '1537845365896839800')
        self.assertEqual(snapshot['states']['source_timestamp_ns'], snapshot['source_timestamp_ns'])
        self.assertEqual(snapshot['summary']['state_events'], 16)
        self.assertEqual(snapshot['summary']['state_instances'], 61)
        self.assertEqual(snapshot['states']['summary']['missing_model_instances'], 3)

    def test_backward_seek_rebuilds_states_without_future_instances(self):
        last = build_snapshot(self.nuscenes, 200)
        first = build_snapshot(self.nuscenes, 0)
        repeated = build_snapshot(self.nuscenes, 200)
        self.assertEqual(last, repeated)
        self.assertEqual(last['summary']['state_instances'], 74)
        self.assertEqual(first['summary']['state_instances'], 20)
        cutoff = int(first['states']['observation_cutoff_ns'])
        self.assertTrue(all(int(obj['last_observed_timestamp_ns']) <= cutoff for obj in first['states']['objects']))
        visible = {actor['id'] for actor in first['actors']}
        future = {obj['track_id'] for obj in self.nuscenes['obstacle_manifest']['objects'] if obj['first_seen_step'] > 0}
        self.assertFalse(visible & future)

    def test_background_people_are_not_automatically_marked_as_map_changes(self):
        actors = build_snapshot(self.nuscenes, 137)['actors']
        people = [actor for actor in actors if actor['type'] == 'PEDESTRIAN']
        self.assertTrue(people)
        self.assertTrue(all(not actor['state_event_ids'] and not actor['is_map_state_change'] for actor in people))
        marked = [actor for actor in actors if actor['is_map_state_change']]
        self.assertEqual(len(marked), 58)

    def test_original_and_updated_versions_keep_the_same_attached_state(self):
        before = build_snapshot(self.nuscenes, 100, 'original')
        after = build_snapshot(self.nuscenes, 100, 'updated')
        self.assertEqual(before['states'], after['states'])
        self.assertEqual(before['actors'], after['actors'])
        self.assertEqual(before['map_sha256'], after['map_sha256'])
        argo_before = build_snapshot(self.argoverse, 100, 'original')
        argo_after = build_snapshot(self.argoverse, 100, 'updated')
        self.assertNotEqual(argo_before['map_sha256'], argo_after['map_sha256'])
        self.assertEqual(argo_before['actors'], argo_after['actors'])
        self.assertEqual(argo_after['states']['objects'], [])
        self.assertEqual(argo_after['states']['events'], [])

    def test_reference_updates_preserve_attributes_and_all_real_changes(self):
        scene = self.argoverse
        self.assertEqual(len(scene['changes']), 170)
        self.assertEqual(len({item['source_reference_patch_id'] for item in scene['changes']}), 93)
        bus = [item for item in scene['changes'] if (item.get('before') or {}).get('source_lane_type') == 'VEHICLE'
               and (item.get('after') or {}).get('source_lane_type') == 'BUS']
        self.assertEqual(len(bus), 35)
        self.assertTrue(all(item['after']['vehicle_access'] == ['bus'] for item in bus))
        self.assertTrue(all(any(change['field'] == 'source_lane_type' for change in item['attribute_changes']) for item in bus))

    def test_snapshot_uses_lightweight_structure_references(self):
        snapshot = build_snapshot(self.argoverse, 30)
        self.assertEqual(len(snapshot['structures']), 170)
        self.assertTrue(all('before' not in item and 'after' not in item for item in snapshot['structures']))
        self.assertEqual({item['feature_id'] for item in snapshot['structures']},
                         {item['feature_id'] for item in self.argoverse['changes']})

    def test_planning_provenance_follows_selected_map_database(self):
        results = {}
        for version in ('original', 'updated'):
            handoff = planning_bundle(ROOT, 'argoverse-winter2021', 30, version)
            manifest = json.loads((scene_database(ROOT, 'argoverse-winter2021', version)
                                   / 'simulation_manifest.json').read_text())
            self.assertEqual(handoff['map_version'], manifest['provenance']['map_features_sha256'])
            self.assertEqual(handoff['provenance']['scene_export_sha256'], manifest['exported_scenario_sha256'])
            snapshot = build_snapshot(self.argoverse, 30, version)
            self.assertEqual(snapshot['exported_scenario_sha256'], manifest['exported_scenario_sha256'])
            results[version] = handoff['provenance']['scene_export_sha256']
        self.assertNotEqual(results['original'], results['updated'])

    def test_display_surfaces_do_not_mutate_hash_bound_planning_geometry(self):
        scene = self.nuscenes
        self.assertEqual(len(scene['display_polygons']['updated']), 194)
        for identifier in scene['display_polygons']['updated']:
            self.assertNotIn('polygon', scene['maps']['updated'][identifier])
        self.assertEqual(feature_sha256(scene['maps']['updated']), scene['versions']['updated']['map_sha256'])

    def test_planning_export_contains_current_participants_and_no_recorded_route(self):
        handoff = planning_bundle(ROOT, 'nuscenes-scene0980', 0)
        self.assertIsNone(handoff['route'])
        self.assertIsNone(handoff['requested_goal'])
        self.assertNotIn('tracks', handoff)
        self.assertNotIn('state_source', handoff)
        self.assertEqual(handoff['source_timestamp_ns'], '1537845352297764000')
        self.assertEqual(handoff['map_version'], self.nuscenes['versions']['updated']['map_sha256'])
        self.assertEqual(handoff['map_state_snapshot']['summary']['objects'], 20)
        for item in handoff['current_objects']:
            self.assertEqual(len(item['position']), 3)
            self.assertIsInstance(item['heading'], float)
            self.assertNotIn('state', item)
        self.assertTrue(all(not item['is_ego'] for item in handoff['current_objects']))

    def test_invalid_steps_are_rejected_instead_of_silently_clamped(self):
        for step in (-1, 201, True, 1.5, '3'):
            with self.subTest(step=step), self.assertRaises(ValueError):
                build_snapshot(self.nuscenes, step)

    def test_state_instance_cannot_bind_an_unrelated_track(self):
        bad = deepcopy(self.nuscenes)
        first = next(iter(bad['state_source']['object_sources']))
        bad['tracks'][first]['original_id'] = 'wrong-source-instance'
        with self.assertRaisesRegex(ValueError, 'State instance identity differs'):
            build_snapshot(bad, 200)

    def test_artifact_map_and_state_tampering_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'demo/simulation/nuscenes-states'
            shutil.copytree(ROOT / 'demo/simulation/nuscenes-states', directory)
            path = directory / 'state_source.json'
            original = path.read_text()
            data = json.loads(original)
            data['scene_id'] = 'another-scene'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'State timeline differs from its export hash'):
                load_scene(root, 'nuscenes-scene0980')
            path.write_text(original)
            path = directory / 'map_before.json'
            data = json.loads(path.read_text())
            identifier = next(iter(data['map_features']))
            data['map_features'][identifier]['type'] = 'ROAD_LINE_SOLID_SINGLE_YELLOW'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'Map artifact differs'):
                load_scene(root, 'nuscenes-scene0980')

    def test_combined_demo_has_real_states_and_explicit_synthetic_structure(self):
        if not (ROOT / 'demo/simulation/nuscenes-combined/simulation_manifest.json').is_file():
            self.skipTest('Optional combined test scene is not packaged')
        scene = load_scene(ROOT, 'nuscenes-combined-demo')
        self.assertTrue(scene['source']['synthetic'])
        self.assertEqual(scene['source']['kind'], 'controlled_demo')
        snapshot = build_snapshot(scene, 137)
        self.assertEqual(len(snapshot['structures']), 1)
        self.assertEqual(snapshot['summary']['state_instances'], 61)
        self.assertTrue(snapshot['structures'][0]['label'].startswith('DEMO'))
        self.assertEqual(snapshot['states'], build_snapshot(self.nuscenes, 137)['states'])
        self.assertNotEqual(scene['versions']['original']['map_sha256'], scene['versions']['updated']['map_sha256'])


if __name__ == '__main__':
    unittest.main()
