"""Meaningful audit failures for native shared-map comparison reports."""
from copy import deepcopy
import unittest

from scripts.validate_simulation_fleet import comparison_checks, route_errors, summarize_vehicle


class FleetValidationTests(unittest.TestCase):
    def test_route_audit_rejects_invented_and_deleted_connections(self):
        features = {"a": {"polyline": [[0, 0], [10, 0]], "exit_lanes": ["b"]},
                    "b": {"polyline": [[10, 0], [20, 0]], "exit_lanes": []},
                    "c": {"polyline": [[10, 0], [20, 1]], "exit_lanes": []}}
        self.assertEqual(route_errors({"lane_ids": ["a", "b"]}, features), [])
        self.assertIn("invented_connection:a->c", route_errors({"lane_ids": ["a", "c"]}, features))
        self.assertIn("missing_lane:removed", route_errors({"lane_ids": ["a", "removed"]}, features))
        features["b"]["polyline"][0] = [16, 0]
        self.assertIn("disconnected_geometry:a->b", route_errors({"lane_ids": ["a", "b"]}, features))

    def test_summary_uses_actual_positions_and_captures_transient_collision(self):
        route = {"status": "tracking", "lane_ids": ["a"]}
        initial = {"current_route": route}
        final = {"actual_trace": [{"step": 0, "position_city_m": [0, 0], "speed_m_s": 0},
                                   {"step": 1, "position_city_m": [3, 4], "speed_m_s": 1},
                                   {"step": 2, "position_city_m": [6, 4], "speed_m_s": 0}],
                 "current_route": route, "goal_city_m": [10, 4], "motion_source": "metadrive_bullet_physics",
                 "uses_recorded_trajectory": False, "stopped_reason": "local_actor_braking_horizon",
                 "received_version": "version", "received_step": 0, "known_state_objects": 2,
                 "replan_count": 1, "last_replan_reason": "initial_delivery"}
        observations = [{"step": i, "current_route": route, "map_key": "original",
                         "stopped_reason": None if i == 0 else "local_actor_braking_horizon",
                         "crash_vehicle": i == 1, "crash_object": False} for i in range(3)]
        summary = summarize_vehicle(initial, final, observations, {"original": {"a": {}}}, .1)
        self.assertAlmostEqual(summary["distance_travelled_m"], 8)
        self.assertAlmostEqual(summary["net_displacement_m"], 52 ** .5)
        self.assertTrue(summary["collision_detected"])
        self.assertEqual(summary["first_collision_step"], 1)
        self.assertEqual(summary["first_braking_step"], 1)
        self.assertEqual(summary["goal_distance_m"], 4)

    def test_comparison_detects_different_physical_world_and_wrong_export_pose(self):
        case = {"mode": "offline", "variant": "old", "physical_map_sha256": "same",
                "missions": [{"goal": [1, 2]}], "planning_inputs_match_actual_car_and_frame": True,
                "vehicles": {"B": {"route_errors": [], "motion_source": "metadrive_bullet_physics",
                                     "uses_recorded_trajectory": False, "collision_detected": False}}}
        other = deepcopy(case)
        other["variant"] = "full"
        self.assertTrue(all(comparison_checks([case, other]).values()))
        other["physical_map_sha256"] = "different"
        other["planning_inputs_match_actual_car_and_frame"] = False
        checks = comparison_checks([case, other])
        self.assertFalse(checks["same_physical_map"])
        self.assertFalse(checks["frame_bound_own_vehicle_inputs"])
        other["vehicles"]["B"]["collision_detected"] = True
        self.assertFalse(comparison_checks([case, other])["no_recipient_collision"])


if __name__ == "__main__":
    unittest.main()
