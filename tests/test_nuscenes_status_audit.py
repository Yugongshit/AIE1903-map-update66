"""Status percentages retain their own populations and cannot alter predictions."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from map_update.nuscenes_status_audit import build_nuscenes_status_audit


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'AIE1903 原始资料'
DELIVERY = ROOT / 'external/perception_delivery'


def source_events():
    import csv
    with (RAW / 'processed/scene0980_unified_candidates_v1.csv').open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    events = []
    for row in rows:
        structural = row['evidence_family'] == 'LANE_MARKING_GEOMETRY'
        first, last = int(row['first_frame']), int(row['last_frame'])
        # The supplied structural windows contain every frame. State rows are
        # present only to test the separate semantic denominator.
        event = {'event_id': row['candidate_id'],
                 'map_layer': {'predicted_layer': 'base_map_structure' if structural else 'map_state_event'},
                 'reliability': {'status': 'insufficient_geometry' if structural else 'supported_conflict',
                                 'reason_codes': ['TEST_FIXTURE']},
                 'decision': {'base_map_write_eligible': False, 'write_gate': {'map_patch_executed': False}}}
        if structural:
            event.update(correspondence_policy={'window': [first, last]},
                         samples=[{'frame_id': str(i)} for i in range(first, last + 1)],
                         model_change_decision={'status': 'insufficient_evidence', 'detected': False})
        events.append(event)
    return events


@unittest.skipUnless((RAW / 'processed/tracking_report.json').exists(), 'supplied nuScenes fixture unavailable')
class NuscenesStatusAuditTests(unittest.TestCase):
    def test_real_source_counts_do_not_replace_model_structural_denominator(self):
        events = source_events()
        before = deepcopy(events)
        report = build_nuscenes_status_audit(RAW, DELIVERY, events)
        self.assertEqual(report['annotation_categories']['total_annotations'], 2278)
        self.assertEqual(report['annotation_categories']['known_category_annotations'], 2278)
        self.assertEqual(report['object_motion_states']['total_instances'], 154)
        self.assertEqual(report['object_motion_states']['uncertain_count'], 3)
        self.assertEqual(report['object_motion_states']['insufficient_observations_count'], 10)
        self.assertAlmostEqual(report['object_motion_states']['uncertain_fraction'], 3 / 154)
        self.assertEqual(report['semantic_routing']['unassigned'], 0)
        self.assertEqual(report['semantic_routing']['total_candidates'], 29)
        self.assertEqual(report['structural_quality']['total_candidates'], 6)
        self.assertEqual(report['structural_quality']['insufficient_fraction'], 1.)
        self.assertFalse(report['five_percent_claim']['model_candidate_target_met'])
        self.assertFalse(report['five_percent_claim']['structural_truth_rate_verified'])
        self.assertEqual(report['independent_labels']['blank_candidate_labels'], 29)
        self.assertEqual(report['independent_labels']['verified_candidate_labels'], 0)
        self.assertEqual(events, before)

    def test_detected_structure_and_write_permission_are_independent(self):
        events = source_events()
        structures = [e for e in events if e['map_layer']['predicted_layer'] == 'base_map_structure']
        for event in structures[:2]:
            event['model_change_decision'] = {'status': 'detected', 'detected': True}
            event['reliability']['status'] = 'supported_conflict'
        report = build_nuscenes_status_audit(RAW, DELIVERY, events)
        self.assertEqual(report['structural_quality']['detected'], 2)
        self.assertAlmostEqual(report['structural_quality']['insufficient_fraction'], 4 / 6)
        self.assertEqual(report['structural_write']['eligible'], 0)
        self.assertEqual(report['structural_write']['executed'], 0)
        self.assertFalse(report['five_percent_claim']['model_candidate_target_met'])
        for event in structures:
            event['model_change_decision'] = {'status': 'detected', 'detected': True}
            event['reliability']['status'] = 'supported_conflict'
        complete = build_nuscenes_status_audit(RAW, DELIVERY, events)
        self.assertTrue(complete['five_percent_claim']['model_candidate_target_met'])
        self.assertFalse(complete['five_percent_claim']['structural_truth_rate_verified'])

    def test_wrong_window_scene_duplicate_and_contradictory_status_are_rejected(self):
        for mutation in ('window', 'scene', 'duplicate', 'status'):
            with self.subTest(mutation=mutation):
                events = source_events()
                event = next(e for e in events if 'model_change_decision' in e)
                if mutation == 'window':
                    event['correspondence_policy']['window'][0] += 1
                elif mutation == 'scene':
                    event['scene_id'] = 'another-scene'
                elif mutation == 'duplicate':
                    events.append(deepcopy(event))
                else:
                    event['model_change_decision']['detected'] = True
                with self.assertRaises(ValueError):
                    build_nuscenes_status_audit(RAW, DELIVERY, events)

    def test_provenance_hashes_the_actual_audit_inputs(self):
        report = build_nuscenes_status_audit(RAW, DELIVERY, source_events())
        roots = {'raw_root': RAW, 'delivery_root': DELIVERY}
        self.assertEqual(report['source_files'], report['source_provenance'])
        for source in report['source_files']:
            path = roots[source['root']] / source['path']
            self.assertEqual(source['sha256'], sha256(path.read_bytes()).hexdigest())
            self.assertEqual(source['role'], 'audit_only')
        self.assertFalse(report['prediction_inputs_modified'])
        self.assertFalse(report['labels_used_for_model_prediction'])


if __name__ == '__main__':
    unittest.main()
