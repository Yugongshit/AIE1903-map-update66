"""Map delivery causality and actual per-vehicle obstacle-aware source routes."""
import copy
import math
from pathlib import Path
import unittest

from map_update.simulation_planner import _length, _point_at, _project, plan_route
from map_update.simulation_shared_map import SharedMapService


def lane(points, exits=()):
    return {"type": "LANE_SURFACE_STREET", "polyline": points, "exit_lanes": list(exits)}


def network():
    return {"start": lane([[0, 0], [10, 0]], ["short", "detour"]),
            "short": lane([[10, 0], [20, 0]], ["end"]),
            "detour": lane([[10, 0], [10, 10], [20, 10], [20, 0]], ["end"]),
            "end": lane([[20, 0], [30, 0]])}


def obstacle(x=15, y=0, **extra):
    return {"id": "barrier", "position": [x, y], "length_m": 1, "width_m": 2,
            "status": "observed_active", "last_observed_timestamp_ns": "100", **extra}


def pose(x=2, y=0, step=0, **extra):
    return {"position": [x, y], "step": step, "heading_rad": 0, "speed_m_s": 0,
            "width_m": 1.8, "length_m": 4.5, **extra}


class SharedMapTests(unittest.TestCase):
    def service(self, **kwargs):
        features = network()
        return SharedMapService(features, features, scene_id="fixture", **kwargs)

    def test_each_vehicle_has_own_goal_controller_and_delivered_knowledge(self):
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        service.subscribe("C", [8, 0])
        service.deliver("B", 0)
        service.deliver("C", 0)
        service.action("B", pose())
        service.action("C", pose())
        service.publish(2, state_objects=[obstacle()], source_timestamp_ns=100)
        service.deliver("B", 2)
        service.action("B", pose(step=2))
        service.action("C", pose(step=2))
        status = service.status()["per_vehicle"]
        self.assertEqual(status["B"]["current_route"]["lane_ids"], ["start", "detour", "end"])
        self.assertEqual(status["C"]["current_route"]["lane_ids"], ["start"])
        self.assertEqual(status["B"]["known_state_objects"], 1)
        self.assertEqual(status["C"]["known_state_objects"], 0)
        self.assertEqual(status["B"]["replan_count"], 2)
        self.assertEqual(status["C"]["replan_count"], 1)
        self.assertEqual(status["B"]["previous_route"]["lane_ids"], ["start", "short", "end"])

    def test_duplicate_old_and_future_deliveries_cannot_change_receiver(self):
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        old = service.latest_message()
        self.assertTrue(service.deliver("B", 0))
        self.assertFalse(service.deliver("B", 0))
        new = service.publish(2, state_objects=[obstacle()], source_timestamp_ns=100)
        with self.assertRaisesRegex(ValueError, "future"):
            service.receive("B", new, 1)
        self.assertTrue(service.receive("B", new, 2))
        self.assertFalse(service.receive("B", old, 3))
        forged = copy.deepcopy(new)
        forged["map_features"]["start"]["exit_lanes"] = []
        with self.assertRaisesRegex(ValueError, "version"):
            service.receive("B", forged, 3)

    def test_disconnect_retains_map_reconnect_delivers_latest(self):
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        service.deliver("B", 0)
        service.set_subscription("B", False)
        service.publish(1, state_objects=[obstacle()], source_timestamp_ns=100)
        self.assertFalse(service.deliver("B", 1))
        self.assertEqual(service.vehicle_status("B")["known_state_objects"], 0)
        service.set_subscription("B", True)
        self.assertTrue(service.deliver("B", 2))
        self.assertEqual(service.vehicle_status("B")["received_step"], 2)

    def test_online_starts_original_until_explicit_structural_publication(self):
        original = network()
        updated = copy.deepcopy(original)
        updated["start"]["exit_lanes"] = ["detour"]
        service = SharedMapService(original, updated, scene_id="fixture", mode="online")
        service.subscribe("B", [28, 0])
        service.deliver("B", 0)
        service.action("B", pose())
        self.assertEqual(service.vehicle_status("B")["current_route"]["lane_ids"], ["start", "short", "end"])
        service.publish(1, publish_structure=True)
        service.deliver("B", 1)
        service.action("B", pose(step=1))
        status = service.vehicle_status("B")
        self.assertEqual(status["current_route"]["lane_ids"], ["start", "detour", "end"])
        self.assertEqual(status["last_replan_reason"], "map_structure_received")

    def test_online_rejects_preview_future_observations_and_offline_prior(self):
        service = self.service(mode="online")
        for objects, extra in [([obstacle(scope="full_scene_overview")], {}),
                               ([obstacle(last_observed_timestamp_ns="101")], {}),
                               ([obstacle()], {"offline_prior": True}),
                               ([obstacle(planning_eligible=False)], {})]:
            with self.subTest(objects=objects, extra=extra), self.assertRaises(ValueError):
                service.publish(1, state_objects=objects, source_timestamp_ns=100, **extra)
        with self.assertRaises(ValueError):
            service.publish(1, observed_step=2)

    def test_offline_variants_change_only_the_declared_received_layers(self):
        original = network()
        updated = copy.deepcopy(original)
        updated["short"]["polyline"] = [[10, 0], [15, 0.1], [20, 0]]
        for variant, map_key, object_count in [("old", "original", 0), ("structure", "updated", 0), ("full", "updated", 1)]:
            service = SharedMapService(original, updated, scene_id="fixture", variant=variant)
            service.subscribe("B", [28, 0])
            service.publish(0, state_objects=[obstacle(scope="full_scene_overview")], offline_prior=True)
            service.deliver("B", 0)
            received = service.get_received_snapshot("B")
            self.assertEqual(received["map_key"], map_key)
            self.assertEqual(len(received["state_objects"]), object_count)
            self.assertTrue(received["offline_prior"])

    def test_semantic_deduplication_does_not_replan_for_clock_or_frame_metadata(self):
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        service.publish(0, state_objects=[obstacle(source_frame_id="0")], source_timestamp_ns=100)
        service.deliver("B", 0)
        service.action("B", pose())
        version = service.vehicle_status("B")["received_version"]
        result = service.publish(1, state_objects=[obstacle(source_frame_id="1", last_observed_timestamp_ns="200")], source_timestamp_ns=200)
        self.assertIsNone(result)
        self.assertFalse(service.deliver("B", 1))
        service.action("B", pose(step=1))
        self.assertEqual(service.vehicle_status("B")["received_version"], version)
        self.assertEqual(service.vehicle_status("B")["replan_count"], 1)

    def test_blocked_lane_has_real_detour_or_stops_without_snapping_elsewhere(self):
        for detour in (True, False):
            features = network()
            if not detour:
                features["start"]["exit_lanes"] = ["short"]
            service = SharedMapService(features, features, scene_id="fixture")
            service.subscribe("B", [28, 0])
            service.publish(0, state_objects=[obstacle()], offline_prior=True)
            service.deliver("B", 0)
            action = service.action("B", pose())
            plan = service.vehicle_status("B")["current_route"]
            if detour:
                self.assertEqual(plan["lane_ids"], ["start", "detour", "end"])
                self.assertGreater(action[1], 0)
            else:
                self.assertEqual(plan["status"], "no_route")
                self.assertEqual(action, [0, -1])
                self.assertEqual(plan["reason"], "shared_state_no_directed_detour")

    def test_unrelated_received_object_does_not_rebuild_route_but_clearance_does(self):
        from unittest.mock import patch
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        service.publish(0, state_objects=[obstacle()], source_timestamp_ns=100)
        service.deliver("B", 0)
        service.action("B", pose())
        service.publish(1, state_objects=[obstacle(), obstacle(x=100, id="remote")], source_timestamp_ns=100)
        service.deliver("B", 1)
        with patch("map_update.simulation_planner.plan_route", side_effect=AssertionError("Unexpected controller replan")):
            service.action("B", pose(step=1))
        self.assertEqual(service.vehicle_status("B")["known_state_objects"], 2)
        self.assertEqual(service.vehicle_status("B")["replan_count"], 1)
        self.assertFalse(service.vehicle_status("B")["last_delivery_affects_route"])
        service.publish(2, state_objects=[obstacle(status="cleared")], source_timestamp_ns=100)
        service.deliver("B", 2)
        service.action("B", pose(step=2))
        status = service.vehicle_status("B")
        self.assertEqual(status["replan_count"], 2)
        self.assertEqual(status["current_route"]["lane_ids"], ["start", "short", "end"])

    def test_unchanged_delivery_fast_path_does_not_copy_or_hash_map(self):
        from unittest.mock import patch
        service = self.service()
        service.subscribe("B", [28, 0])
        service.deliver("B", 0)
        with patch("map_update.simulation_shared_map._plain", side_effect=AssertionError("Unexpected map copy")):
            for step in range(1, 20):
                self.assertFalse(service.deliver("B", step))

    def test_blocked_endpoint_never_reassigns_goal_to_parallel_lane(self):
        features = {"main": lane([[0, 0], [30, 0]]), "parallel": lane([[0, 2], [30, 2]])}
        service = SharedMapService(features, features, scene_id="fixture")
        service.subscribe("B", [28, 0])
        service.publish(0, state_objects=[obstacle()], offline_prior=True)
        service.deliver("B", 0)
        self.assertEqual(service.action("B", pose()), [0, -1])
        self.assertEqual(service.vehicle_status("B")["current_route"]["lane_ids"], [])

    def test_roadside_behind_and_beyond_goal_objects_do_not_close_route(self):
        for obj in [obstacle(y=5), obstacle(x=29), obstacle(x=0)]:
            features = {"only": lane([[0, 0], [30, 0]])}
            service = SharedMapService(features, features, scene_id="fixture")
            service.subscribe("B", [20, 0])
            service.publish(0, state_objects=[obj], offline_prior=True)
            service.deliver("B", 0)
            self.assertGreater(service.action("B", pose())[1], 0)
            self.assertEqual(service.vehicle_status("B")["current_route"]["blocked_lane_ids"], [])

    def test_local_actor_brakes_old_map_car_and_is_separate_from_shared_knowledge(self):
        service = self.service(variant="old")
        service.subscribe("B", [28, 0])
        service.deliver("B", 0)
        action = service.action("B", pose(speed_m_s=3, nearby_actors=[obstacle(x=6)]))
        self.assertEqual(action[1], -1)
        status = service.vehicle_status("B")
        self.assertIsNone(status["braking"]["shared_map"])
        self.assertEqual(status["braking"]["local_actor"]["object_id"], "barrier")
        self.assertEqual(status["known_state_objects"], 0)

    def test_exports_are_receiver_bound_deep_copies_without_global_future(self):
        service = self.service(mode="online")
        service.subscribe("B", [28, 0])
        service.deliver("B", 0)
        service.publish(1, state_objects=[obstacle()], source_timestamp_ns=100)
        export = service.planning_input("B", pose(step=1, future_recorded_tracks=["must not export"]))
        self.assertEqual(export["state_objects"], [])
        self.assertNotIn("future_recorded_tracks", export["vehicle_state"])
        export["map_features"]["start"]["exit_lanes"] = []
        self.assertEqual(service.get_received_snapshot("B")["map_features"]["start"]["exit_lanes"], ["short", "detour"])

    def test_unsubscribed_car_initial_planning_does_not_get_updated_map(self):
        original = network()
        updated = copy.deepcopy(original)
        updated["start"]["exit_lanes"] = ["detour"]
        service = SharedMapService(original, updated, scene_id="fixture")
        service.subscribe("B", [28, 0], enabled=False)
        self.assertFalse(service.deliver("B", 0))
        service.action("B", pose())
        status = service.vehicle_status("B")
        self.assertIsNone(status["received_version"])
        self.assertEqual(status["current_route"]["lane_ids"], ["start", "short", "end"])

    def test_real_nuscenes_lane_graph_is_used_without_recorded_tracks(self):
        from map_update.simulation_platform_data import load_scene
        payload = load_scene(Path(__file__).resolve().parents[1], "nuscenes-scene0980")
        features = payload["maps"]["original"]
        for feature in features.values():
            points = feature.get("polyline", [])
            if feature.get("type") != "LANE_SURFACE_STREET" or len(points) < 2 or _length(points) < 20:
                continue
            start = _point_at(points, _length(points)*0.2)
            goal = _point_at(points, _length(points)*0.8)
            heading = _project(start, points)[3]
            expected = plan_route(features, start, goal, heading_rad=heading)
            if expected["status"] == "planned":
                break
        else:
            self.fail("Source scene needs an actual accessible mission")
        service = SharedMapService(features, payload["maps"]["updated"], scene_id=payload["scene_id"], variant="old")
        service.subscribe("B", goal)
        service.deliver("B", 0)
        service.action("B", pose(x=start[0], y=start[1], heading_rad=heading))
        route = service.vehicle_status("B")["current_route"]
        self.assertEqual(route["lane_ids"], expected["lane_ids"])
        self.assertEqual(route["points"], expected["points"])
        self.assertFalse(route["uses_recorded_trajectory"])
        self.assertTrue(all(key in features for key in route["lane_ids"]))


if __name__ == "__main__":
    unittest.main()
