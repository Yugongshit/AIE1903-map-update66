from copy import deepcopy
import csv
import json
from math import pi
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from map_update.scene import footprint
from map_update.state_events import build_state_event_layer
from map_update.state_object_presentation import build_state_object_presentation


def object_row(token='barrier-a', frame='1', **extra):
    return dict({'frame_index': frame, 'instance_token': token, 'annotation_token': token+'-annotation-'+frame,
                 'sample_token': 'sample-'+frame, 'category': 'movable_object.barrier',
                 'x_global_m': '20', 'y_global_m': '30', 'z_global_m': '1.2',
                 'length_m': '0.3', 'width_m': '2.1', 'height_m': '1.05',
                 'yaw_global_deg': '90'}, **extra)


def observation(rows):
    frame = rows[0]['frame_index']
    tokens = [r['instance_token'] for r in rows]
    return {'timestamp_ns': str((int(frame)+1)*1_000_000_000), 'frame_id': frame, 'visit_id': 'visit',
            'observation': 'supports_change', 'quality': 1.,
            'source_ids': ['event', 'frame:'+frame]+['instance:'+token for token in tokens],
            'instance_tokens': tokens,
            'geometry_city': {'type': 'GeometryCollection', 'geometries': [footprint(r) for r in rows]}}


def source_event(rows):
    return {'event_id': 'event', 'change_type': 'occupancy', 'samples': [observation(rows)],
            'map_layer': {'predicted_layer': 'map_state_event'},
            'reliability': {'status': 'supported_conflict'},
            'evidence': {'categories': sorted({r['category'] for r in rows})}}


class StateObjectPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'processed').mkdir()
        self.rows = [object_row(), object_row('cone-a', category='movable_object.trafficcone',
                                            x_global_m='23', length_m='.4', width_m='.4', height_m='.7')]
        self.event = source_event(self.rows)
        self.layer = build_state_event_layer([self.event])
        self.write_sources()

    def write_sources(self, rows=None, frames=None):
        rows = self.rows if rows is None else rows
        frames = frames if frames is not None else [
            {'frame_index': '0', 'sample_token': 'sample-0', 'timestamp_us': '1000000'},
            {'frame_index': '1', 'sample_token': 'sample-1', 'timestamp_us': '2000000'}]
        for name, data in [('objects.csv', rows), ('frames.csv', frames)]:
            with (self.root/'processed'/name).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows(data)

    def build(self):
        return build_state_object_presentation(self.root, [self.event], self.layer)

    def test_exact_source_dimensions_and_orientation_without_input_mutation(self):
        before = deepcopy([self.event, self.layer])
        result = self.build()
        self.assertEqual(result['fallbacks'], [])
        self.assertEqual(result['summary']['objects'], 2)
        barrier, cone = result['objects']
        self.assertEqual(barrier['center_xyz'], [20., 30., 1.2])
        self.assertEqual([barrier[k] for k in ('length_m', 'width_m', 'height_m')], [.3, 2.1, 1.05])
        self.assertAlmostEqual(barrier['yaw_rad'], pi/2)
        self.assertEqual(barrier['height_source'], 'source_annotation')
        self.assertEqual(barrier['semantic_shape'], 'barrier')
        self.assertEqual(cone['semantic_shape'], 'cone')
        self.assertEqual(cone['footprint_index'], 1)
        self.assertEqual(cone['footprint_geometry_city'], self.layer['features'][0]['geometry']['geometries'][1])
        self.assertEqual([self.event, self.layer], before)
        # Display descriptors own their copies of polygons.
        barrier['footprint_geometry_city']['coordinates'][0][0][0] = -999
        self.assertEqual([self.event, self.layer], before)

    def test_latest_retained_frame_not_first_frame_or_repeated_observations(self):
        older = object_row(frame='0', x_global_m='5')
        self.event['samples'].insert(0, observation([older]))
        self.layer = build_state_event_layer([self.event], '3000000000')
        self.write_sources([older]+self.rows)
        result = self.build()
        self.assertEqual(result['summary']['objects'], 2)
        self.assertTrue(all(r['frame_id'] == '1' and r['timestamp_ns'] == '2000000000' for r in result['objects']))
        self.assertTrue(all(r['observation_state'] == 'unknown' for r in result['objects']))
        self.assertEqual(result['objects'][0]['center_xy'], [20., 30.])

    def test_missing_exact_instance_does_not_match_nearby_or_other_frame(self):
        self.write_sources([object_row(token='different-instance'), object_row(frame='0'), self.rows[1]])
        result = self.build()
        self.assertEqual([r['instance_token'] for r in result['objects']], ['cone-a'])
        self.assertEqual(result['fallbacks'][0]['reason_code'], 'OBJECT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')

    def test_duplicate_source_key_is_ambiguous_even_if_values_identical(self):
        self.write_sources(self.rows+[self.rows[0]])
        result = self.build()
        self.assertEqual(result['summary']['objects'], 1)
        self.assertEqual(result['fallbacks'][0]['instance_token'], 'barrier-a')

    def test_timestamp_and_sample_identity_mismatches_fall_back(self):
        self.write_sources(frames=[{'frame_index': '1', 'sample_token': 'sample-1', 'timestamp_us': '2000001'}])
        result = self.build()
        self.assertEqual(result['objects'], [])
        self.assertEqual(result['fallbacks'][0]['reason_code'], 'FRAME_TIMESTAMP_MISMATCH')
        self.write_sources([dict(self.rows[0], sample_token='other-sample'), self.rows[1]])
        self.assertEqual(self.build()['fallbacks'][0]['reason_code'], 'ANNOTATION_SAMPLE_IDENTITY_MISMATCH')

    def test_category_and_footprint_inconsistency_cannot_change_source_shape(self):
        self.write_sources([dict(self.rows[0], category='vehicle.car'), self.rows[1]])
        self.assertEqual(self.build()['fallbacks'][0]['reason_code'], 'OBJECT_CATEGORY_MISMATCH')
        self.write_sources([dict(self.rows[0], x_global_m='20.01'), self.rows[1]])
        self.assertEqual(self.build()['fallbacks'][0]['reason_code'], 'ANNOTATION_FOOTPRINT_MISMATCH')

    def test_instance_order_is_not_recovered_by_nearest_shape(self):
        self.event['samples'][0]['instance_tokens'].reverse()
        result = self.build()
        self.assertEqual(result['objects'], [])
        self.assertEqual(result['fallbacks'][0]['reason_code'], 'RETAINED_OBSERVATION_IDENTITY_MISMATCH')

    def test_missing_height_is_explicit_schematic_not_measured(self):
        self.write_sources([dict(self.rows[0], height_m=''), self.rows[1]])
        record = self.build()['objects'][0]
        self.assertEqual(record['height_source'], 'schematic_default')
        self.assertEqual(record['height_m'], 1.)

    def test_debris_is_generic_and_source_hash_changes_with_table(self):
        old_hashes = self.build()['provenance']['source_files']
        self.rows[0]['category'] = 'movable_object.debris'
        self.event['evidence']['categories'] = ['movable_object.debris', 'movable_object.trafficcone']
        self.write_sources()
        result = self.build()
        self.assertEqual(result['objects'][0]['semantic_shape'], 'other')
        self.assertEqual(result['objects'][0]['category'], 'movable_object.debris')
        self.assertNotEqual(result['provenance']['source_files'], old_hashes)
        self.assertFalse(result['provenance']['sensor_inference_rerun'])
        self.assertFalse(result['provenance']['predictions_modified'])

    def test_missing_source_file_keeps_state_geometry_as_fallback(self):
        (self.root/'processed/objects.csv').unlink()
        result = self.build()
        self.assertEqual(result['objects'], [])
        self.assertEqual(result['fallbacks'][0]['render_mode'], 'source_footprint')
        self.assertEqual(result['provenance']['unavailable_source_files'], ['processed/objects.csv'])

    def test_supplied_frozen_scene_resolves_all_49_distinct_objects(self):
        repo = Path(__file__).resolve().parents[1]
        frozen = repo/'outputs/pipeline-v6/material-structure-20261109/result.json'
        raw = repo/'AIE1903 原始资料'
        if not frozen.is_file() or not (raw/'processed/objects.csv').is_file():
            self.skipTest('Original scene fixture is not available')
        dataset = json.loads(frozen.read_text())['datasets']['nuscenes']
        before = deepcopy(dataset)
        result = build_state_object_presentation(raw, dataset['events'], dataset['state_event_layer'])
        self.assertEqual(result['fallbacks'], [])
        self.assertEqual(result['summary']['state_events'], 23)
        self.assertEqual(result['summary']['objects'], 49)
        self.assertEqual(result['summary']['distinct_instances'], 49)
        self.assertEqual(result['summary']['annotation_height_objects'], 49)
        self.assertEqual(result['summary']['categories'], {'movable_object.barrier': 35,
                         'movable_object.trafficcone': 11, 'movable_object.debris': 3})
        self.assertEqual(dataset, before)


if __name__ == '__main__':
    unittest.main()
