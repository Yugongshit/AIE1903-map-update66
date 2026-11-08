import copy
import json
from pathlib import Path
import unittest

from map_update.reference_replay import build_reference_replay, rollback_reference_replay


def lane(eid='lane-a', x=0):
    return {'id': eid, 'lane_type': 'VEHICLE', 'is_intersection': False,
            'left_lane_boundary': [{'x': x, 'y': 0}, {'x': x + 10, 'y': 0}],
            'right_lane_boundary': [{'x': x, 'y': 3}, {'x': x + 10, 'y': 3}],
            'left_lane_mark_type': 'NONE', 'right_lane_mark_type': 'SOLID_WHITE',
            'predecessors': [], 'successors': []}


def prior():
    return {'scene_id': 'scene', 'coordinates': 'City XY', 'role': 'stale_prior',
            'lane_segments': [lane()], 'pedestrian_crossings': []}


def record(before, after, kinds, layer='laneSegments'):
    return {'scene_id': 'scene', 'layer': layer, 'element_id': (after or before)['id'],
            'official_changed': True, 'state_comparison_supported': True,
            'prior_present': before is not None, 'current_present': after is not None,
            'state_delta_types': kinds, 'differences': {},
            'prior_state': copy.deepcopy(before), 'current_state': copy.deepcopy(after)}


def inventory(rows):
    return [{key: copy.deepcopy(value) for key, value in r.items()
             if key not in ('prior_state', 'current_state')} for r in rows]


