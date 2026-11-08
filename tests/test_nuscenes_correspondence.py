from copy import deepcopy
from pathlib import Path
import unittest

from map_update.local_correspondence import divider_references, evaluate_marking_window, match_local_curve
from map_update.nuscenes_v2 import evaluate_nuscenes
from map_update.reliability import integrate_m3


def line(points):
    return {'type': 'LineString', 'coordinates': points}


def reference(eid, points):
    return {'element_id': eid, 'layer': 'lane_divider', 'geometry_city': line(points)}


def observations(count=3, height=1.):
    return [{'frame_index': i, 'timestamp_ns': str(i*500_000_000), 'line_id': 1,
             'candidate_ids': ['candidate'], 'source_file': 'source-'+str(i),
             'geometry_city': line([[2., height], [18., height]])} for i in range(count)]


def evaluate(rows, elements, frames=None):
    frames = frames or [dict(frame_index=r['frame_index'], timestamp_ns=r['timestamp_ns'], observed_lines=[]) for r in rows]
    return evaluate_marking_window({'candidate_id': 'candidate', 'frame_window': [0, 2]}, rows,
                                   elements, frames, 'visit', 'scene')


class LocalCorrespondenceTests(unittest.TestCase):
    def test_clipped_local_direction_handles_a_curved_map_line(self):
        refs = divider_references([reference('curved', [[0., 0.], [12., 0.], [12., 30.]])])
        assessed = match_local_curve(line([[4., 2.], [10., 2.]]), refs)
        selected = assessed['selected_reference']
        self.assertEqual(selected['map_aliases'][0]['map_element_id'], 'curved')
        self.assertAlmostEqual(selected['distance_m'], 2.)
        self.assertAlmostEqual(selected['local_direction_deg'], 0.)
        self.assertAlmostEqual(selected['overlap_m'], 6.)

    def test_physical_duplicates_are_merged_but_distinct_close_targets_are_ambiguous(self):
        refs = divider_references([reference('a', [[0., 0.], [20., 0.]]),
                                   reference('same', [[20., 0.], [0., 0.]])])
        self.assertEqual(len(refs), 1)
        self.assertEqual(len(match_local_curve(line([[2., 1.], [18., 1.]]), refs)['selected_reference']['map_aliases']), 2)
        refs += divider_references([reference('other', [[0., 4.], [20., 4.]])])
        assessed = match_local_curve(line([[2., 2.], [18., 2.]]), refs)
        self.assertIsNone(assessed['selected_reference'])
        self.assertIn('DISTINCT_PHYSICAL_REFERENCE_AMBIGUITY', assessed['reason_codes'])

    def test_local_support_is_computed_and_two_frames_do_not_pass_temporal_gate(self):
        refs = [reference('a', [[0., 0.], [20., 0.]])]
        result = evaluate(observations(), refs)['event_fields']
        self.assertEqual(result['reliability']['status'], 'supported_conflict')
        self.assertEqual(result['temporal_block_count'], 3)
        self.assertAlmostEqual(abs(result['median_residual_m']), 1.)
        self.assertFalse(result['base_map_write_eligible'])
        result = evaluate(observations(2), refs)['event_fields']
        self.assertEqual(result['reliability']['status'], 'insufficient_geometry')
        self.assertIn('INSUFFICIENT_TEMPORALLY_SEPARATED_LOCAL_OBSERVATIONS', result['reliability']['reason_codes'])

    def test_out_of_gate_and_changing_reference_are_explicit_failures(self):
        refs = [reference('a', [[0., 0.], [20., 0.]])]
        result = evaluate(observations(height=4.), refs)['event_fields']
        self.assertEqual(result['map_aliases'], [])
        self.assertIn('NO_REFERENCE_WITHIN_GEOMETRIC_GATE', result['reliability']['reason_codes'])
        refs.append(reference('b', [[0., 8.], [20., 8.]]))
        rows = observations()
        rows[-1]['geometry_city'] = line([[2., 9.], [18., 9.]])
        result = evaluate(rows, refs)['event_fields']
        self.assertEqual(result['reliability']['status'], 'insufficient_geometry')
        self.assertIn('REFERENCE_ASSIGNMENT_CHANGES_WITHIN_CANDIDATE_WINDOW', result['reliability']['reason_codes'])

    def test_future_reference_context_is_not_read_or_used(self):
        refs = [reference('a', [[0., 0.], [20., 0.]])]
        rows = observations()
        frames = [dict(frame_index=r['frame_index'], timestamp_ns=r['timestamp_ns'], observed_lines=[]) for r in rows]
        baseline = evaluate(rows, refs, frames)
        frames.append({'frame_index': 3, 'timestamp_ns': '1500000000', 'observed_lines': [
            {'candidate_ids': [], 'geometry_city': {'type': 'invalid future geometry'}}]})
        self.assertEqual(evaluate(rows, refs, frames), baseline)
        with self.assertRaisesRegex(ValueError, 'outside its exact candidate window'):
            evaluate(observations(4), refs, frames)

    def test_crossing_curve_is_not_counted_as_the_reference_line_remaining_present(self):
        rows = observations()
        frames = [dict(frame_index=r['frame_index'], timestamp_ns=r['timestamp_ns'], observed_lines=[
            {'candidate_ids': [], 'line_id': 9, 'track_id': 'crossing', 'source_file': 'crossing-'+str(r['frame_index']),
             'geometry_city': line([[0., -5.], [5., 5.]])}]) for r in rows]
        fields = evaluate(rows, [reference('a', [[0., 0.], [20., 0.]])], frames)['event_fields']
        self.assertEqual(fields['same_window_reference_observations'], [])
        self.assertNotIn('REFERENCE_LINE_ALSO_OBSERVED_IN_SAME_WINDOW', fields['reliability']['reason_codes'])

    def test_real_ld004_support_is_not_a_claim_that_existing_divider_moved(self):
        root = Path(__file__).resolve().parents[1]
        raw = root/'AIE1903 原始资料'
        scene = integrate_m3(raw, root/'external/perception_delivery', raw/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
        before = deepcopy(scene)
        output = evaluate_nuscenes(scene)
        events = {e['event_id']: e for e in output['events']}
        event = events['S0980-LD-004']
        self.assertEqual(event['reliability']['status'], 'supported_conflict')
        self.assertAlmostEqual(event['median_residual_m'], -2.343870715749623)
        self.assertAlmostEqual(event['mad_residual_m'], .0858344089107046)
        self.assertEqual(event['distinct_frame_count'], 6)
        self.assertEqual(event['change_type'], 'line_discrepancy')
        self.assertEqual(event['geometry_method'], 'representative_observed_curve_at_fixed_reference_anchor_without_reconstruction')
        self.assertEqual(event['structural_hypothesis']['operation'], 'unresolved_parallel_marking_or_insertion')
        self.assertFalse(event['structural_hypothesis']['confirmed'])
        self.assertEqual([r['frame_id'] for r in event['same_window_reference_observations']], [str(i) for i in range(28, 34)])
        self.assertTrue(all(abs(r['residual_m']) < .25 for r in event['same_window_reference_observations']))
        self.assertIn('REFERENCE_LINE_ALSO_OBSERVED_IN_SAME_WINDOW', event['reliability']['reason_codes'])
        self.assertEqual(event['observation_evidence']['reference_semantics']['status'], 'divider_geometry_only_paint_attributes_unverified')
        self.assertTrue(event['observation_evidence']['change_assessment']['model_detected_local_change'])
        self.assertFalse(event['observation_evidence']['change_assessment']['map_edit_verified'])
        self.assertEqual(sum(s.get('measured_local_relation') == 'offset_negative' for s in event['samples']), 6)
        self.assertIsNone(event['reliability']['alignment_verified'])
        self.assertIsNone(event['reliability']['effective_coverage'])
        self.assertFalse(event['base_map_write_eligible'])
        self.assertEqual(events['S0980-LD-003']['map_aliases'], [])
        self.assertEqual(sum(e['change_type'] == 'occupancy' and e['reliability']['status'] == 'supported_conflict' for e in output['events']), 23)
        self.assertEqual(scene, before)


if __name__ == '__main__':
    unittest.main()
