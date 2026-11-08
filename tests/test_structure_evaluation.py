"""The evaluation scope must include known unpainted structural positives."""
from copy import deepcopy
import unittest

from map_update.reliability_v2 import evaluate_argoverse
from map_update.structure_evaluation import evaluate_all_boundaries


def inputs():
    lane = {'id': 'a', 'left_lane_boundary': [{'x': 0, 'y': 0}, {'x': 12, 'y': 0}],
            'right_lane_boundary': [{'x': 0, 'y': 20}, {'x': 12, 'y': 20}],
            'left_lane_mark_type': 'NONE', 'right_lane_mark_type': 'NONE'}
    prior = {'scene_id': 's', 'lane_segments': [lane]}
    row = {'scene_id': 's', 'layer': 'laneSegments', 'element_id': 'a', 'official_changed': True,
           'state_comparison_supported': True, 'state_delta_types': ['geometry_changed'],
           'differences': {'left_geometry_difference_m': 1., 'right_geometry_difference_m': 0.}}
    state = deepcopy(lane)
    for p in state['left_lane_boundary']:
        p['y'] = 1.
    changed = [{**row, 'prior_state': deepcopy(lane), 'current_state': state}]
    frames, observations = [], []
    for i in range(4):
        fid = str(i * 500000000)
        frames.append({'scene_id': 's', 'clip_id': 'c', 'frame_id': fid, 'timestamp_ns': fid})
        observations.append({'scene_id': 's', 'clip_id': 'c', 'frame_id': fid, 'observation_id': str(i),
                             'geometry_city': [[x, 1., 0] for x in range(13)],
                             'geometry_pixel_indices': list(range(13)), 'pixel_curve_xy': [[x, 0] for x in range(13)],
                             'model_score_mean': .9})
    return prior, [row], changed, observations, frames


class FullStructureEvaluationTests(unittest.TestCase):
    def test_unpainted_true_positive_is_in_scope_and_does_not_change_predictions(self):
        prior, inventory, changed, rows, frames = inputs()
        result = evaluate_argoverse(prior, rows, frames)
        before = deepcopy(result)
        report = evaluate_all_boundaries(prior, inventory, changed, result['events'])
        self.assertEqual(report['eligible_boundaries'], 2)
        self.assertEqual(report['metrics']['true_positives'], 1)
        self.assertEqual(report['metrics']['recall'], 1.)
        self.assertEqual(report['baseline_none_veto_metrics']['false_negatives'], 1)
        self.assertEqual(report['local_geometry_checks'][0]['aliases'][0]['median_offset_absolute_error_m'], 0.)
        self.assertEqual(result, before)

    def test_geometric_detection_on_wrong_object_remains_false_positive(self):
        prior, inventory, changed, rows, frames = inputs()
        inventory[0]['differences']['left_geometry_difference_m'] = 0.
        inventory[0]['state_delta_types'] = ['lane_type_changed']
        result = evaluate_argoverse(prior, rows, frames)
        report = evaluate_all_boundaries(prior, inventory, changed, result['events'])
        self.assertEqual(report['metrics']['false_positives'], 1)
        self.assertEqual(report['metrics']['true_positives'], 0)

    def test_conflicting_shared_alias_truth_is_not_selected_for_a_favorable_hit(self):
        prior, inventory, changed, rows, frames = inputs()
        duplicate = deepcopy(prior['lane_segments'][0]); duplicate['id'] = 'b'
        prior['lane_segments'].append(duplicate)
        other = deepcopy(inventory[0]); other['element_id'] = 'b'
        other['differences']['left_geometry_difference_m'] = 0.
        inventory.append(other)
        result = evaluate_argoverse(prior, rows, frames)
        report = evaluate_all_boundaries(prior, inventory, changed, result['events'])
        self.assertEqual(report['excluded_boundaries'], 1)
        self.assertEqual(report['metrics']['true_positives'], 0)
        self.assertEqual(report['event_diagnostics'][0]['outcome'], 'not_evaluable')


if __name__ == '__main__':
    unittest.main()