class ReferenceReplayTests(unittest.TestCase):
    def test_complete_object_update_keeps_attributes_connectivity_and_original(self):
        source = prior()
        after = lane()
        after.update(left_lane_mark_type='DASHED_WHITE', lane_type='BUS', successors=[12])
        after['right_lane_boundary'][1]['y'] = 3.5
        rows = [record(source['lane_segments'][0], after,
                       ['geometry_changed', 'marking_changed', 'lane_type_changed', 'connectivity_changed'])]
        labels = inventory(rows)
        untouched = copy.deepcopy((source, rows, labels))
        result = build_reference_replay(source, rows, labels, {'files': ['checked-M8']})
        self.assertEqual((source, rows, labels), untouched)
        self.assertEqual(result['updated_map']['lane_segments'], [after])
        self.assertEqual(result['rollback_map'], source)
        self.assertEqual(result['summary']['executed_patches'], 1)
        self.assertEqual(result['summary']['replaced'], 1)
        self.assertFalse(result['summary']['detector_successes_claimed'])
        self.assertFalse(result['prediction_input'])
        feature = result['change_layer']['features'][0]
        self.assertEqual(feature['properties']['source'], 'delivered_ground_truth')
        self.assertEqual(feature['properties']['confirmation_source'], 'reference')
        self.assertTrue(feature['properties']['map_patch_executed'])
        self.assertNotEqual(result['changes'][0]['before_geometry'], result['changes'][0]['after_geometry'])

    def test_add_crossing_converts_vertices_and_rollback_restores_original_order(self):
        source = prior()
        crossing = {'id': 'cross-a', 'edges': [{'x': 0, 'y': 0}, {'x': 4, 'y': 0},
                                              {'x': 4, 'y': 2}, {'x': 0, 'y': 2}]}
        rows = [record(None, lane('lane-b', 20), ['added']),
                record(None, crossing, ['added'], 'pedCrossings')]
        result = build_reference_replay(source, rows, inventory(rows))
        self.assertEqual([r['id'] for r in result['updated_map']['lane_segments']], ['lane-a', 'lane-b'])
        self.assertEqual(result['updated_map']['pedestrian_crossings'][0]['points'],
                         [[0, 0], [4, 0], [4, 2], [0, 2], [0, 0]])
        self.assertEqual(result['summary']['added'], 2)
        self.assertEqual(result['rollback_map'], source)
        self.assertEqual(result['updated_map_context']['summary']['source_crossings'], 1)

    def test_delete_and_mixed_operations_have_real_inverse(self):
        source = prior()
        source['lane_segments'] += [lane('lane-b', 20), lane('lane-c', 40)]
        after = lane('lane-c', 40)
        after['lane_type'] = 'BUS'
        rows = [record(source['lane_segments'][0], None, ['removed']),
                record(None, lane('lane-d', 60), ['added']),
                record(source['lane_segments'][2], after, ['lane_type_changed'])]
        result = build_reference_replay(source, rows, inventory(rows))
        self.assertEqual(result['summary']['deleted'], 1)
        self.assertEqual([r['id'] for r in result['updated_map']['lane_segments']], ['lane-b', 'lane-c', 'lane-d'])
        self.assertEqual(result['rollback_map'], source)
        restored = rollback_reference_replay(result['updated_map'], result['patch_log'],
                                            result['prior_metadata'], result['version']['updated_map_sha256'])
        self.assertEqual(restored, source)
        altered = copy.deepcopy(result['updated_map'])
        altered['lane_segments'][0]['lane_type'] = 'BUS'
        with self.assertRaisesRegex(ValueError, 'version'):
            rollback_reference_replay(altered, result['patch_log'], result['prior_metadata'],
                                      result['version']['updated_map_sha256'])

    def test_prior_mismatch_and_invalid_geometry_reject_entire_transaction(self):
        source = prior()
        after = lane()
        after['lane_type'] = 'BUS'
        rows = [record(lane(), after, ['lane_type_changed'])]
        labels = inventory(rows)
        rows[0]['prior_state']['left_lane_mark_type'] = 'SOLID_WHITE'
        with self.assertRaisesRegex(ValueError, 'prior state'):
            build_reference_replay(source, rows, labels)
        rows = [record(lane(), after, ['lane_type_changed'])]
        rows[0]['current_state']['left_lane_boundary'] = [{'x': 0, 'y': 0}]
        with self.assertRaises(ValueError):
            build_reference_replay(source, rows, labels)
        self.assertEqual(source, prior())

    def test_missing_duplicate_cross_scene_and_nonchanged_labels_rejected(self):
        source = prior()
        after = lane()
        after['lane_type'] = 'BUS'
        rows = [record(lane(), after, ['lane_type_changed'])]
        labels = inventory(rows)
        with self.assertRaisesRegex(ValueError, 'Missing supported'):
            build_reference_replay(source, [], labels)
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            build_reference_replay(source, rows + rows, labels)
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            build_reference_replay(source, rows, labels + labels)
        for records in (copy.deepcopy(rows), copy.deepcopy(labels)):
            records[0]['scene_id'] = 'different'
            with self.assertRaises(ValueError):
                build_reference_replay(source, records if 'prior_state' in records[0] else rows,
                                       records if 'prior_state' not in records[0] else labels)
        rows[0]['official_changed'] = labels[0]['official_changed'] = False
        with self.assertRaisesRegex(ValueError, 'explicitly labelled changed'):
            build_reference_replay(source, rows, labels)

    def test_unknown_layers_are_explicit_and_not_counted_as_executed(self):
        source = prior()
        row = record(None, {'id': 'area-a'}, ['added'], 'drivableAreas')
        row['state_comparison_supported'] = False
        result = build_reference_replay(source, [row], inventory([row]))
        self.assertEqual(result['summary']['executed_patches'], 0)
        self.assertEqual(result['summary']['unsupported_changed_objects'], 1)
        self.assertEqual(result['summary']['unsupported_inventory_objects'], 1)
        self.assertEqual(result['changes'], [])
        self.assertEqual(result['rollback_map'], source)

    def test_all_delivered_98_changes_restore_current_objects_and_exact_prior(self):
        packages = list((Path(__file__).resolve().parents[1] / 'AIE1903 原始资料/deliveries').glob('*_M8_v1'))
        if len(packages) != 1:
            self.skipTest('The delivered M8 reference package is unavailable')
        package = packages[0]
        source = json.loads((package / 'prior_map_reference.json').read_text())
        rows = [json.loads(line) for line in (package / 'gt_changed_objects.jsonl').read_text().splitlines()]
        labels = [json.loads(line) for line in (package / 'gt_object_inventory.jsonl').read_text().splitlines()]
        current = json.loads((package / 'current_gt_map.json').read_text())
        result = build_reference_replay(source, rows, labels)
        self.assertEqual(result['summary']['confirmed_changes'], 98)
        self.assertEqual(result['summary']['executed_patches'], 98)
        self.assertEqual(result['summary']['added'], 27)
        self.assertEqual(result['summary']['replaced'], 71)
        self.assertEqual(result['summary']['layers'], {'laneSegments': 89, 'pedCrossings': 9})
        self.assertEqual(result['rollback_map'], source)
        self.assertEqual(result['version']['prior_map_sha256'], result['version']['rollback_map_sha256'])
        # Compare every object with the separately supplied full current map,
        # including unchanged objects; crossing closure is the only conversion.
        for data in (current, result['updated_map']):
            for crossing in data['pedestrian_crossings']:
                points = crossing['points']
                if points[0] != points[-1]:
                    points.append(copy.deepcopy(points[0]))
        for key in ('lane_segments', 'pedestrian_crossings'):
            self.assertEqual({r['id']: r for r in result['updated_map'][key]},
                             {r['id']: r for r in current[key]})
        self.assertEqual(len(result['change_layer']['features']), 98)


if __name__ == '__main__':
    unittest.main()
