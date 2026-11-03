from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from map_update.delivery import read_csv
from map_update.output import write_scene
from map_update.scene import footprint, inspect_scene

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/"AIE1903 原始资料"


class OriginalSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = inspect_scene(RAW, ROOT/"external/perception_delivery")

    def test_frame_time_and_single_visit_are_preserved(self):
        self.assertEqual(len(self.scene["replay"]), 41)
        self.assertEqual(self.scene["replay"][0]["timestamp_ns"], "1537845352297764000")
        self.assertAlmostEqual(self.scene["replay"][-1]["elapsed_s"], 19.85016)
        self.assertEqual(self.scene["summary"]["independent_visits"], 1)

    def test_map_geometry_available_but_prior_role_and_m3_not_fabricated(self):
        m = self.scene["reference_map"]
        self.assertEqual(m["coordinate_reference"]["city_id"], "singapore-onenorth")
        self.assertEqual(len(m["elements"]), 157)
        self.assertEqual(m["map_role"], "reference_unverified_temporal_role")
        self.assertFalse(m["write_eligible"])
        self.assertEqual(self.scene["map_updates"], [])
        self.assertTrue(all(c["reliability"] is None for c in self.scene["candidates"]))
        self.assertTrue(all(c["valid_visibility_frames"] is None for c in self.scene["candidates"]))

    def test_all_candidate_geometry_counts_match_delivered_frames(self):
        self.assertEqual(self.scene["summary"]["frame_line_observations"], 57)
        self.assertEqual(self.scene["summary"]["occupancy_frame_events"], 311)
        for c in self.scene["candidates"]:
            self.assertEqual(len(c["frame_indices"]), c["observed_frames"])

    def test_lanes_preserve_ordered_per_frame_curve_not_pooled_points(self):
        frame = self.scene["replay"][11]
        curve = next(l for l in frame["observed_lines"] if l["line_id"] == 2)
        self.assertEqual(curve["candidate_ids"], ["S0980-LD-001"])
        original = read_csv(RAW/curve["source_file"])
        self.assertEqual(curve["geometry_city"]["coordinates"], [[float(p["global_x_m"]), float(p["global_y_m"])] for p in original])
        self.assertNotIn("S0980-LD-001", self.scene["replay"][13]["candidate_ids"])

    def test_occupancy_uses_member_exact_frame_and_oriented_polygon(self):
        first = next(o for o in self.scene["replay"][0]["occupancy"] if o["candidate_id"].endswith("O001"))
        self.assertEqual(len(first["objects"]), 6)
        self.assertNotIn(first["candidate_id"], self.scene["replay"][15]["candidate_ids"])
        self.assertAlmostEqual(first["lane_overlap_area_m2"], 3.3372)
        for obj in first["objects"]:
            ring = obj["geometry_city"]["coordinates"][0]
            self.assertEqual(len(ring), 5)
            self.assertEqual(ring[0], ring[-1])

    def test_rotation_keeps_footprint_area_and_rejects_invalid_sizes(self):
        row = {"x_global_m": "100", "y_global_m": "200", "length_m": "4", "width_m": "2", "yaw_global_deg": "37"}
        ring = footprint(row)["coordinates"][0]
        area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:])))/2
        self.assertAlmostEqual(area, 8, places=8)
        row["width_m"] = "0"
        with self.assertRaises(ValueError):
            footprint(row)

    def test_occ_map_foreign_keys_resolved_but_ld_targets_unknown(self):
        elements = {e["element_id"] for e in self.scene["reference_map"]["elements"]}
        for c in self.scene["candidates"]:
            if c["change_type"] == "occupancy":
                self.assertIn(c["map_element_id"], elements)
            else:
                self.assertIsNone(c["map_element_id"])

    def test_checks_include_source_hash_and_divider_correspondence(self):
        checks = self.scene["raw_validation"]["checks"]
        self.assertTrue(all(c["passed"] for c in checks))
        self.assertEqual(sum(c["check"].startswith("source_sha256:") for c in checks), 6)
        self.assertEqual(sum(c["check"].startswith("divider_matches_source_map:") for c in checks), 8)

    def test_output_distinguishes_reference_map_from_updated_map(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_scene(self.scene, Path(temp)/"scene")
            self.assertTrue((path/"reference_map.json").is_file())
            self.assertTrue((path/"replay.json").is_file())
            self.assertFalse((path/"map.json").exists())
            self.assertFalse((path/"planning.json").exists())


if __name__ == "__main__":
    unittest.main()
