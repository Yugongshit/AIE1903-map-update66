import copy
import unittest

try:
    import numpy  # noqa: F401
except ImportError:
    numpy = None

from map_update.reliability_v2 import canonical_boundaries, evaluate_argoverse


def prior(mark='SOLID_WHITE'):
    return {'scene_id': 'scene', 'lane_segments': [{
        'id': 'a', 'left_lane_boundary': [{'x': 0, 'y': 0}, {'x': 12, 'y': 0}],
        'right_lane_boundary': [{'x': 0, 'y': 20}, {'x': 12, 'y': 20}],
        'left_lane_mark_type': mark, 'right_lane_mark_type': 'NONE'}]}


def observations(offsets, interval=.5):
    rows, frames = [], []
    for i, offset in enumerate(offsets):
        fid = str(round(i*interval*1e9))
        frames.append({'frame_id': fid, 'timestamp_ns': fid, 'clip_id': 'clip', 'scene_id': 'scene'})
        rows.append({'observation_id': 'o'+str(i), 'frame_id': fid, 'clip_id': 'clip', 'scene_id': 'scene',
                     'geometry_city': [[x, offset, 0] for x in range(13)],
                     'geometry_pixel_indices': list(range(13)), 'pixel_curve_xy': [[x, 0] for x in range(13)],
                     'model_score_mean': .9, 'visibility': 'unknown', 'quality_flags': ['curb_confusion_possible']})
    return rows, frames


