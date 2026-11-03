from pathlib import Path
import json
import tempfile
import unittest

from map_update.delivery import inspect_delivery, read_csv
from map_update.output import write_intake


ROOT = Path(__file__).resolve().parents[1]/"external/perception_delivery"


class DeliveredDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = inspect_delivery(ROOT)

    def test_real_counts_and_all_integrity_checks_pass(self):
        self.assertEqual(self.data["summary"]["candidates"], 29)
        self.assertEqual(self.data["summary"]["families"], {"ROAD_OCCUPANCY": 23, "LANE_MARKING_GEOMETRY": 6})
        self.assertEqual(self.data["summary"]["associations"], {"associated": 5, "not_associated": 81, "uncertain": 89})
        self.assertTrue(all(c["passed"] for c in self.data["validation"]["checks"]))

    def test_no_visibility_denominator_or_m3_fabricated(self):
        self.assertTrue(all(c["valid_visibility_frames"] is None for c in self.data["candidates"]))
        self.assertTrue(all(c["reliability"] is None for c in self.data["candidates"]))
        self.assertTrue(any(c["support_frames"] > 10 for c in self.data["candidates"]))

    def test_no_geometry_or_map_or_absolute_time_fabricated(self):
        self.assertIsNone(self.data["map"])
        self.assertEqual(self.data["map_updates"], [])
        self.assertEqual(self.data["time_reference"], "clip_relative_seconds_only")
        for c in self.data["candidates"]:
            self.assertEqual(c["geometry"]["type"], "MultiPoint")
            self.assertFalse(c["write_eligible"])
            self.assertEqual(c["persistence_status"], "insufficient_evidence")
            self.assertIsNone(c["map_element_id"])

    def test_synthetic_tracks_kept_separate_despite_misleading_source_tag(self):
        actual = {r["track_id"] for r in self.data["vehicle_features"]}
        synthetic = {r["track_id"] for r in read_csv(ROOT/"outputs/stage7/synthetic_rule_checks/result/vehicle_features.csv")}
        self.assertEqual(len(actual), 13)
        self.assertEqual(synthetic, {"gap", "straight", "turn", "u_turn", "stationary"})
        self.assertFalse(actual & synthetic)

    def test_empty_annotation_templates_not_ground_truth(self):
        self.assertEqual(self.data["summary"]["labeled_rows"], {"candidate_evidence": 0, "behavior": 0, "association": 0})

    def test_intake_output_is_not_a_map_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = write_intake(self.data, Path(temp)/"intake")
            self.assertTrue((dest/"index.html").is_file())
            self.assertFalse((dest/"map.json").exists())
            self.assertFalse((dest/"planning.json").exists())
            self.assertEqual(json.loads((dest/"summary.json").read_text())["map_updates"], 0)


if __name__ == "__main__":
    unittest.main()
