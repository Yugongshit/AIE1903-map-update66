"""Integration regressions against the supplied real scene and M3 handoff."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from map_update.output import write_maintenance
from map_update.reliability import attach_m3
from map_update.scene import inspect_scene


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/'AIE1903 原始资料'
HANDOFF = RAW/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json'


class RealM3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = inspect_scene(RAW, ROOT/'external/perception_delivery')
        cls.handoff = json.loads(HANDOFF.read_text(encoding='utf-8'))
        cls.joined = attach_m3(cls.scene, cls.handoff)

    def test_actual_results_and_reference_map_are_preserved(self):
        data = self.joined
        self.assertEqual(data['reference_map'], self.scene['reference_map'])
        self.assertEqual(data['maintenance']['base_map_updates'], [])
        self.assertEqual(data['maintenance']['review_units'], 7)
        self.assertEqual(data['maintenance']['resolved_review_units'], 0)
        self.assertEqual(data['maintenance']['priority_review_ids'], ['S0980-LD-003', 'S0980-LD-004'])
        self.assertEqual(data['m3']['summary']['matched_test_frames_above_reference'], 0)
        self.assertEqual(data['m3']['matched_frame_evidence'], self.handoff['matched_frame_evidence'])
        self.assertFalse(data['maintenance']['planning_required_for_map_maintenance'])
        self.assertTrue(all(c['reliability'] is None for c in self.scene['candidates']))

    def test_later_t004_match_does_not_clear_earlier_t004_candidate(self):
        data = self.joined
        c = next(c for c in data['candidates'] if c['candidate_id'] == 'S0980-LD-003')
        self.assertEqual(c['frame_window'], [18, 25])
        self.assertEqual(c['reliability']['record_id'], 'S0980-LD-003')
        self.assertEqual(c['reliability']['decision'], 'Insufficient Evidence')
        self.assertIsNone(c['map_element_id'])
        self.assertIsNone(c['reliability']['reference_element_id'])
        local = next(u for u in data['m3']['review_units'] if u['record_type'] == 'MATCHED_WINDOW')
        self.assertEqual(local['frame_window'], [26, 40])
        self.assertIsNone(local['candidate_id'])
        self.assertIsNotNone(local['reference_element_id'])
        earlier = [l for f in data['replay'][:26] for l in f['observed_lines']]
        self.assertFalse(any('m3_record_id' in line for line in earlier))

    def test_unknown_coverage_alignment_and_truth_are_not_fabricated(self):
        for unit in self.joined['m3']['review_units']:
            self.assertIsNone(unit['effective_coverage'])
            self.assertIsNone(unit['alignment_valid'])
            self.assertIsNone(unit['source_case']['truth'])
            self.assertFalse(unit['write_eligible'])
        for row in self.joined['m3']['matched_frame_evidence']:
            self.assertIsNone(row['p_value'])

    def test_occupancy_keeps_real_frame_geometry_without_clearance_or_planning_action(self):
        events = self.joined['event_layer']['events']
        self.assertEqual(len(events), 23)
        self.assertEqual(sum(len(e['observations']) for e in events), 311)
        for e in events:
            self.assertEqual(e['duration_class'], 'unknown')
            self.assertFalse(e['clearance_confirmed'])
            self.assertFalse(e['write_eligible'])
            self.assertEqual(e['planning_action'], 'independent_passability_review_required')
            for obs in e['observations']:
                frame = self.scene['replay'][obs['frame_index']]
                original = next(o for o in frame['occupancy'] if o['candidate_id'] == e['event_id'])
                self.assertEqual(obs['objects'], original['objects'])
                self.assertEqual(obs['timestamp_ns'], frame['timestamp_ns'])
        occupancy = [c for c in self.joined['candidates'] if c['change_type'] == 'occupancy']
        self.assertTrue(all(c['reliability'] is None for c in occupancy))

    def test_wrong_scene_map_version_or_hash_is_rejected(self):
        for field, value in [('scene', 'scene-0001'), ('file', 'other.json'), ('version', '1.4'), ('sha256', '0'*64)]:
            with self.subTest(field=field):
                h = deepcopy(self.handoff)
                (h if field == 'scene' else h['source_map'])[field] = value
                with self.assertRaises(ValueError):
                    attach_m3(self.scene, h)

    def test_wrong_absolute_time_and_shifted_candidate_window_are_rejected(self):
        for field, value in [('first_timestamp_us', 1537845357697970), ('first_frame', 10), ('track', 'T004')]:
            with self.subTest(field=field):
                h = deepcopy(self.handoff)
                h['cases'][1][field] = value
                with self.assertRaises(ValueError):
                    attach_m3(self.scene, h)

    def test_duplicate_cases_and_duplicate_frame_evidence_are_rejected(self):
        for key in ('cases', 'matched_frame_evidence'):
            h = deepcopy(self.handoff)
            h[key].append(deepcopy(h[key][0]))
            with self.assertRaises(ValueError):
                attach_m3(self.scene, h)

    def test_controlled_case_cannot_enter_real_stream(self):
        h = deepcopy(self.handoff)
        h['cases'][1]['scripted_change'] = True
        with self.assertRaises(ValueError):
            attach_m3(self.scene, h)
        h = deepcopy(self.handoff)
        h['cases'][1]['record_id'] = 'W1_full_0.00_+0.30'
        with self.assertRaises(ValueError):
            attach_m3(self.scene, h)

    def test_invented_coverage_or_promoted_decision_cannot_bypass_current_contract(self):
        for field, value in [('effective_coverage', 0), ('effective_coverage', 1),
                             ('effective_coverage', float('nan')), ('valid_visibility_frames', 15),
                             ('final_decision', 'NO_SIGNIFICANT_MISMATCH')]:
            with self.subTest(field=field, value=value):
                h = deepcopy(self.handoff)
                h['cases'][0][field] = value
                with self.assertRaises(ValueError):
                    attach_m3(self.scene, h)

    def test_missing_ld_record_and_bad_anomaly_statistics_are_rejected(self):
        h = deepcopy(self.handoff)
        h['cases'].pop()
        with self.assertRaises(ValueError):
            attach_m3(self.scene, h)
        for field, value in [('above_reference', True), ('anomaly_score', float('inf')),
                             ('map_token', 'wrong-divider'), ('timestamp_us', 0), ('p_value', .01)]:
            with self.subTest(field=field):
                h = deepcopy(self.handoff)
                h['matched_frame_evidence'][0][field] = value
                with self.assertRaises(ValueError):
                    attach_m3(self.scene, h)

    def test_overlapping_windows_cannot_reuse_matched_evidence(self):
        h = deepcopy(self.handoff)
        duplicate = deepcopy(h['cases'][0])
        duplicate['record_id'] = 'another-window'
        h['cases'].append(duplicate)
        with self.assertRaises(ValueError):
            attach_m3(self.scene, h)

    def test_output_names_and_browser_payload_do_not_claim_an_updated_map(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = write_maintenance(self.joined, Path(temp)/'result')
            self.assertTrue((dest/'event_layer.json').exists())
            self.assertTrue((dest/'reference_map.json').exists())
            self.assertFalse((dest/'map.json').exists())
            self.assertFalse((dest/'planning.json').exists())
            self.assertEqual(json.loads((dest/'maintenance.json').read_text())['base_map_updates'], [])
            self.assertIn('M3 已接入', (dest/'index.html').read_text())
            with self.assertRaises(ValueError):
                write_maintenance(self.joined, dest)


if __name__ == '__main__':
    unittest.main()
