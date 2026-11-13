"""Source-data checks for independently planned recipient-car experiments."""
from copy import deepcopy
import math
from pathlib import Path
import unittest

from shapely.geometry import LineString, shape
from shapely.ops import unary_union

from map_update.simulation_fleet_scenarios import choose_fleet_missions, fleet_profile
from map_update.simulation_platform_data import load_scene


ROOT = Path(__file__).resolve().parents[1]


class FleetScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nuscenes = load_scene(ROOT, "nuscenes-scene0980")
        cls.argoverse = load_scene(ROOT, "argoverse-winter2021")

    def test_source_directed_connections_are_used_in_both_versions(self):
        for payload in (self.nuscenes, self.argoverse):
            missions = choose_fleet_missions(payload)
            self.assertEqual([item["vehicle_id"] for item in missions], ["B", "C"])
            self.assertNotEqual(missions[0]["goal_scenario_m"], missions[1]["goal_scenario_m"])
            for mission in missions:
                self.assertTrue(mission["initial_clearance_checked"])
                self.assertGreater(mission["source_route_length_m"], 25)
                self.assertFalse(mission["uses_recorded_trajectory"])
                for version, field in (("original", "source_lane_ids"), ("updated", "updated_lane_ids")):
                    features = payload["maps"][version]
                    for source, target in zip(mission[field], mission[field][1:]):
                        self.assertIn(target, features[source]["exit_lanes"])

    def test_nuscenes_b_reaches_true_state_footprint_while_c_is_clear(self):
        payload = self.nuscenes
        missions = choose_fleet_missions(payload)
        footprint = unary_union([shape(feature["geometry"])
                                 for feature in payload["state_overview"]["layer"]["features"]])
        intersections = []
        for mission in missions:
            corridors = [LineString([point[:2] for point in payload["maps"]["original"][key]["polyline"]]).buffer(1.0)
                         for key in mission["source_lane_ids"]]
            intersections.append(any(footprint.intersects(corridor) for corridor in corridors))
            self.assertEqual(mission["source_lane_ids"], mission["updated_lane_ids"])
        self.assertEqual(intersections, [True, False])
        self.assertEqual(fleet_profile(payload)["unchanged_comparison"], "old_equals_structure")

    def test_argo_b_uses_the_delivered_new_turn_connections_c_is_control(self):
        profile = fleet_profile(self.argoverse)
        b, c = profile["missions"]
        self.assertIn("ls-168084879", b["source_lane_ids"])
        self.assertNotIn("ls-168084879", b["updated_lane_ids"])
        self.assertIn("ls-168084910", b["updated_lane_ids"])
        self.assertNotIn("ls-168084910", self.argoverse["maps"]["original"])
        self.assertIn("ls-168084910", self.argoverse["maps"]["updated"])
        self.assertEqual(c["source_lane_ids"], c["updated_lane_ids"])
        self.assertEqual(profile["unchanged_comparison"], "structure_equals_full")
        self.assertTrue(profile["synthetic_recipient_vehicles"])
        self.assertFalse(profile["synthetic_map_changes"])

    def test_recipient_goals_do_not_read_future_ego_or_traffic_positions(self):
        payload = deepcopy(self.nuscenes)
        expected = choose_fleet_missions(payload)
        for track in payload["tracks"].values():
            for field in ("position", "heading", "length", "width", "valid"):
                track[field] = track[field][:1]
        self.assertEqual(choose_fleet_missions(payload), expected)

    def test_nuscenes_stopped_b_starts_behind_initial_approaching_replay_cars(self):
        b = choose_fleet_missions(self.nuscenes)[0]
        forward = (math.cos(b["heading_rad"]), math.sin(b["heading_rad"]))
        # In a full-prior run B may stop immediately. Source A and the initial
        # following traffic car must already be ahead, so neither is seeded to
        # rear-end B while following its unmodified recorded trajectory.
        for identifier in ("ego", "0ec7a218142c41f085e47440c0a60245"):
            source_position = self.nuscenes["tracks"][identifier]["position"][0]
            ahead = sum((source_position[i] - b["position_scenario_m"][i]) * forward[i] for i in (0, 1))
            self.assertGreater(ahead, 10)

    def test_changed_actor_occupancy_rejects_an_unsafe_preset_spawn(self):
        payload = deepcopy(self.nuscenes)
        mission = choose_fleet_missions(payload)[0]
        payload["static_overview_actors"].append({
            "id": "test-spawn-blocker", "position": mission["position_scenario_m"],
            "heading": mission["heading_rad"], "length": 4.0, "width": 2.0,
        })
        with self.assertRaisesRegex(ValueError, "spawn overlaps.*test-spawn-blocker"):
            choose_fleet_missions(payload)

    def test_missing_connection_cannot_be_filled_with_an_invented_path(self):
        payload = deepcopy(self.nuscenes)
        payload["maps"]["original"]["71213690-7222-4882-a4cb-94610eda0ff3"]["exit_lanes"] = []
        with self.assertRaisesRegex(ValueError, "directed source route"):
            choose_fleet_missions(payload)

    def test_unknown_scene_cannot_silently_reuse_another_city_mission(self):
        with self.assertRaisesRegex(ValueError, "No verified recipient-car missions"):
            choose_fleet_missions({"key": "another-city"})


if __name__ == "__main__":
    unittest.main()
