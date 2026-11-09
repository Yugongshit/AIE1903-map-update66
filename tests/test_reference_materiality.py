"""Reference materiality must preserve full labels and execute reversible edits."""

import copy
import json
from pathlib import Path
import unittest

from map_update.reference_replay import (
    build_material_reference_replay,
    build_reference_replay,
    rollback_reference_replay,
)

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
    return [{key: copy.deepcopy(value) for key, value in row.items()
             if key not in ('prior_state', 'current_state')} for row in rows]


def shifted(before, metres):
    after = copy.deepcopy(before)
    for side in ('left', 'right'):
        for point in after[side + '_lane_boundary']:
            point['y'] += metres
    return after


class ReferenceMaterialityTests(unittest.TestCase):
    def test_small_geometry_is_closed_no_update_without_erasing_reference_label(self):
        source = prior()
        before = source['lane_segments'][0]
        rows = [record(before, shifted(before, .2), ['geometry_changed'])]
        labels = inventory(rows)
        original = copy.deepcopy((source, rows, labels))
        full = build_reference_replay(source, rows, labels)
        result = build_material_reference_replay(source, rows, labels)
        self.assertEqual((source, rows, labels), original)
        self.assertEqual(full['summary']['confirmed_changes'], 1)
        self.assertEqual(result['summary']['confirmed_changes'], 0)
        self.assertEqual(result['summary']['reference_total_changes'], 1)
        self.assertEqual(result['summary']['skipped_minor_changes'], 1)
        self.assertEqual(result['updated_map']['lane_segments'], source['lane_segments'])
        decision = result['materiality_decisions'][0]
        self.assertEqual(decision['final_action'], 'no_update')
        self.assertTrue(decision['source_confirmed_change'])
        self.assertFalse(decision['revisit_required'])
        self.assertEqual(result['rollback_map'], source)

    def test_new_lane_is_kept_without_a_displacement_threshold(self):
        source = prior()
        # A new identity remains a discrete addition even if its footprint is
        # close to an existing one; aggregate lateral offsets are irrelevant.
        added = shifted(lane('lane-b'), .2)
        rows = [record(None, added, ['added'])]
        result = build_material_reference_replay(source, rows, inventory(rows))
        self.assertEqual(result['summary']['accepted_material_changes'], 1)
        self.assertEqual(result['summary']['added'], 1)
        self.assertEqual(result['materiality_decisions'][0]['reason_codes'],
                         ['REFERENCE_CONFIRMED_OBJECT_ADDITION'])
        self.assertEqual(result['rollback_map'], source)

    def test_coherent_displacement_is_kept_using_actual_geometry_not_aggregate_hint(self):
        source = prior()
        before = source['lane_segments'][0]
        rows = [record(before, shifted(before, .8), ['geometry_changed'])]
        # A deliberately contradictory aggregate hint cannot hide actual geometry.
        rows[0]['differences'] = {'right_boundary_distance_m': .01}
        result = build_material_reference_replay(source, rows, inventory(rows))
        self.assertEqual(result['summary']['accepted_material_changes'], 1)
        self.assertTrue(any(check['material']
                            for check in result['materiality_decisions'][0]['geometry_checks']))
        self.assertEqual(result['rollback_map'], source)

    def test_discrete_attribute_changes_remain_material_at_zero_displacement(self):
        for field, value, kind in [('lane_type', 'BUS', 'lane_type_changed'),
                                   ('successors', ['lane-b'], 'connectivity_changed'),
                                   ('left_lane_mark_type', 'SOLID_WHITE', 'marking_changed')]:
            with self.subTest(field=field):
                source = prior()
                before = source['lane_segments'][0]
                after = copy.deepcopy(before)
                after[field] = value
                rows = [record(before, after, [kind])]
                result = build_material_reference_replay(source, rows, inventory(rows))
                self.assertEqual(result['summary']['accepted_material_changes'], 1)
                self.assertEqual(result['rollback_map'], source)

    def test_mixed_filtered_transaction_rolls_back_exactly(self):
        source = prior()
        source['lane_segments'].extend([lane('lane-b', 20), lane('lane-c', 40)])
        rows = [record(source['lane_segments'][0], shifted(source['lane_segments'][0], .2),
                       ['geometry_changed']),
                record(source['lane_segments'][1], shifted(source['lane_segments'][1], .8),
                       ['geometry_changed']),
                record(source['lane_segments'][2], None, ['removed']),
                record(None, lane('lane-d', 60), ['added'])]
        result = build_material_reference_replay(source, rows, inventory(rows))
        self.assertEqual(result['summary']['accepted_material_changes'], 3)
        self.assertEqual(result['summary']['skipped_minor_changes'], 1)
        self.assertEqual(result['summary']['executed_patches'], 3)
        restored = rollback_reference_replay(result['updated_map'], result['patch_log'],
                                            result['prior_metadata'], result['version']['updated_map_sha256'])
        self.assertEqual(restored, source)
        self.assertEqual(result['rollback_map'], source)

    def test_skipped_object_is_still_validated_against_source_map(self):
        source = prior()
        before = source['lane_segments'][0]
        rows = [record(before, shifted(before, .2), ['geometry_changed'])]
        rows[0]['prior_state']['lane_type'] = 'BUS'
        with self.assertRaisesRegex(ValueError, 'prior state'):
            build_material_reference_replay(source, rows, inventory(rows))

    def test_delivered_reference_is_partitioned_without_prediction_accuracy_claim(self):
        packages = list((Path(__file__).resolve().parents[1] /
                         'AIE1903 原始资料/deliveries').glob('*_M8_v1'))
        if len(packages) != 1:
            self.skipTest('The delivered M8 reference package is unavailable')
        package = packages[0]
        source = json.loads((package / 'prior_map_reference.json').read_text())
        rows = [json.loads(line) for line in (package / 'gt_changed_objects.jsonl').read_text().splitlines()]
        labels = [json.loads(line) for line in (package / 'gt_object_inventory.jsonl').read_text().splitlines()]
        full = build_reference_replay(source, rows, labels)
        result = build_material_reference_replay(source, rows, labels)
        self.assertEqual(full['summary']['executed_patches'], 98)
        self.assertEqual(result['summary']['reference_total_changes'], 98)
        self.assertEqual(result['summary']['accepted_material_changes'] +
                         result['summary']['skipped_minor_changes'], 98)
        self.assertEqual(result['summary']['added'], full['summary']['added'])
        self.assertEqual(result['summary']['added'], 27)
        self.assertGreater(result['summary']['skipped_minor_changes'], 0)
        self.assertEqual(result['rollback_map'], source)
        self.assertFalse(result['prediction_input'])
        self.assertFalse(result['summary']['detector_successes_claimed'])
        self.assertFalse(result['summary']['calibrated_accuracy_claimed'])


if __name__ == '__main__':
    unittest.main()
