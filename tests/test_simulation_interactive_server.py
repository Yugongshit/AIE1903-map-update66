"""Real HTTP boundaries and the server ticker, with no native engine process."""
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from map_update.simulation_platform import BusyError, PlatformServer
from map_update.simulation_room import VEHICLE_IDS


class FakeBroker:
    """Serialized, identifiable native completions; no physics or subprocess."""
    def __init__(self):
        self.lock = threading.RLock()
        self.commands = []
        self.delay = 0.0
        self.pending = None
        self.counter = 0
        self.step = 0
        self.camera = {"target": "ego", "yaw": 0., "pitch": 30., "distance": 24., "pan": [0., 0.]}
        self.fail_next_tick = False
        self.malformed_next_tick = False
        self.state = {"busy": False, "ready": False, "job_id": None, "last_result": None,
                      "error": None, "scene_id": None, "version": None}

    def submit(self, command, *, scene_id=None, version=None, expected_scene_id=None, expected_version=None):
        with self.lock:
            self._complete_if_ready()
            if self.state["busy"]:
                raise BusyError("fake native command already in flight")
            if expected_scene_id is not None and (expected_scene_id != self.state["scene_id"] or expected_version != self.state["version"]):
                raise BusyError("fake scene ownership mismatch")
            self.counter += 1
            job_id = "fake-job-" + str(self.counter)
            if scene_id is not None:
                self.state.update(scene_id=scene_id, version=version)
            self.commands.append({"command": deepcopy(command), "thread": threading.current_thread().name,
                                  "job_id": job_id})
            self.state.update(busy=True, job_id=job_id, error=None)
            self.pending = (time.monotonic()+self.delay, deepcopy(command))
            return {"job_id": job_id}

    def _complete_if_ready(self):
        if self.pending is None or time.monotonic() < self.pending[0]:
            return
        _, command = self.pending
        self.pending = None
        kind = command["command"]
        self.state["busy"] = False
        if kind == "interactive_tick" and self.fail_next_tick:
            self.fail_next_tick = False
            self.state.update(error="injected tick failure", ready=False)
            return
        if kind == "close":
            self.state.update(last_result={"closed": True}, ready=False)
            return
        if kind == "open":
            self.step = 0
        elif kind == "interactive_tick":
            self.step += command["steps"]
        elif kind == "camera":
            self.camera.update({key: value for key, value in command.items() if key != "command"})
        vehicles = {key: {"vehicle_id": key, "position_city_m": [float(index), float(self.step)],
                          "position_scenario_m": [float(index), float(self.step)], "heading_rad": 0.,
                          "speed_m_s": 0., "length_m": 4.5, "width_m": 1.8,
                          "collision": {"vehicle": False, "object": False, "human": False, "building": False},
                          "mode": "external" if key == "ego" else "ai", "action": [0., -1.]}
                    for index, key in enumerate(VEHICLE_IDS)}
        if kind == "interactive_tick" and self.malformed_next_tick:
            self.malformed_next_tick = False
            del vehicles["P5"]
        capture = kind != "interactive_tick" or command.get("capture", True)
        result = {"step": self.step, "sequence": self.counter, "state_id": "fake-state-"+str(self.counter),
                  "image_available": capture,
                  "map_features_sha256": "source-map", "camera": deepcopy(self.camera),
                  "interactive": {"vehicles": vehicles, "sim_time_s": self.step*.1,
                                  "static_actors": [{"vehicle_id": "barrier", "position_city_m": [20., 10.]}]}}
        result['snapshot'] = {'step': self.step, 'map_sha256': 'source-map',
                              'actors': [*deepcopy(list(vehicles.values())), {'id': 'barrier', 'position': [20., 10.]}]}
        if capture:
            result["frame_id"] = "fake-frame-"+str(self.counter)
        self.state.update(last_result=result, ready=True)

    def status(self, **kwargs):
        with self.lock:
            self._complete_if_ready()
            return deepcopy(self.state if kwargs.get('include_result', True) else {
                key: value for key, value in self.state.items() if key != 'last_result'})

    def close(self):
        pass

    def frame(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n"  # Opaque fake bytes: read endpoint must never tick.


class InteractiveHttpTests(unittest.TestCase):
    KEY = "test-admin-control-key-123456"
    ORIGIN = "https://viewer.example"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = PlatformServer(("127.0.0.1", 0), self.root, control_key=self.KEY,
                                     allowed_origins=[self.ORIGIN])
        previous = self.server.engine
        self.broker = self.server.engine = FakeBroker()
        previous.close()
        self.server.scene = lambda key: {"key": key, "maps": {"updated": {}}, "coordinate": "source_city_xy",
                                         "map": "source-map", "source": {"kind": "test"}}
        missions = [{"vehicle_id": key, "position_scenario_m": [float(index), 0.], "heading_rad": 0.}
                    for index, key in enumerate(VEHICLE_IDS[1:])]
        self.missions_patch = patch("map_update.simulation_interactive_scenarios.choose_interactive_missions", return_value=missions)
        self.database_patch = patch("map_update.simulation_platform_data.scene_database", return_value=self.root/"allowed")
        self.missions_patch.start()
        self.database_patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.database_patch.stop()
        self.missions_patch.stop()
        self.tmp.cleanup()

    def request(self, path, *, method="GET", body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        status, response_headers, content = response.status, dict(response.getheaders()), response.read()
        connection.close()
        payload = (json.loads(content) if content and "application/json" in response_headers.get("Content-Type", "")
                   else content if content else None)
        return status, response_headers, payload

    def post(self, endpoint, body, *, admin=False, headers=None):
        auth = {"Authorization": "Bearer " + self.KEY} if admin else {}
        return self.request("/api/interactive/"+endpoint, method="POST", body=body,
                            headers={**auth, **(headers or {})})

    def state(self):
        status, _, value = self.request("/api/interactive/state")
        self.assertEqual(status, 200)
        return value

    def until(self, predicate, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            value = self.state()
            if predicate(value):
                return value
            time.sleep(.005)
        self.fail("Timed out waiting for coordinator state: " + json.dumps(self.state()))

    def open_room(self):
        status, _, value = self.post("open", {"scene_id": "test-scene", "version": "updated"}, admin=True)
        self.assertEqual(status, 202, value)
        self.identity = value["room"]
        self.until(lambda state: state["phase"] == "paused")
        return self.identity

    def public_identity(self):
        return {key: self.identity[key] for key in ("session_id", "epoch")}

    def host(self, action, **extra):
        return self.post("control", {**self.public_identity(), "host_token": self.identity["host_token"],
                                     "action": action, **extra})

    def test_public_binding_requires_a_strong_control_key_before_opening_socket(self):
        for key in (None, "short"):
            with self.assertRaisesRegex(ValueError, "control key"):
                PlatformServer(("0.0.0.0", 0), self.root, control_key=key)

    def test_admin_bearer_is_required_for_room_creation_but_not_public_read(self):
        body = {"scene_id": "test-scene", "version": "updated"}
        self.assertEqual(self.post("open", body)[0], 403)
        self.assertEqual(self.post("open", body, headers={"Authorization": "Bearer incorrect"})[0], 403)
        self.assertEqual(self.broker.commands, [])
        self.open_room()
        public = self.state()
        serialized = json.dumps(public)
        self.assertNotIn(self.identity["host_token"], serialized)
        self.assertNotIn(self.identity["join_token"], serialized)
        self.assertNotIn(self.KEY, serialized)

    def test_exact_allowed_origin_preflight_and_untrusted_cross_origin_rejection(self):
        status, headers, _ = self.request("/api/interactive/claim", method="OPTIONS",
            headers={"Origin": self.ORIGIN, "Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], self.ORIGIN)
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])
        self.assertEqual(self.request("/api/interactive/claim", method="OPTIONS",
            headers={"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(self.post("open", {"scene_id": "test-scene"}, admin=True,
            headers={"Origin": "https://untrusted.example"})[0], 400)
        status, headers, _ = self.post("open", {"scene_id": "test-scene"}, admin=True,
            headers={"Origin": self.ORIGIN})
        self.assertEqual(status, 202)
        self.assertEqual(headers["Access-Control-Allow-Origin"], self.ORIGIN)

    def test_external_five_car_claim_input_release_and_foreign_input_boundaries(self):
        self.open_room()
        identity = self.public_identity()
        claim = {**identity, "vehicle_ids": list(VEHICLE_IDS[1:]), "kind": "external"}
        self.assertEqual(self.post("claim", claim)[0], 403)
        status, _, value = self.post("claim", {**claim, "join_token": self.identity["join_token"]})
        self.assertEqual(status, 200, value)
        token = value["lease_token"]
        self.assertEqual(self.post("claim", {**identity, "vehicle_id": "P1", "join_token": self.identity["join_token"]})[0], 409)
        actions = {key: [0.1, .2] for key in VEHICLE_IDS[1:]}
        packet = {**identity, "lease_token": token, "seq": 1, "observation_step": 0, "actions": actions}
        self.assertEqual(self.post("input", packet)[0], 200)
        self.assertEqual(self.post("input", packet)[0], 409)
        self.assertEqual(self.post("input", {**packet, "seq": 2, "actions": {"ego": [0, 1]}})[0], 403)
        self.assertEqual(self.post("input", {**packet, "seq": 2, "observation_step": 99})[0], 409)
        self.assertEqual(self.post("release", {**identity, "lease_token": "foreign"})[0], 403)
        self.assertEqual(self.post("release", {**identity, "lease_token": token})[0], 200)
        self.assertEqual(self.post("input", {**packet, "seq": 2})[0], 403)
        self.assertTrue(all(not row["claimed"] for row in self.state()["room"]["vehicles"].values()))

    def test_spectators_never_tick_and_only_server_thread_advances_running_room(self):
        self.open_room()
        for _ in range(15):
            state = self.state()
            self.assertEqual(state["room"]["step"], 0)
            self.request("/api/interactive/observation?session_id="+self.identity["session_id"]+"&vehicle_id=ego")
            self.assertEqual(self.request("/api/engine/frame.png?frame_id=fake-frame-1")[0], 200)
        self.assertEqual([row["command"]["command"] for row in self.broker.commands], ["open"])
        self.assertEqual(self.host("play")[0], 200)
        self.until(lambda value: value["room"]["step"] >= 2)
        self.assertEqual(self.host("pause")[0], 200)
        self.until(lambda value: value["phase"] == "paused" and not self.broker.status()["busy"])
        ticks = [row for row in self.broker.commands if row["command"]["command"] == "interactive_tick"]
        self.assertTrue(ticks)
        self.assertTrue(all(row["thread"] == "interactive-physics-clock" for row in ticks))
        step = self.state()["room"]["step"]
        self.assertEqual(step, len(ticks))
        for _ in range(10):
            self.assertEqual(self.state()["room"]["step"], step)

    def test_active_room_excludes_legacy_engine_mutations(self):
        self.open_room()
        status, _, _ = self.request("/api/engine/command", method="POST", body={"command": "advance", "steps": 1},
                                   headers={"Authorization": "Bearer "+self.KEY})
        self.assertEqual(status, 409)
        self.assertEqual(len(self.broker.commands), 1)

    def test_only_host_pauses_closes_and_rebuilt_room_rejects_old_tokens(self):
        self.open_room()
        old = dict(self.identity)
        _, _, claim = self.post("claim", {**self.public_identity(), "vehicle_id": "P1", "join_token": old["join_token"]})
        self.assertEqual(self.post("control", {**self.public_identity(), "host_token": old["join_token"], "action": "play"})[0], 403)
        self.assertEqual(self.host("close")[0], 200)
        closed = self.until(lambda value: not value["active"])
        self.assertFalse(closed["room"]["active"])
        self.open_room()
        self.assertNotEqual(old["session_id"], self.identity["session_id"])
        self.assertGreater(self.identity["epoch"], old["epoch"])
        status, _, _ = self.post("input", {"session_id": old["session_id"], "epoch": old["epoch"],
            "lease_token": claim["lease_token"], "seq": 1, "actions": {"P1": [0, 1]}})
        self.assertNotEqual(status, 200)
        self.assertEqual(self.post("claim", {**self.public_identity(), "vehicle_id": "P1", "join_token": old["join_token"]})[0], 403)

    def test_camera_request_is_not_starved_when_every_physics_frame_is_slow(self):
        self.open_room()
        self.broker.delay = .15
        self.assertEqual(self.host("play")[0], 200)
        self.until(lambda value: any(row["command"]["command"] == "interactive_tick" for row in self.broker.commands))
        status, _, _ = self.post("camera", {**self.public_identity(), "join_token": self.identity["join_token"],
                                           "camera": {"yaw": 120}})
        self.assertEqual(status, 200)
        self.until(lambda value: any(row["command"]["command"] == "camera" for row in self.broker.commands))
        self.until(lambda value: value["room"]["step"] >= 2)
        self.assertTrue(any(row["command"]["command"] == "camera" for row in self.broker.commands))

    def test_host_reset_changes_generation_and_native_ticks_preserve_action_deadline(self):
        self.open_room()
        old = dict(self.identity)
        _, _, claim = self.post("claim", {**self.public_identity(), "vehicle_id": "P1", "join_token": old["join_token"]})
        packet = {**self.public_identity(), "lease_token": claim["lease_token"], "seq": 1, "actions": {"P1": [0, .2]}}
        self.assertEqual(self.post("input", packet)[0], 200)
        self.host("play")
        self.until(lambda value: value["room"]["step"] >= 1)
        self.host("pause")
        self.until(lambda value: value["phase"] == "paused" and not self.broker.status()["busy"])
        tick = next(row["command"] for row in self.broker.commands if row["command"]["command"] == "interactive_tick")
        self.assertIn("expires_at_monotonic", tick["controls"]["P1"])
        status, _, value = self.host("reset")
        self.assertEqual(status, 200, value)
        self.identity = value["room"]
        self.until(lambda state: state["phase"] == "paused")
        self.assertGreater(self.identity["epoch"], old["epoch"])
        self.assertNotEqual(self.identity["session_id"], old["session_id"])
        self.assertNotEqual(self.post("input", {**packet, "seq": 2})[0], 200)

    def test_queued_close_finishes_even_if_inflight_physics_fails(self):
        self.open_room()
        self.broker.delay, self.broker.fail_next_tick = .15, True
        self.host("play")
        self.until(lambda value: self.broker.status()["busy"])
        self.assertEqual(self.host("close")[0], 200)
        self.until(lambda value: not value["active"])
        self.assertEqual(self.broker.commands[-1]["command"]["command"], "close")

    def test_failed_physics_never_serves_cached_pose_as_current_observation(self):
        self.open_room()
        query = "/api/interactive/observation?session_id="+self.identity["session_id"]+"&vehicle_id=ego"
        self.assertEqual(self.request(query)[0], 200)
        self.broker.fail_next_tick = True
        self.host("play")
        state = self.until(lambda value: value["phase"] == "error")
        self.assertIsNotNone(state["engine"]["last_result"])
        status, _, body = self.request(query)
        self.assertEqual(status, 400)
        self.assertIn("unavailable", body["error"])
        self.assertNotIn("vehicles", body)

    def test_closing_room_does_not_return_observation_from_last_successful_frame(self):
        self.open_room()
        self.broker.delay = .15
        self.host("close")
        self.until(lambda value: value["phase"] == "closing")
        query = "/api/interactive/observation?session_id="+self.identity["session_id"]+"&vehicle_id=ego"
        self.assertEqual(self.request(query)[0], 400)

    def test_default_live_updates_skip_png_but_observations_have_unique_state_identity(self):
        self.open_room()
        self.host("play")
        self.until(lambda state: state["room"]["step"] >= 2)
        self.host("pause")
        state = self.until(lambda value: value["phase"] == "paused" and not self.broker.status()["busy"])
        ticks = [row["command"] for row in self.broker.commands if row["command"]["command"] == "interactive_tick"]
        self.assertTrue(ticks)
        self.assertTrue(all(command["capture"] is False for command in ticks))
        result = state["engine"]["last_result"]
        self.assertFalse(result["image_available"])
        self.assertNotIn("frame_id", result)
        query = "/api/interactive/observation?session_id="+self.identity["session_id"]+"&vehicle_id=ego"
        status, _, observation = self.request(query)
        self.assertEqual(status, 200)
        self.assertEqual(observation["state_id"], result["state_id"])
        self.assertEqual(observation["step"], result["step"])
        self.assertIsNone(observation["frame_id"])

    def test_native_view_interest_enables_capture_and_expires_without_stopping_physics(self):
        self.open_room()
        self.assertEqual(self.request("/api/interactive/state?render=1")[0], 200)
        self.host("play")
        self.until(lambda state: state["room"]["step"] >= 1)
        captured = [row["command"] for row in self.broker.commands if row["command"]["command"] == "interactive_tick"]
        self.assertTrue(captured[-1]["capture"])
        # Advance the coordinator's rendering-interest deadline deterministically;
        # unlike public state polling this does not alter its physics step.
        with self.server.interactive.lock:
            self.server.interactive.render_until = time.monotonic()-1
        previous_step = self.state()["room"]["step"]
        self.until(lambda state: state["room"]["step"] >= previous_step+2)
        ticks = [row["command"] for row in self.broker.commands if row["command"]["command"] == "interactive_tick"]
        self.assertFalse(ticks[-1]["capture"])

    def test_paused_native_view_requests_picture_without_advancing_physics(self):
        self.open_room()
        self.host("play")
        self.until(lambda state: state["room"]["step"] >= 1)
        self.host("pause")
        paused = self.until(lambda value: value["phase"] == "paused" and not self.broker.status()["busy"])
        step, before = paused["room"]["step"], len(self.broker.commands)
        self.assertFalse(paused["engine"]["last_result"]["image_available"])
        self.assertEqual(self.request("/api/interactive/state?render=1")[0], 200)
        result = self.until(lambda value: value["engine"]["last_result"].get("image_available") is True)
        self.assertEqual(result["room"]["step"], step)
        following = [row["command"]["command"] for row in self.broker.commands[before:]]
        self.assertEqual(following, ["frame"])

    def test_malformed_native_completion_stops_clock_even_when_spectator_publishes_it(self):
        self.open_room()
        self.broker.malformed_next_tick = True
        self.host("play")
        state = self.until(lambda value: value["phase"] == "error")
        self.assertFalse(state["playing"])
        self.assertIn("all five", state["error"])
        self.assertEqual(state["room"]["step"], 0)
        self.assertEqual(self.broker.step, 1)
        query = "/api/interactive/observation?session_id="+self.identity["session_id"]+"&vehicle_id=ego"
        self.assertEqual(self.request(query)[0], 400)

    def test_compact_state_keeps_actual_actor_geometry_and_omits_only_duplicate_state(self):
        self.open_room()
        complete = self.state()
        status, _, compact = self.request('/api/interactive/state?compact=1')
        self.assertEqual(status, 200)
        full_result, result = complete['engine']['last_result'], compact['engine']['last_result']
        self.assertEqual(result['snapshot'], full_result['snapshot'])
        self.assertEqual(result['interactive']['vehicles'], full_result['interactive']['vehicles'])
        self.assertEqual(result['map_features_sha256'], full_result['map_features_sha256'])
        self.assertEqual(result['state_id'], full_result['state_id'])
        self.assertNotIn('static_actors', result['interactive'])
        self.assertNotIn('objects', compact['room'])
        self.assertNotIn('state', compact['room']['vehicles']['P1'])
        self.assertIn('objects', complete['room'])
        self.assertIn('static_actors', full_result['interactive'])
        self.assertEqual(len(result['snapshot']['actors']), 7)
        encoded = json.dumps(compact)
        self.assertNotIn(self.identity['host_token'], encoded)
        self.assertNotIn(self.identity['join_token'], encoded)

    def test_unchanged_physics_does_not_cache_room_ownership_or_control_mode(self):
        self.open_room()
        state_id = self.state()['engine']['last_result']['state_id']
        query = '/api/interactive/state?compact=1&since_state_id='+state_id
        before = self.request(query)[2]
        self.assertTrue(before['state_unchanged'])
        self.assertIsNone(before['engine']['last_result'])
        _, _, claim = self.post('claim', {**self.public_identity(), 'vehicle_id': 'P1',
                                          'join_token': self.identity['join_token']})
        claimed = self.request(query)[2]
        self.assertTrue(claimed['room']['vehicles']['P1']['claimed'])
        self.assertEqual(claimed['room']['vehicles']['P1']['mode'], 'manual')
        self.assertIsNone(claimed['engine']['last_result'])
        self.post('release', {**self.public_identity(), 'lease_token': claim['lease_token']})
        released = self.request(query)[2]
        self.assertFalse(released['room']['vehicles']['P1']['claimed'])
        self.assertEqual(released['room']['vehicles']['P1']['mode'], 'ai')
        self.assertEqual(self.host('mode', vehicle_id='P2', mode='external')[0], 200)
        self.assertEqual(self.request(query)[2]['room']['vehicles']['P2']['mode'], 'external')
        self.assertEqual(len(self.broker.commands), 1)
        self.assertIsNotNone(self.request('/api/interactive/state?compact=1&since_state_id=old')[2]['engine']['last_result'])

    def test_unchanged_state_still_expires_a_disconnected_control_lease(self):
        self.open_room()
        state_id = self.state()['engine']['last_result']['state_id']
        self.post('claim', {**self.public_identity(), 'vehicle_id': 'P1', 'join_token': self.identity['join_token']})
        now = time.monotonic()
        with patch.object(self.server.interactive.room, '_clock', return_value=now+10):
            state = self.request('/api/interactive/state?compact=1&since_state_id='+state_id)[2]
        self.assertTrue(state['state_unchanged'])
        self.assertFalse(state['room']['vehicles']['P1']['claimed'])
        self.assertEqual(state['room']['vehicles']['P1']['mode'], 'ai')

    def test_invalid_compact_query_cannot_change_room_or_advance_physics(self):
        self.open_room()
        for query in ('compact=yes', 'compact=1&compact=0', 'render=true', 'since_state_id='+'x'*129):
            self.assertEqual(self.request('/api/interactive/state?'+query)[0], 400)
        self.assertEqual(len(self.broker.commands), 1)
        interest = self.server.interactive.render_until
        self.assertEqual(self.request('/api/interactive/state?render=1&session_id=stale-room')[0], 400)
        self.assertEqual(self.server.interactive.render_until, interest)


if __name__ == "__main__":
    unittest.main()
