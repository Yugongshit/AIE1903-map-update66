"""Concurrency, authority, stale actions and current-state delivery for rooms."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest

from map_update.simulation_room import InteractiveRoom, RoomError, VEHICLE_IDS


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class InteractiveRoomTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.room = InteractiveRoom(clock=self.clock, wall_clock=self.clock)
        self.created = self.room.create("scene", "updated", [])
        self.identity = {key: self.created[key] for key in ("session_id", "epoch")}
        self.host = {**self.identity, "host_token": self.created["host_token"]}

    def claim(self, vehicles="P1", kind="manual"):
        return self.room.claim(vehicles, kind=kind, client_label="test", **self.identity,
                               join_token=self.created["join_token"])["lease_token"]

    def send(self, token, actions, seq=0, **extra):
        return self.room.input(token, seq=seq, actions=actions, **self.identity, **extra)

    def test_defaults_and_public_snapshots_never_expose_secrets(self):
        token = self.claim()
        snapshot = self.room.snapshot()
        encoded = json.dumps(snapshot)
        for secret in (token, self.created["join_token"], self.created["host_token"]):
            self.assertNotIn(secret, encoded)
        self.assertEqual(snapshot["vehicles"]["ego"]["mode"], "external")
        self.assertEqual(snapshot["vehicles"]["ego"]["effective_action"], [0, -1])
        self.assertEqual(snapshot["vehicles"]["P2"]["mode"], "ai")

    def test_compact_room_retains_authority_but_skips_duplicate_physical_state(self):
        token = self.claim()
        full = self.room.snapshot()
        compact = self.room.snapshot(include_objects=False, include_vehicle_state=False)
        self.assertNotIn('objects', compact)
        self.assertNotIn('state', compact['vehicles']['P1'])
        expected = {key: value for key, value in full['vehicles']['P1'].items() if key != 'state'}
        self.assertEqual(compact['vehicles']['P1'], expected)
        self.assertTrue(compact['vehicles']['P1']['claimed'])
        self.clock.advance(4)
        self.assertFalse(self.room.snapshot(include_objects=False)['vehicles']['P1']['claimed'])
        self.assertNotIn(token, json.dumps(compact))

    def test_claim_needs_invitation_but_players_do_not_need_host_ticket(self):
        with self.assertRaises(RoomError) as failure:
            self.room.claim("P1", **self.identity)
        self.assertEqual(failure.exception.status, 403)
        token = self.claim()
        self.assertTrue(token)
        with self.assertRaises(RoomError):
            self.room.set_mode("P1", "ai", **self.identity, host_token=self.created["join_token"])
        with self.assertRaises(RoomError) as failure:
            self.room.claim("P2", **self.identity, join_token="非ASCII令牌")
        self.assertEqual(failure.exception.status, 403)

    def test_simultaneous_players_cannot_claim_the_same_car(self):
        gate = threading.Barrier(2)
        def compete(index):
            gate.wait()
            try:
                result = self.room.claim("P1", **self.identity, join_token=self.created["join_token"],
                                         client_label=str(index))
                return result["lease_token"]
            except RoomError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, [1, 2]))
        self.assertEqual(results.count("vehicle_claimed"), 1)
        self.assertEqual(sum(value != "vehicle_claimed" for value in results), 1)

    def test_external_batch_claim_is_all_or_nothing(self):
        self.claim("P3")
        with self.assertRaises(RoomError):
            self.claim(["P1", "P2", "P3", "P4", "P5"], "external")
        snapshot = self.room.snapshot()
        self.assertFalse(snapshot["vehicles"]["P1"]["claimed"])
        self.assertFalse(snapshot["vehicles"]["P5"]["claimed"])
        self.assertTrue(snapshot["vehicles"]["P3"]["claimed"])

    def test_external_client_controls_five_cars_with_one_atomic_message(self):
        keys = list(VEHICLE_IDS[1:])
        token = self.claim(keys, "external")
        actions = {key: [0.1, 0.3] for key in keys}
        self.send(token, actions, observation_step=0)
        controls = self.room.controls_for_tick(1)
        self.assertTrue(all(controls["vehicles"][key]["action"] == [0.1, 0.3] for key in keys))

    def test_reordered_duplicate_and_unowned_inputs_are_rejected(self):
        token = self.claim()
        self.send(token, {"P1": [0, 0.5]}, seq=3)
        for seq in (3, 2):
            with self.assertRaises(RoomError) as error:
                self.send(token, {"P1": [0, -1]}, seq=seq)
            self.assertEqual(error.exception.code, "stale_action")
        with self.assertRaises(RoomError):
            self.send(token, {"P1": [0, -1], "P2": [0, 1]}, seq=4)
        self.assertEqual(self.room.controls_for_tick(0)["vehicles"]["P1"]["action"], [0, 0.5])
        self.send(token, {"P1": [0, 0.2]}, seq=4)

    def test_invalid_batch_does_not_consume_sequence_or_partially_apply(self):
        token = self.claim(["P1", "P2"], "external")
        for bad in ([float("nan"), 0], [False, 0], [0, 2], [0], "stop"):
            with self.assertRaises(RoomError):
                self.send(token, {"P1": [0, 1], "P2": bad}, observation_step=0)
        controls = self.room.controls_for_tick(0)["vehicles"]
        self.assertEqual(controls["P1"]["action"], [0, -1])
        self.send(token, {"P1": [0, 0.2], "P2": [0, 0.3]}, observation_step=0)

    def test_manual_deadman_brakes_then_releases_even_if_heartbeat_continues(self):
        token = self.claim()
        self.send(token, {"P1": [0, 0.5]})
        self.clock.advance(0.41)
        self.room.heartbeat(token, **self.identity)
        controls = self.room.controls_for_tick(1)["vehicles"]["P1"]
        self.assertEqual(controls["action"], [0, -1])
        self.assertEqual(controls["reason"], "action_deadman_brake")
        self.clock.advance(2.5)
        self.room.heartbeat(token, **self.identity)
        self.clock.advance(0.1)
        controls = self.room.controls_for_tick(2)["vehicles"]["P1"]
        self.assertEqual(controls["mode"], "ai")
        self.assertEqual(controls["reason"], "action_stream_expired")

    def test_native_command_has_receipt_based_deadline_but_public_snapshot_does_not(self):
        token = self.claim()
        self.send(token, {"P1": [0.1, 0.5]})
        self.clock.advance(0.2)
        control = self.room.controls_for_tick(1)["vehicles"]["P1"]
        self.assertAlmostEqual(control["expires_at_monotonic"], 10.4)
        self.assertNotIn("expires_at_monotonic", json.dumps(self.room.snapshot()))
        self.clock.advance(0.21)
        control = self.room.controls_for_tick(2)["vehicles"]["P1"]
        self.assertEqual(control["action"], [0, -1])
        self.assertNotIn("expires_at_monotonic", control)
        self.assertNotIn("expires_at_monotonic", self.room.controls_for_tick(2)["vehicles"]["P2"])

    def test_external_lost_ego_stays_stopped_and_p_cars_return_to_ai(self):
        token = self.claim(["ego", "P1"], "external")
        self.send(token, {"ego": [0, 0.5], "P1": [0, 0.5]}, observation_step=0)
        self.clock.advance(3.01)
        control = self.room.controls_for_tick(1)["vehicles"]
        self.assertEqual(control["ego"]["mode"], "external")
        self.assertEqual(control["ego"]["action"], [0, -1])
        self.assertEqual(control["P1"]["mode"], "ai")
        with self.assertRaises(RoomError):
            self.room.set_mode("ego", "ai", **self.host)

    def test_external_observation_must_be_current_enough_and_cannot_be_future(self):
        token = self.claim("ego", "external")
        self.room.controls_for_tick(10)
        for observed in (None, 11, 4):
            with self.assertRaises(RoomError):
                self.send(token, {"ego": [0, 0.4]}, observation_step=observed)
        self.send(token, {"ego": [0, 0.4]}, observation_step=5)
        self.assertEqual(self.room.controls_for_tick(10)["vehicles"]["ego"]["action"], [0, 0.4])
        control = self.room.controls_for_tick(11)["vehicles"]["ego"]
        self.assertEqual(control["action"], [0, -1])
        self.assertEqual(control["reason"], "observation_deadman_brake")

    def test_release_and_reclaim_invalidates_old_control_token(self):
        old = self.claim()
        self.room.release(old, **self.identity)
        new = self.claim()
        with self.assertRaises(RoomError):
            self.send(old, {"P1": [0, 1]})
        self.send(new, {"P1": [0, 0.2]})

    def test_new_session_rejects_old_actions_and_old_native_completions(self):
        token = self.claim()
        new = self.room.create("another-scene", "original", [])
        self.assertGreater(new["epoch"], self.created["epoch"])
        with self.assertRaises(RoomError) as error:
            self.send(token, {"P1": [0, 1]})
        self.assertEqual(error.exception.code, "stale_session")
        with self.assertRaises(RoomError):
            self.room.publish({"step": 0, "vehicles": {}}, "old-frame", **self.identity)
        self.assertIsNone(self.room.snapshot()["frame_id"])

    def test_spectator_polling_does_not_advance_simulation_clock(self):
        for _ in range(10):
            self.clock.advance(0.1)
            self.assertEqual(self.room.snapshot()["step"], 0)
        self.room.controls_for_tick(4)
        self.assertEqual(self.room.snapshot()["step"], 4)
        with self.assertRaises(RoomError):
            self.room.controls_for_tick(3)

    def test_current_state_whitelist_excludes_future_trajectories_and_is_copied(self):
        native = {"step": 7, "tracks": ["future"], "interactive": {"vehicles": {
            "ego": {"vehicle_id": "ego", "position_scenario_m": [1, 2], "speed_m_s": 3,
                    "future_positions": [[1, 2], [3, 4]], "recorded_route": ["future"]}},
            "objects": [{"id": "person", "position": [4, 5], "heading_rad": 0}],
            "state_overview": ["future"]}}
        for identifier in VEHICLE_IDS[1:]:
            native["interactive"]["vehicles"][identifier] = {"vehicle_id": identifier, "position_city_m": [0, 0]}
        snapshot = self.room.publish(native, "frame-7", **self.identity)
        self.assertEqual(snapshot["observation_step"], 7)
        self.assertEqual(snapshot["frame_id"], "frame-7")
        self.assertNotIn('"future"', json.dumps(snapshot))
        self.assertNotIn("future_positions", snapshot["vehicles"]["ego"]["state"])
        snapshot["vehicles"]["ego"]["state"]["position_scenario_m"][0] = 999
        native["interactive"]["objects"][0]["position"][0] = 999
        self.assertEqual(self.room.snapshot()["vehicles"]["ego"]["state"]["position_scenario_m"], [1, 2])
        self.assertEqual(self.room.snapshot()["objects"][0]["position"], [4, 5])

    def test_publish_rejects_trajectory_disguised_as_current_pose(self):
        vehicles = {key: {"position": [0, 0]} for key in VEHICLE_IDS}
        vehicles["ego"]["position"] = [[0, 0], [1, 1]]
        with self.assertRaises(RoomError):
            self.room.publish({"step": 0, "vehicles": vehicles}, **self.identity)
        vehicles["ego"] = {"speed_m_s": float("inf")}
        with self.assertRaises(RoomError):
            self.room.publish({"step": 0, "vehicles": vehicles}, **self.identity)

    def test_partial_native_frame_cannot_mix_vehicle_states_from_different_steps(self):
        with self.assertRaises(RoomError):
            self.room.publish({"step": 1, "vehicles": {"ego": {"position": [0, 0]}}}, **self.identity)
        self.assertIsNone(self.room.snapshot()["observation_step"])

    def test_native_static_actor_alias_and_current_collision_are_preserved(self):
        vehicles = {key: {"vehicle_id": key, "position_city_m": [0, 0],
                          "collision": {"vehicle": False, "object": True},
                          "stopped_reason": "collision", "action": [0, -1]} for key in VEHICLE_IDS}
        result = {"step": 0, "interactive": {"vehicles": vehicles,
                  "static_actors": [{"id": "barrier", "position_city_m": [10, 0]}]}}
        snapshot = self.room.publish(result, "f0", **self.identity)
        self.assertEqual(snapshot["objects"][0]["id"], "barrier")
        self.assertTrue(snapshot["vehicles"]["ego"]["state"]["collision"]["object"])

    def test_host_mode_change_revokes_only_selected_vehicle_of_a_batch(self):
        token = self.claim(["P1", "P2"], "external")
        self.room.set_mode("P1", "ai", **self.host)
        self.send(token, {"P2": [0, 0.2]}, observation_step=0)
        with self.assertRaises(RoomError):
            self.send(token, {"P1": [0, 0.2]}, seq=1, observation_step=0)

    def test_close_requires_host_and_rejects_further_controls(self):
        with self.assertRaises(RoomError):
            self.room.close(**self.identity, host_token=self.created["join_token"])
        self.room.close(**self.host)
        self.assertFalse(self.room.snapshot()["active"])
        with self.assertRaises(RoomError):
            self.room.controls_for_tick(0)


if __name__ == "__main__":
    unittest.main()
