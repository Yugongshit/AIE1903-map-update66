"""Overlay geometry uses the applied patch and never edits the simulation map."""
from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from map_update.simulation_visuals import attach_update_overlay, dashed_segments, prepare_update_overlay


def line(x=100):
    return {"type": "ROAD_LINE_BROKEN_SINGLE_WHITE", "polyline": [[x, 200, 1.7], [x + 5, 200, 2.1]]}


class SimulationVisualsTests(unittest.TestCase):
    def test_replacement_offsets_once_and_keeps_source_unchanged(self):
        patch = [{"feature_id": "lane-1", "operation": "replace", "before": line(), "after": line(101)}]
        saved = deepcopy(patch)
        output = prepare_update_overlay(patch, source_to_runtime_offset=(-100, -200), synthetic=True)
        item = output["items"][0]
        self.assertEqual(item["paths"][0]["points"], [[0, 0], [5, 0]])
        self.assertEqual(item["paths"][1]["points"], [[1, 0], [6, 0]])
        self.assertEqual(item["label"], "DEMO U01")
        self.assertEqual(patch, saved)
        self.assertFalse(output["report"]["affects_physics_or_planning"])

    def test_add_and_delete_have_distinct_roles(self):
        log = [{"feature_id": "new", "operation": "add", "before": None, "after": line()},
               {"feature_id": "old", "operation": "delete", "before": line(), "after": None}]
        items = prepare_update_overlay(log)["items"]
        self.assertEqual([item["paths"][0]["role"] for item in items], ["updated", "removed"])
        self.assertEqual([item["paths"][0]["dashed"] for item in items], [False, True])
        self.assertEqual([item["label"] for item in items], ["U01", "U02"])

    def test_empty_patch_has_explicit_no_change_legend(self):
        output = prepare_update_overlay([])
        self.assertEqual(output["items"], [])
        self.assertIn("NO MAP CHANGES APPLIED", output["report"]["legend"])
        self.assertEqual(output["report"]["changed_features"], 0)

    def test_polygon_outline_is_closed_without_using_z(self):
        log = [{"feature_id": "lane", "operation": "add", "before": None,
                "after": {"type": "LANE_SURFACE_STREET", "polygon": [[0, 0, 9], [2, 0, 9], [2, 2, 9]]}}]
        path = prepare_update_overlay(log)["items"][0]["paths"][0]["points"]
        self.assertEqual(path, [[0, 0], [2, 0], [2, 2], [0, 0]])

    def test_dashes_continue_across_corners(self):
        segments = dashed_segments([[0, 0], [1, 0], [1, 1]])
        self.assertEqual(segments[0], [[0, 0], [.75, 0]])
        self.assertEqual(segments[1], [[1, .25], [1, 1]])

    def test_duplicate_and_invalid_patch_do_not_fabricate_annotations(self):
        valid = {"feature_id": "a", "operation": "add", "before": None, "after": line()}
        for patch in ([valid, valid], [{**valid, "operation": "unknown"}],
                      [{**valid, "before": line()}],
                      [{**valid, "after": {"polyline": [[0, 0], [math.nan, 1]]}}]):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                prepare_update_overlay(patch)

    def test_headless_handle_needs_no_panda_and_does_not_mutate_map(self):
        scenario = {"metadata": {"old_origin_in_current_coordinate": [-100, -200]},
                    "map_features": {"a": line()}}
        env = SimpleNamespace(engine=SimpleNamespace(mode="none", data_manager=SimpleNamespace(current_scenario=scenario)))
        saved = deepcopy(scenario)
        handle, report = attach_update_overlay(env, [], synthetic=False)
        handle.close()
        self.assertEqual(scenario, saved)
        self.assertEqual(report["changed_features"], 0)


if __name__ == "__main__":
    unittest.main()
