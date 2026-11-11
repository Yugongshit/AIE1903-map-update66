"""The example planner must use updated directed geometry, not replay tracks."""
import copy
import math
import unittest

from map_update.simulation_planner import LaneGraphController, plan_route


def lane(points, exits=()):
    return {"type": "LANE_SURFACE_STREET", "polyline": points, "exit_lanes": list(exits)}


def network():
    return {
        "start": lane([[0, 0], [10, 0]], ["short", "detour"]),
        "short": lane([[10, 0], [20, 0]], ["end"]),
        "detour": lane([[10, 0], [10, 10], [20, 10], [20, 0]], ["end"]),
        "end": lane([[20, 0], [30, 0]]),
    }


class PlannerTests(unittest.TestCase):
    def test_updated_topology_changes_route(self):
        features = network()
        original = plan_route(features, [2, 0], [28, 0], heading_rad=0)
        self.assertEqual(original["lane_ids"], ["start", "short", "end"])
        features["start"]["exit_lanes"] = ["detour"]
        updated = plan_route(features, [2, 0], [28, 0], heading_rad=0)
        self.assertEqual(updated["lane_ids"], ["start", "detour", "end"])
        self.assertGreater(updated["length_m"], original["length_m"])
        self.assertFalse(updated["uses_recorded_trajectory"])

    def test_geometry_update_changes_waypoints(self):
        features = network()
        features["short"]["polyline"] = [[10, 0], [15, 1], [20, 0]]
        plan = plan_route(features, [2, 0], [28, 0])
        self.assertIn([15.0, 1.0], plan["points"])
        self.assertEqual(plan["points"][0], [2.0, 0.0])
        self.assertEqual(plan["points"][-1], [28.0, 0.0])

    def test_bus_lane_update_blocks_ordinary_car_but_preserves_bus_route(self):
        features = network()
        features["start"]["exit_lanes"] = ["short"]
        for feature in features.values():
            feature.update(source_lane_type="VEHICLE", vehicle_access=["vehicle", "bus"])
        before = plan_route(features, [2, 0], [28, 0])
        self.assertEqual(before["lane_ids"], ["start", "short", "end"])
        features["short"].update(source_lane_type="BUS", vehicle_access=["bus"])
        after = plan_route(features, [2, 0], [28, 0])
        self.assertEqual(after["status"], "no_route")
        self.assertEqual(after["reason"], "no_directed_path_to_goal")
        self.assertEqual(after["access_excluded_lane_ids"], ["short"])
        bus = plan_route(features, [2, 0], [28, 0], vehicle_class="bus")
        self.assertEqual(bus["lane_ids"], before["lane_ids"])

    def test_bus_lane_update_routes_ordinary_vehicle_over_existing_detour(self):
        features = network()
        features["short"].update(source_lane_type="BUS", vehicle_access=["bus"])
        car = plan_route(features, [2, 0], [28, 0])
        self.assertEqual(car["lane_ids"], ["start", "detour", "end"])
        self.assertEqual(car["vehicle_class"], "vehicle")

    def test_unknown_or_conflicting_permissions_do_not_create_access(self):
        for restriction in (
                {"source_lane_type": "UNKNOWN"},
                {"source_lane_type": None},
                {"source_lane_type": {"type": "VEHICLE"}},
                {"vehicle_access": None},
                {"vehicle_access": "vehicle"},
                {"vehicle_access": ["unknown"]},
                {"vehicle_access": ["vehicle", "unknown"]},
                {"source_lane_type": "UNKNOWN", "vehicle_access": ["vehicle"]},
                {"source_lane_type": "BUS", "vehicle_access": ["vehicle"]},
                {"source_lane_type": "VEHICLE", "vehicle_access": []}):
            with self.subTest(restriction=restriction):
                features = {"only": dict(lane([[0, 0], [10, 0]]), **restriction)}
                result = plan_route(features, [2, 0], [8, 0])
                self.assertEqual(result["status"], "no_route")
                self.assertEqual(result["reason"], "no_accessible_lane_centerlines")

    def test_source_lane_use_is_kept_when_access_list_is_absent(self):
        features = {"only": dict(lane([[0, 0], [10, 0]]), source_lane_type="BUS")}
        self.assertEqual(plan_route(features, [2, 0], [8, 0])["status"], "no_route")
        self.assertEqual(plan_route(features, [2, 0], [8, 0], vehicle_class="bus")["status"], "planned")

    def test_bike_lane_is_closed_to_both_vehicle_classes(self):
        features = {"only": dict(lane([[0, 0], [10, 0]]),
                                 source_lane_type="BIKE", vehicle_access=["bicycle"])}
        for vehicle_class in ("vehicle", "bus"):
            self.assertEqual(plan_route(features, [2, 0], [8, 0], vehicle_class=vehicle_class)["status"], "no_route")

    def test_invalid_vehicle_classes_are_rejected(self):
        for vehicle_class in (None, "VEHICLE", "bicycle", "unknown", ["vehicle"]):
            with self.subTest(vehicle_class=vehicle_class):
                with self.assertRaises(ValueError):
                    plan_route(network(), [2, 0], [28, 0], vehicle_class=vehicle_class)
                with self.assertRaises(ValueError):
                    LaneGraphController(vehicle_class=vehicle_class)

    def test_disconnected_goal_no_route(self):
        features = network()
        features["start"]["exit_lanes"] = []
        self.assertEqual(plan_route(features, [2, 0], [28, 0])["status"], "no_route")

    def test_no_reverse_or_invented_lane_change(self):
        features = {"a": lane([[0, 0], [10, 0]])}
        result = plan_route(features, [8, 0], [2, 0], heading_rad=0)
        self.assertEqual(result["reason"], "goal_behind_on_same_lane")

    def test_do_not_bridge_geometric_hole(self):
        features = {"a": lane([[0, 0], [10, 0]], ["b"]),
                    "b": lane([[100, 0], [120, 0]])}
        self.assertEqual(plan_route(features, [2, 0], [118, 0])["status"], "no_route")

    def test_heading_filters_opposite_direction(self):
        features = {"forward": lane([[0, 0], [10, 0]]),
                    "backward": lane([[10, 1], [0, 1]])}
        result = plan_route(features, [2, 0.6], [8, 0], heading_rad=0)
        self.assertEqual(result["lane_ids"], ["forward"])

    def test_invalid_or_distant_goal(self):
        with self.assertRaises(ValueError):
            plan_route(network(), [0, 0], [math.nan, 0])
        self.assertEqual(plan_route(network(), [2, 0], [100, 100])["status"], "no_route")

    def state(self, features=None):
        return {"map_features_runtime": features if features is not None else network(),
                "position_runtime_m": [2, 0], "runtime_origin_in_scenario_m": [100, 200],
                "goal_scenario_m": [128, 200], "heading_rad": 0, "speed_m_s": 0,
                "scenario_id": "example", "step": 0,
                "wheelbase_m": 2.8, "max_steering_rad": math.radians(60)}

    def test_runtime_goal_translation_and_control(self):
        controller = LaneGraphController()
        command = controller(self.state())
        self.assertEqual(command[0], 0)
        self.assertGreater(command[1], 0)
        self.assertEqual(controller.diagnostics()["requested_goal"], [28.0, 0.0])

    def test_no_route_brakes_and_goal_changes_replan(self):
        controller = LaneGraphController()
        state = self.state()
        state["goal_scenario_m"] = [500, 500]
        self.assertEqual(controller(state), [0, -1])
        state["step"] = 1
        state["goal_scenario_m"] = [128, 200]
        self.assertGreater(controller(state)[1], 0)

    def test_new_map_uses_new_route_and_revision_invalidates_cache(self):
        controller = LaneGraphController()
        state = self.state()
        controller(state)
        state["step"] = 1
        state["map_features_runtime"] = copy.deepcopy(state["map_features_runtime"])
        state["map_features_runtime"]["start"]["exit_lanes"] = ["detour"]
        controller(state)
        self.assertIn("detour", controller.diagnostics()["lane_ids"])
        state["step"], state["map_version"] = 2, "v2"
        state["map_features_runtime"]["start"]["exit_lanes"] = []
        self.assertEqual(controller(state), [0, -1])

    def test_lane_use_update_changes_controller_plan_and_bus_class_is_explicit(self):
        features = network()
        features["start"]["exit_lanes"] = ["short"]
        state = self.state(features)
        controller = LaneGraphController()
        self.assertGreater(controller(state)[1], 0)
        state["step"], state["map_version"] = 1, "bus-update"
        features["short"].update(source_lane_type="BUS", vehicle_access=["bus"])
        self.assertEqual(controller(state), [0, -1])
        self.assertEqual(controller.diagnostics()["access_excluded_lane_ids"], ["short"])
        bus_controller = LaneGraphController(vehicle_class="bus")
        self.assertGreater(bus_controller(state)[1], 0)
        self.assertEqual(bus_controller.diagnostics()["lane_ids"], ["start", "short", "end"])
        self.assertEqual(bus_controller.diagnostics()["vehicle_class"], "bus")

    def test_reaching_goal_brakes(self):
        controller = LaneGraphController()
        state = self.state()
        controller(state)
        state["step"], state["position_runtime_m"] = 1, [27, 0]
        self.assertEqual(controller(state), [0, -1])

    def test_arrival_matches_custom_goal_radius(self):
        for radius in (2.0, 0.5, 0.1):
            with self.subTest(radius=radius):
                controller = LaneGraphController()
                state = self.state()
                state["goal_radius_m"] = radius
                controller(state)
                state["step"], state["position_runtime_m"] = 1, [28-radius-0.05, 0]
                self.assertGreater(controller(state)[1], 0)
                self.assertEqual(controller.diagnostics()["status"], "tracking")
                state["step"], state["position_runtime_m"] = 2, [28-radius+0.01, 0]
                self.assertEqual(controller(state), [0, -1])
                self.assertEqual(controller.diagnostics()["status"], "goal_reached")

    def test_small_radius_does_not_stop_at_endpoint_standoff(self):
        controller = LaneGraphController()
        state = self.state()
        state["goal_radius_m"] = 0.1
        controller(state)
        state["step"], state["position_runtime_m"] = 1, [27.8, 0]
        self.assertGreater(controller(state)[1], 0)
        self.assertEqual(controller.diagnostics()["status"], "tracking")

    def test_off_center_goal_outside_radius_is_not_false_arrival(self):
        controller = LaneGraphController()
        state = self.state({"a": lane([[0, 0], [30, 0]])})
        state["position_runtime_m"] = [10, 0]
        state["goal_scenario_m"] = [110, 202.5]
        self.assertEqual(controller(state), [0, -1])
        self.assertEqual(controller.diagnostics()["status"], "no_route")
        self.assertEqual(controller.diagnostics()["reason"], "goal_radius_does_not_reach_lane_centerline")

    def test_off_center_goal_inside_radius_and_radius_change(self):
        controller = LaneGraphController()
        state = self.state()
        state["goal_scenario_m"] = [128, 200.6]
        self.assertGreater(controller(state)[1], 0)
        state["step"], state["goal_radius_m"] = 1, 0.5
        self.assertEqual(controller(state), [0, -1])
        self.assertEqual(controller.diagnostics()["status"], "no_route")

    def test_invalid_goal_radius_rejected(self):
        for radius in (0, -1, math.nan):
            with self.subTest(radius=radius):
                state = self.state()
                state["goal_radius_m"] = radius
                with self.assertRaises(ValueError):
                    LaneGraphController()(state)

    def test_commands_finite_and_bounded(self):
        controller = LaneGraphController()
        state = self.state()
        state["position_runtime_m"] = [2, 1]
        state["speed_m_s"] = 20
        command = controller(state)
        self.assertTrue(all(math.isfinite(x) and -1 <= x <= 1 for x in command))
        self.assertLess(command[1], 0)


if __name__ == "__main__":
    unittest.main()
