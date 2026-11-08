from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from map_update import map_context as mc
from map_update.scene import inspect_scene


ROOT = Path(__file__).resolve().parents[1]
RAW, DELIVERY = ROOT/'AIE1903 原始资料', ROOT/'external/perception_delivery'


class MapContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexts = mc.build_map_contexts(RAW, DELIVERY)

    def test_nuscenes_backdrop_preserves_all_157_original_features(self):
        context = self.contexts['nuscenes']
        source = inspect_scene(RAW, DELIVERY)['reference_map']
        self.assertEqual(len(context['elements']), 157)
        self.assertEqual(context['coordinate_reference'], source['coordinate_reference'])
        self.assertEqual(context['map_sha256'], source['source_sha256'])
        originals = {e['element_id']: e for e in source['elements']}
        for e in context['elements']:
            self.assertEqual(e['geometry_city'], originals[e['element_id']]['geometry_city'])
            self.assertEqual(e['source_ids'], [e['element_id']])

    def test_argoverse_prior_geometry_and_all_boundary_aliases_are_preserved(self):
        context = self.contexts['argoverse']
        source = json.loads((next((RAW/'deliveries').glob('*_M2_v1'))/'prior_map.json').read_text())
        self.assertEqual(context['summary']['layers'], {'lane': 219, 'lane_boundary': 343, 'ped_crossing': 27})
        self.assertEqual(len(context['elements']), 589)
        lanes = {e['id']: e for e in source['lane_segments']}
        crossings = {e['id']: e for e in source['pedestrian_crossings']}
        aliases = []
        for e in context['elements']:
            if e['layer'] == 'lane_boundary':
                coords = e['geometry_city']['coordinates']
                for alias in e['map_aliases']:
                    lane = lanes[alias['map_element_id']]
                    expected = [[p['x'], p['y']] for p in lane[alias['boundary_side']+'_lane_boundary']]
                    self.assertTrue(coords == expected or coords == expected[::-1])
                    self.assertEqual(alias['marking_type'], lane[alias['boundary_side']+'_lane_mark_type'])
                    aliases.append((alias['map_element_id'], alias['boundary_side']))
            elif e['layer'] == 'ped_crossing':
                self.assertEqual(e['geometry_city']['coordinates'], [crossings[e['element_id']]['points']])
            else:
                lane = lanes[e['element_id']]
                ring = [[p['x'], p['y']] for p in lane['left_lane_boundary']+lane['right_lane_boundary'][::-1]]
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                self.assertEqual(e['geometry_city']['coordinates'], [ring])
        self.assertEqual(set(aliases), {(eid, side) for eid in lanes for side in ('left', 'right')})
        self.assertEqual(len(aliases), 438)

    def test_provenance_uses_source_prior_without_gt_and_keeps_metric_frames(self):
        for key, context in self.contexts.items():
            self.assertEqual(context['dataset_id'], key)
            self.assertEqual(context['coordinate_reference']['units'], 'm')
            self.assertFalse(context['provenance']['gt_current_map_used'])
            for source in context['provenance']['source_files']:
                self.assertNotIn('_M8_', source['path'])
                self.assertNotIn('current_gt', source['path'])
                self.assertEqual(mc._hash(RAW/source['path']), source['sha256'])
        self.assertEqual(self.contexts['argoverse']['provenance']['not_supplied_layers'], ['drivable_area'])
        self.assertEqual(self.contexts['nuscenes']['coordinate_reference']['frame'], 'nuScenes_global')

    def test_repeated_extraction_does_not_mutate_original_inputs_or_other_views(self):
        paths = [RAW/s['path'] for c in self.contexts.values() for s in c['provenance']['source_files']]
        before = {p: mc._hash(p) for p in paths}
        output = mc.build_map_contexts(RAW, DELIVERY)
        original = deepcopy(output)
        output['argoverse']['elements'][0]['geometry_city']['coordinates'][0][0][0] += 1
        self.assertEqual(self.contexts, original)
        self.assertEqual({p: mc._hash(p) for p in paths}, before)

    def test_tampered_argoverse_prior_is_rejected_by_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            source = next((RAW/'deliveries').glob('*_M2_v1'))
            package = raw/'deliveries'/source.name
            package.mkdir(parents=True)
            (package/'file_manifest.json').write_bytes((source/'file_manifest.json').read_bytes())
            (package/'prior_map.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                mc._argoverse(raw)


if __name__ == '__main__':
    unittest.main()
