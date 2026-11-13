"""Validation helper tests; native physics evidence comes from the CLI run."""
from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from scripts.validate_interactive_simulation import (
    BRAKE, IDS, brake_controls, collision_rows, compact_vehicles, headway_test_controller, patrol_transitions,
    validation_passed, native_map_hash,
)


def observation(gap=22., *, heading=0., speed=0.):
    return {"vehicles": {
        "ego": {"position_city_m": [100., 200.], "heading_rad": heading, "speed_m_s": speed},
        "P1": {"position_city_m": [100.+gap*math.cos(heading), 200.+gap*math.sin(heading)]},
    }}


class InteractiveValidationTests(unittest.TestCase):
    def test_an_interrupted_run_cannot_pass_with_only_early_successful_checks(self):
        self.assertFalse(validation_passed([{"passed": True}], completed=False))
        self.assertFalse(validation_passed([], completed=True))
        self.assertFalse(validation_passed([{"passed": False}], completed=True))
        self.assertTrue(validation_passed([{"passed": True}], completed=True))

    def test_map_verification_detects_actual_mutation_even_when_status_hash_is_stale(self):
        features = {"lane": {"type": "LANE_SURFACE_STREET", "polyline": [[0., 0.], [10., 0.]], "exit_lanes": []}}
        session = SimpleNamespace(env=SimpleNamespace(engine=SimpleNamespace(
            data_manager=SimpleNamespace(current_scenario={"map_features": features}))))
        original = native_map_hash(session)
        session.map_hash = original
        features["lane"]["polyline"][1][0] = 12.
        self.assertNotEqual(native_map_hash(session), session.map_hash)

    def test_controller_changes_action_when_current_leader_position_changes(self):
        state = observation()
        action, rationale = headway_test_controller(state)
        self.assertGreater(action[1], 0)
        self.assertEqual(rationale["reason"], "test_follow_current_p1")
        state["vehicles"]["P1"]["position_city_m"] = [108., 200.]
        action, rationale = headway_test_controller(state)
        self.assertEqual(action, BRAKE)
        self.assertEqual(rationale["observed_gap_m"], 8.)

    def test_controller_uses_source_heading_and_current_speed(self):
        for heading in (0., math.pi/2, -2.):
            action, rationale = headway_test_controller(observation(9., heading=heading, speed=2.))
            self.assertEqual(action, BRAKE)
            self.assertAlmostEqual(rationale["observed_gap_m"], 9.)
        action, _ = headway_test_controller(observation(22., speed=4.))
        self.assertLess(action[1], 0.)

    def test_test_controller_has_no_side_effect_on_the_native_observation(self):
        state = observation()
        before = deepcopy(state)
        headway_test_controller(state)
        self.assertEqual(state, before)

    def test_brake_batch_addresses_all_six_cars_with_independent_actions(self):
        batch = brake_controls()
        self.assertEqual(set(batch), set(IDS))
        self.assertTrue(all(row == {"mode": "external", "action": BRAKE} for row in batch.values()))
        batch["P1"]["action"][1] = .5
        self.assertEqual(batch["P2"]["action"], BRAKE)
        self.assertEqual(brake_controls()["P1"]["action"], BRAKE)

    def test_collision_evidence_keeps_historical_contacts_when_current_flags_clear(self):
        status = {"step": 30, "interactive": {"vehicles": {
            "P1": {"collision": {"vehicle": False}, "collision_contact_steps": [12]},
            "P2": {"collision": {"vehicle": True}, "collision_contact_steps": [30]},
            "ego": {"collision": {"vehicle": False}, "collision_contact_steps": []},
        }}}
        rows = collision_rows(status, "ai")
        self.assertEqual([row["vehicle_id"] for row in rows], ["P1", "P2"])
        self.assertEqual(rows[0]["recorded_contact_steps"], [12])
        self.assertEqual(rows[1]["contacts"], ["vehicle"])

    def test_compact_evidence_copies_positions_and_actions(self):
        status = {"interactive": {"vehicles": {"P1": {
            "position_city_m": [1., 2.], "action": [0., .5], "collision": {"vehicle": False},
        }}}}
        saved = compact_vehicles(status)
        status["interactive"]["vehicles"]["P1"]["position_city_m"][0] = 9.
        status["interactive"]["vehicles"]["P1"]["action"][1] = -1.
        self.assertEqual(saved["P1"]["position_city_m"], [1., 2.])
        self.assertEqual(saved["P1"]["action"], [0., .5])

    def test_target_advancement_is_not_mislabeled_as_a_completed_cycle(self):
        def state(index):
            return {"step": 30, "interactive": {"vehicles": {
                key: {"patrol_goal_index": index if key == "P1" else 0} for key in IDS}}}
        missions = {key: {"patrol_loop": True, "patrol_goals_scenario_m": [[0, 0], [10, 0]]} for key in IDS[1:]}
        outward = patrol_transitions(state(0), state(1), missions)
        self.assertEqual(len(outward), 1)
        self.assertFalse(outward[0]["completed_source_cycle"])
        returned = patrol_transitions(state(1), state(0), missions)
        self.assertTrue(returned[0]["completed_source_cycle"])
        missions["P1"]["patrol_loop"] = False
        self.assertFalse(patrol_transitions(state(1), state(0), missions)[0]["completed_source_cycle"])


if __name__ == "__main__":
    unittest.main()
