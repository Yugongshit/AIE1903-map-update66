"""Thread-safe authority and latest-action mailboxes for one shared simulator.

This module never advances physics, executes planners, or follows client URLs.
The server owns the simulation clock. Public snapshots contain only current
states; host/join tickets and vehicle leases never enter spectator responses.
"""
from __future__ import annotations

from copy import deepcopy
import math
import secrets
import threading
import time
import uuid


VEHICLE_IDS = ("ego", "P1", "P2", "P3", "P4", "P5")


class RoomError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RoomError("invalid_input", name + " must be finite", 400)
    return float(value)


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise RoomError("invalid_input", name + " must be a nonnegative integer", 400)
    return value


def _action(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RoomError("invalid_action", "Action must be [steering, throttle]", 400)
    result = [_number(v, "action") for v in value]
    if any(abs(v) > 1 for v in result):
        raise RoomError("invalid_action", "Action values must lie within [-1, 1]", 400)
    return result


def _token_matches(candidate, expected):
    return (isinstance(candidate, str) and len(candidate) <= 512 and isinstance(expected, str)
            and secrets.compare_digest(candidate.encode("utf-8", errors="surrogatepass"), expected.encode("utf-8")))


def _current_actor(actor):
    """Whitelisted scalar/current-pose fields, never recorded trajectory arrays."""
    if not isinstance(actor, dict):
        raise RoomError("invalid_state", "Current actor state must be an object", 400)
    result = {}
    for key in ("vehicle_id", "id", "native_object_id", "type", "motion_source", "label", "mode", "stopped_reason"):
        if key in actor:
            if actor[key] is None and key == "stopped_reason":
                result[key] = None
                continue
            if not isinstance(actor[key], str):
                raise RoomError("invalid_state", key + " must be a string", 400)
            result[key] = actor[key][:256]
    for key in ("position", "position_scenario_m", "position_city_m", "velocity", "velocity_m_s"):
        if key in actor:
            values = actor[key]
            if not isinstance(values, (list, tuple)) or len(values) not in (2, 3):
                raise RoomError("invalid_state", key + " must be a single XY/XYZ vector", 400)
            result[key] = [_number(v, key) for v in values]
    for key in ("heading_rad", "speed_m_s", "length_m", "width_m", "height_m"):
        if key in actor:
            result[key] = _number(actor[key], key)
    for key in ("crash_vehicle", "crash_object", "crash_human", "crash_building", "collision_stop", "arrived"):
        if key in actor:
            if type(actor[key]) is not bool:
                raise RoomError("invalid_state", key + " must be boolean", 400)
            result[key] = actor[key]
    if "collision" in actor:
        collision = actor["collision"]
        if (not isinstance(collision, dict) or set(collision) - {"vehicle", "object", "human", "building"}
                or any(type(value) is not bool for value in collision.values())):
            raise RoomError("invalid_state", "Collision state must contain current boolean contact flags", 400)
        result["collision"] = dict(collision)
    for key in ("action", "requested_action"):
        if key in actor:
            result[key] = _action(actor[key])
    return result


class InteractiveRoom:
    """One room generation, six vehicle slots, exclusive renewable leases.

``controls_for_tick`` is the server ticker's only control read. Viewers calling
``snapshot`` or fetching a PNG cannot advance the simulation. Manual/external
actions expire after 0.4 seconds; after 3 seconds without a fresh action the
lease is released (P cars return to AI; ego stays stopped under external mode).
Heartbeats extend a lease but never refresh an old driving command.
"""
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time,
                 manual_timeout_s=0.4, lease_timeout_s=3.0,
                 action_release_timeout_s=3.0, external_max_lag_steps=5):
        if not 0 < manual_timeout_s < action_release_timeout_s:
            raise ValueError("Action timeout must be positive and shorter than release timeout")
        if lease_timeout_s <= 0 or type(external_max_lag_steps) is not int or external_max_lag_steps < 0:
            raise ValueError("Invalid lease timeout or external observation lag")
        self._clock, self._wall_clock = clock, wall_clock
        self.manual_timeout_s = float(manual_timeout_s)
        self.lease_timeout_s = float(lease_timeout_s)
        self.action_release_timeout_s = float(action_release_timeout_s)
        self.external_max_lag_steps = external_max_lag_steps
        self._lock = threading.RLock()
        self._epoch = 0
        self._session_id = None
        self._active = False
        self._host_token = self._join_token = None
        self._vehicles, self._leases = {}, {}
        self._scene = self._version = self._frame_id = None
        self._step = 0
        self._published_step = None
        self._objects = []

    def create(self, scene, version, missions=()):
        if not isinstance(scene, str) or not scene or not isinstance(version, str) or not version:
            raise RoomError("invalid_scene", "Scene and map version are required", 400)
        if not isinstance(missions, (list, tuple)):
            raise RoomError("invalid_missions", "Missions must be an array", 400)
        initial = {}
        for mission in missions:
            if not isinstance(mission, dict):
                raise RoomError("invalid_missions", "Mission must be an object", 400)
            identifier = mission.get("vehicle_id")
            if identifier not in VEHICLE_IDS or identifier in initial:
                raise RoomError("invalid_missions", "Mission vehicle ID is invalid or repeated", 400)
            initial[identifier] = _current_actor(mission)
        with self._lock:
            self._epoch += 1
            self._session_id = uuid.uuid4().hex
            self._host_token, self._join_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._active, self._scene, self._version = True, scene, version
            self._step, self._published_step, self._frame_id = 0, None, None
            self._objects, self._leases = [], {}
            self._vehicles = {key: {"mode": "external" if key == "ego" else "ai", "lease": None,
                                   "action": [0.0, -1.0], "input_at": None, "received_at_s": None,
                                   "observation_step": None, "last_seq": None,
                                   "reason": "awaiting_external_controller" if key == "ego" else "default_ai",
                                   "state": initial.get(key, {})} for key in VEHICLE_IDS}
            return {"session_id": self._session_id, "epoch": self._epoch,
                    "host_token": self._host_token, "join_token": self._join_token,
                    "snapshot": self.snapshot()}

    def _identity(self, session_id, epoch):
        if not self._active:
            raise RoomError("room_closed", "No active interactive room", 409)
        if session_id != self._session_id or type(epoch) is not int or epoch != self._epoch:
            raise RoomError("stale_session", "Room session or simulation generation changed", 409)

    def _host(self, token):
        if not _token_matches(token, self._host_token):
            raise RoomError("host_required", "A valid host ticket is required", 403)

    @staticmethod
    def _ids(vehicle_id, vehicle_ids):
        ids = vehicle_ids if vehicle_ids is not None else ([vehicle_id] if isinstance(vehicle_id, str) else vehicle_id)
        if not isinstance(ids, (list, tuple)) or not ids or any(key not in VEHICLE_IDS for key in ids):
            raise RoomError("invalid_vehicle", "Choose ego or P1 through P5", 400)
        if len(set(ids)) != len(ids):
            raise RoomError("invalid_vehicle", "A vehicle may occur only once in a batch", 400)
        return list(ids)

    def _drop_vehicle(self, identifier, reason):
        slot = self._vehicles[identifier]
        token = slot["lease"]
        if token in self._leases:
            self._leases[token]["vehicle_ids"].remove(identifier)
            if not self._leases[token]["vehicle_ids"]:
                del self._leases[token]
        slot.update(mode="external" if identifier == "ego" else "ai", lease=None,
                    action=[0.0, -1.0], input_at=None, observation_step=None, reason=reason)

    def _expire(self, now):
        for identifier, slot in self._vehicles.items():
            token = slot["lease"]
            if token is None:
                continue
            lease = self._leases[token]
            action_age = now - (slot["input_at"] if slot["input_at"] is not None else lease["created_at"])
            if now >= lease["expires_at"]:
                self._drop_vehicle(identifier, "lease_expired")
            elif action_age >= self.action_release_timeout_s:
                self._drop_vehicle(identifier, "action_stream_expired")

    def _lease(self, token, now):
        self._expire(now)
        if not isinstance(token, str) or token not in self._leases:
            raise RoomError("lease_expired", "Control lease is missing, released or expired", 403)
        return self._leases[token]

    def claim(self, vehicle_id=None, *, vehicle_ids=None, session_id, epoch,
              host_token=None, join_token=None, kind="manual", client_label=""):
        ids = self._ids(vehicle_id, vehicle_ids)
        if not isinstance(kind, str) or kind not in {"manual", "external"}:
            raise RoomError("invalid_mode", "A lease is manual or external", 400)
        if kind == "manual" and len(ids) != 1:
            raise RoomError("invalid_claim", "Manual players claim one vehicle; external clients may claim a batch", 400)
        if not isinstance(client_label, str) or len(client_label) > 80:
            raise RoomError("invalid_label", "Client label must be at most 80 characters", 400)
        with self._lock:
            self._identity(session_id, epoch)
            if not (_token_matches(host_token, self._host_token) or _token_matches(join_token, self._join_token)):
                raise RoomError("join_required", "A valid room invitation or host ticket is required", 403)
            now = self._clock()
            self._expire(now)
            if any(self._vehicles[key]["lease"] is not None for key in ids):
                raise RoomError("vehicle_claimed", "At least one requested vehicle already has a controller", 409)
            token = secrets.token_urlsafe(32)
            self._leases[token] = {"vehicle_ids": set(ids), "kind": kind, "client_label": client_label,
                                   "created_at": now, "expires_at": now+self.lease_timeout_s, "last_seq": -1}
            for identifier in ids:
                self._vehicles[identifier].update(mode=kind, lease=token, action=[0.0, -1.0],
                    input_at=None, received_at_s=None, observation_step=None, last_seq=None,
                    reason="awaiting_first_action")
            return {"session_id": self._session_id, "epoch": self._epoch,
                    "lease_token": token, "vehicle_ids": ids, "kind": kind,
                    "expires_in_ms": round(self.lease_timeout_s*1000)}

    def input(self, lease_token, *, session_id, epoch, seq, actions, observation_step=None):
        _integer(seq, "seq")
        if not isinstance(actions, dict) or not actions:
            raise RoomError("invalid_action", "actions must map vehicle IDs to bounded action pairs", 400)
        validated = {key: _action(value) for key, value in actions.items()}
        with self._lock:
            self._identity(session_id, epoch)
            now = self._clock()
            lease = self._lease(lease_token, now)
            if not set(validated) <= lease["vehicle_ids"]:
                raise RoomError("vehicle_unowned", "An action addresses a vehicle outside this lease", 403)
            if seq <= lease["last_seq"]:
                raise RoomError("stale_action", "Action sequence must increase strictly", 409)
            if lease["kind"] == "external":
                _integer(observation_step, "observation_step")
                if observation_step > self._step or self._step-observation_step > self.external_max_lag_steps:
                    raise RoomError("stale_observation", "External observation is future or too far behind the engine", 409)
            elif observation_step is not None:
                _integer(observation_step, "observation_step")
            receipt_time = self._wall_clock()
            for key, action in validated.items():
                self._vehicles[key].update(action=action, input_at=now, received_at_s=receipt_time,
                    observation_step=observation_step, last_seq=seq, reason="fresh_action")
            lease.update(last_seq=seq, expires_at=now+self.lease_timeout_s)
            return {"accepted": True, "session_id": self._session_id, "epoch": self._epoch,
                    "seq": seq, "received_at_s": receipt_time, "step": self._step}

    def heartbeat(self, lease_token, *, session_id, epoch):
        with self._lock:
            self._identity(session_id, epoch)
            now = self._clock()
            lease = self._lease(lease_token, now)
            lease["expires_at"] = now+self.lease_timeout_s
            return {"accepted": True, "expires_in_ms": round(self.lease_timeout_s*1000)}

    def release(self, lease_token, *, session_id, epoch):
        with self._lock:
            self._identity(session_id, epoch)
            lease = self._lease(lease_token, self._clock())
            for identifier in list(lease["vehicle_ids"]):
                self._drop_vehicle(identifier, "released")
            return {"released": True}

    def set_mode(self, vehicle_id, mode, *, session_id, epoch, host_token):
        if (vehicle_id not in VEHICLE_IDS or not isinstance(mode, str)
                or mode not in {"ai", "manual", "external"} or (vehicle_id == "ego" and mode == "ai")):
            raise RoomError("invalid_mode", "ego supports manual/external; P1..P5 also support AI", 400)
        with self._lock:
            self._identity(session_id, epoch)
            self._host(host_token)
            self._drop_vehicle(vehicle_id, "host_mode_change")
            self._vehicles[vehicle_id].update(mode=mode, action=[0.0, -1.0])
            return {"vehicle_id": vehicle_id, "mode": mode}

    def authorize_host(self, *, session_id, epoch, host_token):
        """Coordinator checks this before play/pause/reset/stop operations."""
        with self._lock:
            self._identity(session_id, epoch)
            self._host(host_token)
            return True

    def close(self, *, session_id, epoch, host_token):
        with self._lock:
            self.authorize_host(session_id=session_id, epoch=epoch, host_token=host_token)
            for identifier in self._vehicles:
                self._drop_vehicle(identifier, "room_closed")
            self._active = False
            return {"closed": True}

    def controls_for_tick(self, step):
        _integer(step, "step")
        with self._lock:
            if not self._active:
                raise RoomError("room_closed", "No active interactive room", 409)
            if step < self._step:
                raise RoomError("stale_step", "Create a new room generation before rewinding", 409)
            self._step = step
            now = self._clock()
            self._expire(now)
            vehicles = {identifier: self._control(slot, now) for identifier, slot in self._vehicles.items()}
            return {"session_id": self._session_id, "epoch": self._epoch, "step": self._step, "vehicles": vehicles}

    def _control(self, slot, now):
        action, reason = None, slot["reason"]
        deadline = None
        if slot["mode"] != "ai":
            action = [0.0, -1.0]
            if slot["lease"] is None or slot["input_at"] is None:
                pass
            elif now-slot["input_at"] >= self.manual_timeout_s:
                reason = "action_deadman_brake"
            elif slot["mode"] == "external" and self._step-slot["observation_step"] > self.external_max_lag_steps:
                reason = "observation_deadman_brake"
            else:
                action, reason = list(slot["action"]), "fresh_action"
                deadline = slot["input_at"] + self.manual_timeout_s
        result = {"mode": slot["mode"], "action": action, "reason": reason}
        if deadline is not None:
            # Only the same-host native worker receives this internal clock.
            # A command delayed after dispatch must not revive old throttle.
            result["expires_at_monotonic"] = deadline
        return result

    def publish(self, result, frame_id=None, *, session_id, epoch):
        """Bind present native states to an opaque rendered frame identity."""
        if not isinstance(result, dict):
            raise RoomError("invalid_state", "Native room result must be an object", 400)
        source = result.get("interactive", result)
        if not isinstance(source, dict):
            raise RoomError("invalid_state", "Interactive native result must be an object", 400)
        step = result.get("step", source.get("step"))
        _integer(step, "step")
        if frame_id is not None and not isinstance(frame_id, str):
            raise RoomError("invalid_state", "Frame ID must be a string", 400)
        actors = source.get("vehicles", {})
        if not isinstance(actors, dict) or set(actors) != set(VEHICLE_IDS):
            raise RoomError("invalid_state", "Native result must contain ego and all five player vehicles", 400)
        vehicle_states = {key: _current_actor(value) for key, value in actors.items()}
        objects = source.get("objects", source.get("static_actors", []))
        if not isinstance(objects, list) or len(objects) > 10000:
            raise RoomError("invalid_state", "Current objects must be a bounded array", 400)
        current_objects = [_current_actor(value) for value in objects]
        with self._lock:
            self._identity(session_id, epoch)
            if step < self._step:
                raise RoomError("stale_frame", "Native result belongs to an older simulation step", 409)
            self._step, self._published_step, self._frame_id = step, step, frame_id
            for key, state in vehicle_states.items():
                self._vehicles[key]["state"] = state
            self._objects = current_objects
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            now = self._clock()
            self._expire(now)
            vehicles = {}
            for identifier, slot in self._vehicles.items():
                lease = self._leases.get(slot["lease"])
                control = self._control(slot, now)
                vehicles[identifier] = {"vehicle_id": identifier, "mode": slot["mode"],
                    "claimed": lease is not None, "client_label": lease["client_label"] if lease else None,
                    "lease_expires_in_ms": max(0, round((lease["expires_at"]-now)*1000)) if lease else None,
                    "last_input_age_ms": round((now-slot["input_at"])*1000) if slot["input_at"] is not None else None,
                    "last_action_seq": slot["last_seq"], "received_at_s": slot["received_at_s"],
                    "observation_step": slot["observation_step"], "reason": control["reason"],
                    "effective_action": control["action"],
                    "state": deepcopy(slot["state"])}
            return {"active": self._active, "session_id": self._session_id, "epoch": self._epoch,
                    "scene_id": self._scene, "version": self._version, "step": self._step,
                    "observation_step": self._published_step, "frame_id": self._frame_id,
                    "vehicles": vehicles, "objects": deepcopy(self._objects),
                    "coordinate_frame": "source_city_XY_metres", "clock_owner": "server",
                    "recorded_future_tracks_included": False,
                    "action_timeout_ms": round(self.manual_timeout_s*1000),
                    "lease_timeout_ms": round(self.lease_timeout_s*1000),
                    "external_max_lag_steps": self.external_max_lag_steps}
