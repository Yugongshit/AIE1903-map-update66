"""Interactive traffic presets are source-connected and clear at initial spawn."""
from copy import deepcopy
import math
from pathlib import Path
import unittest

from map_update.simulation_bridge import feature_sha256
from map_update.simulation_interactive_scenarios import (
    choose_interactive_missions, interactive_profile, vehicle_footprint,
)
from map_update.simulation_platform_data import load_scene


ROOT = Path(__file__).resolve().parents[1]


class InteractiveScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = [load_scene(ROOT, key) for key in ("nuscenes-scene0980", "argoverse-winter2021")]
        cls.missions = [choose_interactive_missions(scene) for scene in cls.scenes]

    def test_five_independent_goals_and_p1_near_ego_in_front(self):
        for missions in self.missions:
            self.assertEqual([m["vehicle_id"] for m in missions], ["P1", "P2", "P3", "P4", "P5"])
            self.assertEqual(len({tuple(m["goal_scenario_m"]) for m in missions}), 5)
            self.assertGreater(missions[0]["spawn_ahead_of_ego_m"], 15.)
            self.assertLess(missions[0]["spawn_ahead_of_ego_m"], 25.)
            self.assertTrue(all(m["spawn_distance_to_ego_m"] < 35 for m in missions))
            self.assertTrue(all(m["source_route_length_m"] > 20 for m in missions))

    def test_native_sized_spawn_envelopes_clear_ego_static_world_and_each_other(self):
        self.assertEqual(len(self.scenes[0]["static_overview_actors"]), 103)
        for scene, missions in zip(self.scenes, self.missions):
            ego = scene["tracks"][scene["ego_track_id"]]
            occupied = [vehicle_footprint(ego["position"][0], ego["heading"][0])]
            occupied.extend(vehicle_footprint(a["position"], a["heading"], a["length"], a["width"], margin=0.)
                            for a in scene["static_overview_actors"])
            for mission in missions:
                geometry = vehicle_footprint(mission["position_scenario_m"], mission["heading_rad"])
                self.assertTrue(all(not geometry.intersects(other) for other in occupied))
                self.assertGreater(mission["initial_clearance_m"], 0.)
                occupied.append(geometry)

    def test_every_route_and_patrol_leg_uses_accessible_source_connections(self):
        for scene, missions in zip(self.scenes, self.missions):
            features = scene["maps"]["updated"]
            for mission in missions:
                self.assertEqual(mission["patrol_goals_scenario_m"][0], mission["goal_scenario_m"])
                self.assertEqual(len(mission["patrol_lane_routes"]), 2 if mission["patrol_loop"] else 1)
                for route in [mission["source_lane_ids"], *mission["patrol_lane_routes"]]:
                    self.assertGreater(len(route), 1)
                    for lane_id in route:
                        lane = features[lane_id]
                        self.assertNotIn(lane.get("source_lane_type"), {"BUS", "BIKE"})
                        if "vehicle_access" in lane:
                            self.assertIn("vehicle", lane["vehicle_access"])
                    for source, target in zip(route, route[1:]):
                        self.assertIn(target, features[source]["exit_lanes"])
                        self.assertLessEqual(math.dist(features[source]["polyline"][-1][:2],
                                                       features[target]["polyline"][0][:2]), 5.)
                if mission["patrol_loop"]:
                    outward, homeward = mission["patrol_lane_routes"]
                    self.assertEqual(outward[0], homeward[-1])
                    self.assertEqual(outward[-1], homeward[0])

    def test_no_future_ego_or_any_recorded_moving_traffic_selects_goals(self):
        payload = deepcopy(self.scenes[0])
        ego = payload["tracks"][payload["ego_track_id"]]
        payload["tracks"] = {payload["ego_track_id"]: {
            "position": ego["position"][:1], "heading": ego["heading"][:1], "valid": ego["valid"][:1]}}
        self.assertEqual(choose_interactive_missions(payload), self.missions[0])

    def test_source_maps_unchanged_and_ego_has_no_assigned_planner(self):
        scene = self.scenes[1]
        before = feature_sha256(scene["maps"])
        profile = interactive_profile(scene)
        self.assertEqual(feature_sha256(scene["maps"]), before)
        self.assertEqual(profile["ego_control"], "external_waiting")
        self.assertFalse(profile["recorded_moving_traffic"])
        self.assertFalse(profile["source_geometry_modified"])
        self.assertTrue(all(not m["uses_recorded_trajectory"] for m in profile["missions"]))

    def test_moving_ego_onto_p1_rejects_unsafe_source_spawn(self):
        scene = deepcopy(self.scenes[0])
        scene["tracks"][scene["ego_track_id"]]["position"][0] = self.missions[0][0]["position_scenario_m"]
        with self.assertRaisesRegex(ValueError, "P1 spawn overlaps: ego"):
            choose_interactive_missions(scene)

    def test_new_static_obstacle_at_p1_rejects_unsafe_source_spawn(self):
        scene = deepcopy(self.scenes[0])
        mission = self.missions[0][0]
        scene["static_overview_actors"].append({"id": "test-physical-barrier",
            "position": mission["position_scenario_m"], "heading": mission["heading_rad"],
            "length": 3., "width": 2.})
        with self.assertRaisesRegex(ValueError, "P1 spawn overlaps: test-physical-barrier"):
            choose_interactive_missions(scene)

    def test_missing_source_connection_is_not_invented_for_preset(self):
        scene = deepcopy(self.scenes[0])
        scene["maps"]["updated"][self.missions[0][0]["start_lane_id"]]["exit_lanes"] = []
        with self.assertRaisesRegex(ValueError, "declared directed source route: P1"):
            choose_interactive_missions(scene)

    def test_inaccessible_source_lane_is_rejected(self):
        scene = deepcopy(self.scenes[1])
        scene["maps"]["updated"][self.missions[1][0]["start_lane_id"]]["source_lane_type"] = "BIKE"
        with self.assertRaisesRegex(ValueError, "missing or inaccessible"):
            choose_interactive_missions(scene)

    def test_unsupported_or_controlled_demo_scene_fails_clearly(self):
        for key in ("unknown", "nuscenes-combined-demo"):
            with self.assertRaisesRegex(ValueError, "No verified five-car interactive missions"):
                choose_interactive_missions({"key": key})


if __name__ == "__main__":
    unittest.main()
