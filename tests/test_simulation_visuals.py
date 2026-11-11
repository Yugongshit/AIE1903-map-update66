"""Overlay geometry uses the applied patch and never edits the simulation map."""
from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from map_update.simulation_visuals import UpdateOverlay, attach_update_overlay, dashed_segments, prepare_update_overlay


def line(x=100):
    return {"type": "ROAD_LINE_BROKEN_SINGLE_WHITE", "polyline": [[x, 200, 1.7], [x + 5, 200, 2.1]]}


class SimulationVisualsTests(unittest.TestCase):
    def test_world_labels_follow_selection_without_hiding_changed_geometry(self):
        class Node:
            def show(self): self.visible = True
            def hide(self): self.visible = False
        patch = [{'feature_id': name, 'operation': 'replace', 'before': line(), 'after': line(101)}
                 for name in ('a', 'b')]
        nodes = {name: Node() for name in ('a', 'b')}
        handle = UpdateOverlay(prepare_update_overlay(patch), patch_log=patch, label_nodes=nodes)
        self.assertTrue(nodes['a'].visible)
        self.assertFalse(nodes['b'].visible)
        handle.select_change(1)
        self.assertFalse(nodes['a'].visible)
        self.assertTrue(nodes['b'].visible)
        self.assertEqual(len(handle.prepared['items']), 2)

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

    def test_comparison_stays_on_edit_when_car_moves_and_selection_wraps(self):
        log = [{"feature_id": "first", "operation": "replace", "before": line(), "after": line(101)},
               {"feature_id": "second", "operation": "add", "before": None, "after": line(150)}]
        prepared = prepare_update_overlay(log, source_to_runtime_offset=(-100, -200), synthetic=True)
        env = SimpleNamespace(agent=SimpleNamespace(position=[0, 0]))
        handle = UpdateOverlay(prepared, env=env, patch_log=log)
        bounds = deepcopy(handle.comparison["viewport"]["bounds_xyxy"])
        handle.refresh()
        first_distance = handle.distance_m
        env.agent.position = [100, 0]
        handle.refresh()
        self.assertEqual(handle.comparison["viewport"]["bounds_xyxy"], bounds)
        self.assertGreater(handle.distance_m, first_distance)
        self.assertIn("dx +1.00 m", handle._summary())
        self.assertEqual(handle.comparison["before"]["paths"][0]["role"], "previous")
        self.assertEqual(handle.comparison["after"]["paths"][0]["role"], "updated")
        handle.select_change(-1)
        self.assertEqual(handle.comparison["feature_id"], "second")
        self.assertFalse(handle.comparison["before"]["present"])
        handle.select_change(1)
        self.assertEqual(handle.comparison["feature_id"], "first")
        self.assertEqual(handle.comparison["viewport"]["bounds_xyxy"], bounds)
        handle.close()

    def test_focus_only_moves_camera_in_already_centered_coordinates(self):
        log = [{"feature_id": "first", "operation": "replace", "before": line(), "after": line(101)}]
        prepared = prepare_update_overlay(log, source_to_runtime_offset=(-100, -200))
        calls = []
        camera = SimpleNamespace(
            stop_track=lambda **kwargs: calls.append(("stop_track", kwargs)),
            set_bird_view_pos=lambda position: calls.append(("bird", position)),
            camera=SimpleNamespace(setPos=lambda *p: calls.append(("position", p)),
                                   lookAt=lambda *p: calls.append(("look", p))))
        lens = SimpleNamespace(getFov=lambda: (60, 45))
        env = SimpleNamespace(main_camera=camera, episode_step=137,
                              config={"agent_policy": "ReplayEgoCarPolicy", "manual_control": False},
                              agent=SimpleNamespace(position=[-20, -60]),
                              engine=SimpleNamespace(cam=SimpleNamespace(node=lambda: SimpleNamespace(getLens=lambda: lens))))
        config, position = deepcopy(env.config), list(env.agent.position)
        handle = UpdateOverlay(prepared, env=env, patch_log=log)
        self.assertTrue(handle.focus_selected())
        self.assertEqual(calls[0], ("stop_track", {"bird_view_on_current_position": False}))
        self.assertEqual(calls[1][1][:2], [3, 0])
        self.assertGreaterEqual(calls[1][1][2], 12)
        self.assertEqual(env.config, config)
        self.assertEqual(env.agent.position, position)
        self.assertEqual(env.episode_step, 137)
        handle.close()

    def test_empty_comparison_cannot_focus_or_invent_an_edit(self):
        handle = UpdateOverlay(prepare_update_overlay([]))
        handle.select_change(1)
        self.assertFalse(handle.focus_selected())
        self.assertTrue(handle.comparison["empty"])
        self.assertIsNone(handle.prepared["report"]["comparison_ui"]["selected_feature_id"])
        handle.close()

    def test_attribute_only_edit_displays_real_difference_and_reports_full_ids(self):
        before = {**line(), "exit_lanes": ["long-original-lane-id", "unchanged-lane-id"]}
        after = {**line(), "exit_lanes": ["unchanged-lane-id", "long-new-lane-id", "second-new-lane-id"]}
        log = [{"feature_id": "lane", "operation": "replace", "before": before, "after": after}]
        handle = UpdateOverlay(prepare_update_overlay(log), patch_log=log)
        self.assertEqual(handle._summary(), "Exit Lanes: 2 -> 3 (+2 / -1)")
        change = handle.prepared["report"]["comparison_ui"]["attribute_changes"][0]
        self.assertEqual(change["before"], before["exit_lanes"])
        self.assertEqual(change["after"], after["exit_lanes"])
        handle.close()
        after = {**line(), "type": "ROAD_LINE_SOLID_SINGLE_WHITE"}
        log = [{"feature_id": "line", "operation": "replace", "before": line(), "after": after}]
        handle = UpdateOverlay(prepare_update_overlay(log), patch_log=log)
        self.assertIn("Type: broken single white -> solid single white", handle._summary())
        self.assertNotIn("below", handle._summary())
        handle.close()


if __name__ == "__main__":
    unittest.main()