@unittest.skipIf(numpy is None, 'Argoverse optional numpy dependency not installed')
class ReliabilityV2Tests(unittest.TestCase):
    def test_shared_boundaries_merge_reversal_and_preserve_aliases(self):
        p = prior()
        other = copy.deepcopy(p['lane_segments'][0]); other['id'] = 'b'
        other['left_lane_boundary'].reverse(); p['lane_segments'].append(other)
        boundaries = canonical_boundaries(p)
        self.assertEqual(len(boundaries), 2)
        self.assertTrue(all(len(b['aliases']) == 2 for b in boundaries))
        rows, frames = observations([1, 1, 1, 1])
        event = evaluate_argoverse(p, rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'supported_conflict')
        self.assertTrue(event['physical_target_unambiguous'])
        self.assertFalse(event['map_object_identity_unambiguous'])
        self.assertEqual(len(event['map_aliases']), 2)

    def test_different_physical_targets_remain_ambiguous(self):
        p = prior(); p['lane_segments'][0]['right_lane_boundary'] = [{'x': 0, 'y': 2}, {'x': 12, 'y': 2}]
        p['lane_segments'][0]['right_lane_mark_type'] = 'SOLID_WHITE'
        rows, frames = observations([1, 1, 1, 1])
        result = evaluate_argoverse(p, rows, frames)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['summary']['observation_reasons']['DISTINCT_PHYSICAL_REFERENCE_AMBIGUITY'], 4)

    def test_conflicting_attributes_are_not_silently_merged(self):
        p = prior(); duplicate = copy.deepcopy(p['lane_segments'][0]); duplicate['id'] = 'b'
        duplicate['left_lane_mark_type'] = 'DASHED_WHITE'; p['lane_segments'].append(duplicate)
        self.assertEqual(len(canonical_boundaries(p)), 3)
        rows, frames = observations([1, 1, 1])
        self.assertEqual(evaluate_argoverse(p, rows, frames)['events'], [])

    def test_fixed_anchor_and_mad_resist_single_outlier(self):
        rows, frames = observations([1, 1, 2.8, 1, 1])
        result = evaluate_argoverse(prior(), rows, frames)
        event = result['events'][0]
        self.assertEqual(event['median_residual_m'], 1.)
        self.assertEqual(event['mad_residual_m'], 0.)
        self.assertEqual(event['reliability']['status'], 'supported_conflict')
        self.assertFalse(event['base_map_write_eligible'])
        self.assertFalse(result['diagnostics']['probability_calibrated'])
        self.assertEqual(event['fixed_anchor']['xy_m'], [2.5, 0.])

    def test_moving_observation_window_compares_same_physical_position(self):
        rows, frames = observations([0, 0, 0, 0])
        for row, (start, end) in zip(rows, [(0, 8), (2, 10), (4, 12), (0, 12)]):
            row['geometry_city'] = [[x, .1*x+.2, 0] for x in range(start, end+1)]
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['fixed_anchor']['xy_m'], [6., 0.])
        self.assertAlmostEqual(event['median_residual_m'], .8)
        self.assertAlmostEqual(event['mad_residual_m'], 0.)
        self.assertTrue(all(abs(s['residual_m']-.8) < 1e-9 for s in event['samples']))

    def test_camera_copies_and_exact_duplicates_do_not_inflate_evidence(self):
        rows, frames = observations([1])
        duplicates = [copy.deepcopy(rows[0]) for _ in range(20)]
        for i, row in enumerate(duplicates): row['observation_id'] += '-camera-'+str(i)
        result = evaluate_argoverse(prior(), rows+rows+duplicates, frames)
        event = result['events'][0]
        self.assertEqual(result['summary']['unique_observations'], 21)
        self.assertEqual(event['distinct_frame_count'], 1)
        self.assertEqual(event['temporal_block_count'], 1)
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')

    def test_dense_correlated_frames_do_not_become_temporal_blocks(self):
        rows, frames = observations([1]*20, interval=.01)
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['temporal_block_count'], 1)
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')

    def test_unknown_visibility_does_not_erase_positive_conflict(self):
        rows, frames = observations([1, 1, 1, 1])
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'supported_conflict')
        self.assertIsNone(event['reliability']['effective_coverage'])
        self.assertTrue(all(s['observation'] == 'supports_change' for s in event['samples']))

    def test_absence_cannot_generate_deletion(self):
        _, frames = observations([0, 0, 0, 0])
        result = evaluate_argoverse(prior(), [], frames)
        self.assertEqual(result['events'], [])
        self.assertIn('deletion', result['diagnostics']['visibility_unknown_blocks'])

    def test_none_and_unknown_are_not_normal_painted_markings(self):
        rows, frames = observations([0, 0, 0, 0])
        for mark, reason in [('NONE', 'PRIOR_EXPLICITLY_UNPAINTED_REQUIRES_INSERTION_VERIFICATION'),
                             ('UNKNOWN', 'PRIOR_MARKING_SEMANTICS_UNKNOWN')]:
            event = evaluate_argoverse(prior(mark), rows, frames)['events'][0]
            self.assertEqual(event['reliability']['status'], 'insufficient_geometry')
            self.assertIn(reason, event['reliability']['reason_codes'])
            self.assertEqual(event['change_type'], 'line_discrepancy')

    def test_none_routes_paint_presence_hypothesis_without_claiming_boundary_displacement(self):
        rows, frames = observations([1, 1, 1, 1])
        event = evaluate_argoverse(prior('NONE'), rows, frames)['events'][0]
        hypothesis = event['structural_hypothesis']
        self.assertEqual(hypothesis['target_property'], 'paint_presence')
        self.assertEqual(hypothesis['operation'], 'marking_presence_change')
        self.assertEqual(hypothesis['status'], 'unconfirmed')
        self.assertFalse(hypothesis['confirmed'])
        self.assertFalse(hypothesis['base_map_write_eligible'])
        self.assertEqual(event['change_type'], 'line_discrepancy')
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')
        self.assertTrue(all(s['observation'] == 'unknown' for s in event['samples']))
        self.assertTrue(all(s['measured_local_relation'] == 'offset_positive' for s in event['samples']))
        evidence = event['observation_evidence']
        self.assertEqual(evidence['geometry_measurement']['relation_counts'], {'offset_positive': 4})
        self.assertEqual(evidence['temporal_support']['status'], 'sufficient')
        self.assertEqual(evidence['local_consistency']['status'], 'stable')
        self.assertEqual(evidence['reference_semantics']['status'], 'unpainted_or_logical_boundary')
        self.assertEqual(evidence['paint_identity']['status'], 'unverified')

    def test_none_geometric_agreement_is_not_proof_of_no_paint_presence_change(self):
        rows, frames = observations([.1, .1, .1, .1])
        event = evaluate_argoverse(prior('NONE'), rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')
        self.assertEqual(event['structural_hypothesis']['target_property'], 'paint_presence')
        self.assertTrue(all(s['measured_local_relation'] == 'within_tolerance' for s in event['samples']))
        self.assertTrue(all(s['observation'] == 'unknown' for s in event['samples']))

    def test_unknown_reference_does_not_assume_geometry_or_presence_change(self):
        rows, frames = observations([1, 1, 1, 1])
        event = evaluate_argoverse(prior('UNKNOWN'), rows, frames)['events'][0]
        hypothesis = event['structural_hypothesis']
        self.assertEqual(hypothesis['target_property'], 'unspecified_marking_semantics')
        self.assertEqual(hypothesis['operation'], 'unspecified')
        self.assertFalse(hypothesis['confirmed'])
        self.assertEqual(event['observation_evidence']['reference_semantics']['status'], 'unknown')

    def test_supported_painted_conflict_is_not_confirmed_full_map_change(self):
        rows, frames = observations([1, 1, 1, 1])
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'supported_conflict')
        self.assertEqual(event['change_type'], 'geometry_change')
        hypothesis = event['structural_hypothesis']
        self.assertEqual(hypothesis['target_property'], 'boundary_geometry')
        self.assertEqual(hypothesis['operation'], 'geometry_change')
        self.assertEqual(hypothesis['assessment'], 'supported_conflict')
        self.assertFalse(hypothesis['confirmed'])
        self.assertFalse(event['observation_evidence']['change_assessment']['confirmed_map_change'])
        self.assertEqual(event['observation_evidence']['alignment']['status'], 'unverified')

    def test_unstable_or_short_evidence_retains_all_measured_directions(self):
        rows, frames = observations([1, -1, 1, -1])
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        evidence = event['observation_evidence']
        self.assertEqual(evidence['local_consistency']['status'], 'unstable')
        self.assertEqual(evidence['geometry_measurement']['relation_counts'],
                         {'offset_positive': 2, 'offset_negative': 2})
        self.assertTrue(all(s['observation'] == 'unknown' for s in event['samples']))
        rows, frames = observations([1] * 20, interval=.01)
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        evidence = event['observation_evidence']
        self.assertEqual(evidence['temporal_support']['status'], 'insufficient')
        self.assertEqual(evidence['geometry_measurement']['relation_counts'], {'offset_positive': 1})
        self.assertEqual(event['samples'][0]['observation'], 'unknown')

    def test_hypothesis_and_evidence_do_not_consume_ground_truth_or_mutate_inputs(self):
        rows, frames = observations([1, 1, 1])
        reference = prior('NONE')
        original = copy.deepcopy((reference, rows, frames))
        expected = evaluate_argoverse(reference, rows, frames)
        self.assertEqual((reference, rows, frames), original)
        for row in rows:
            row['gt_change_type'] = 'geometry_change'
            row['ground_truth'] = {'permanent': True, 'paint_identity_verified': True}
        self.assertEqual(evaluate_argoverse(reference, rows, frames), expected)

    def test_local_agreement_and_conflicting_offsets_differ(self):
        rows, frames = observations([.1, .12, .08, .1])
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'consistent_with_map')
        rows, frames = observations([1, -1, 1, -1])
        event = evaluate_argoverse(prior(), rows, frames)['events'][0]
        self.assertEqual(event['reliability']['status'], 'insufficient_geometry')

    def test_conflicting_duplicate_and_cross_scene_are_rejected(self):
        rows, frames = observations([1, 1, 1])
        changed = copy.deepcopy(rows[0]); changed['model_score_mean'] = .8
        with self.assertRaises(ValueError): evaluate_argoverse(prior(), rows+[changed], frames)
        rows[0]['scene_id'] = 'another'
        with self.assertRaises(ValueError): evaluate_argoverse(prior(), rows, frames)

    def test_explicit_bad_alignment_is_rejected_even_with_high_model_score(self):
        rows, frames = observations([1, 1, 1])
        for row in rows: row['alignment_valid'] = False
        result = evaluate_argoverse(prior(), rows, frames)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['summary']['observation_reasons']['EXPLICIT_ALIGNMENT_FAILURE'], 3)


if __name__ == '__main__':
    unittest.main()
