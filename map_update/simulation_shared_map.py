"""Versioned shared-map delivery and independent, source-lane fleet planning.

All public coordinates are source city XY metres, including nearby actors.
Publication, receipt and vehicle control are separate operations: a vehicle
that is disconnected retains its own last received map. Offline variants are
explicit counterfactuals; online subscriptions never accept full-scene preview
objects. This is a low-speed research controller, not a complete driving stack.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon, shape

from .simulation_planner import (
    LaneGraphController, _length, _project, _slice, _xy, plan_route,
)


LANE_TYPES = {"LANE_SURFACE_STREET", "LANE_SURFACE_UNSTRUCTURE", "LANE_FREEWAY"}


def _plain(value):
    def default(item):
        if hasattr(item, "tolist"):
            return item.tolist()
        if hasattr(item, "item"):
            return item.item()
        raise TypeError(f"Unsupported map value: {type(item).__name__}")
    return json.loads(json.dumps(value, default=default, allow_nan=False))


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _geometry(obj):
    """Read source geometry; runtime-centered paths are intentionally ignored."""
    raw = obj.get("geometry_city") or obj.get("geometry")
    if raw:
        geometry = shape(raw)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if not geometry.is_empty:
            return geometry
    position = obj.get("position", obj.get("center_xy"))
    if position is None:
        return None
    x, y = _xy(position)
    dimensions = obj.get("dimensions_m") or {}
    if isinstance(dimensions, (list, tuple)):
        length, width = dimensions[:2]
    else:
        length = obj.get("length_m", obj.get("length", dimensions.get("length", 0.5)))
        width = obj.get("width_m", obj.get("width", dimensions.get("width", 0.5)))
    length, width = _positive(length, "object length"), _positive(width, "object width")
    heading = float(obj.get("heading_rad", obj.get("yaw_rad", obj.get("heading", 0))))
    if not math.isfinite(heading):
        raise ValueError("Object heading must be finite")
    c, s = math.cos(heading), math.sin(heading)
    return Polygon([(x+c*dx-s*dy, y+s*dx+c*dy) for dx, dy in (
        (-length/2, -width/2), (length/2, -width/2),
        (length/2, width/2), (-length/2, width/2))])


def _active_objects(objects):
    for obj in objects:
        if obj.get("status") in {"cleared", "removed"}:
            continue
        geometry = _geometry(obj)
        if geometry is not None and not geometry.is_empty:
            yield str(obj.get("id", obj.get("object_id", "unknown"))), geometry


def _state_semantics(objects):
    """Fresh source-frame metadata alone must not invalidate a driving route."""
    rows = []
    for obj in objects:
        geometry = _geometry(obj)
        rows.append({"id": str(obj.get("id", obj.get("object_id", "unknown"))),
                     "status": obj.get("status"), "event_states": obj.get("event_states"),
                     "geometry": geometry.__geo_interface__ if geometry is not None else None})
    return sorted(rows, key=lambda row: (row["id"], _digest(row)))


def _state_change_affects_route(subscriber, objects):
    if subscriber.dirty_reason or subscriber.current_route.get("status") not in {"planned", "tracking"}:
        return True
    old = {row["id"]: row for row in _state_semantics(subscriber.objects)}
    new = {row["id"]: row for row in _state_semantics(objects)}
    changed = [row for key in old.keys() | new.keys() if old.get(key) != new.get(key)
               for row in (old.get(key), new.get(key)) if row and row["geometry"]]
    if not changed:
        return False
    paths = [subscriber.current_route.get("points", [])]
    # A previously blocked source lane becoming available can restore the
    # shorter route even when the vehicle is currently following a detour.
    paths.extend(subscriber.features[key].get("polyline", [])
                 for key in subscriber.current_route.get("blocked_lane_ids", [])
                 if key in subscriber.features)
    corridors = [LineString(path).buffer(subscriber.width/2+0.25, cap_style=2)
                 for path in paths if len(path) > 1 and _length(path) > 1e-8]
    return any(corridor.intersects(shape(row["geometry"])) for row in changed for corridor in corridors)


def _obstacle_plan(features, start, goal, heading, vehicle_class, objects, width):
    """Filter obstructed source lanes without reassigning the matched endpoints.

Start and goal lanes are clipped to the actual requested interval. Removing a
blocked endpoint lane then snapping onto its neighbour would invent a lane
change, so blocked endpoints fail closed instead. Only supplied directed exits
can provide a detour around an obstructed intermediate lane.
"""
    initial = plan_route(features, start, goal, heading_rad=heading, vehicle_class=vehicle_class)
    initial["blocked_lane_ids"] = []
    if initial["status"] != "planned" or not objects:
        return initial, features
    obstacles = list(_active_objects(objects))
    if not obstacles:
        return initial, features
    first, last = initial["lane_ids"][0], initial["lane_ids"][-1]
    blocked, obstacle_ids = [], set()
    for lane_id, feature in features.items():
        if feature.get("type") not in LANE_TYPES:
            continue
        points = [_xy(p) for p in feature.get("polyline", [])]
        if len(points) < 2 or _length(points) < 1e-8:
            continue
        begin = _project(start, points)[1] if lane_id == first else 0.0
        end = _project(goal, points)[1] if lane_id == last else _length(points)
        if end <= begin + 1e-8:
            continue
        # Flat corridor ends ensure an object beyond the chosen goal or behind
        # the current vehicle does not falsely close the remaining route.
        corridor = LineString(_slice(points, begin, end)).buffer(width/2 + 0.25, cap_style=2)
        hits = [key for key, geometry in obstacles if corridor.intersects(geometry)]
        if hits:
            blocked.append(str(lane_id))
            obstacle_ids.update(hits)
    if not blocked:
        return initial, features
    filtered = {key: value for key, value in features.items() if key not in blocked}
    if first in blocked or last in blocked:
        result = dict(initial, status="no_route", reason="shared_state_blocks_endpoint_lane",
                      points=[], lane_ids=[])
    else:
        result = plan_route(filtered, start, goal, heading_rad=heading, vehicle_class=vehicle_class)
        if result["status"] == "planned" and (
                result["lane_ids"][0] != first or result["lane_ids"][-1] != last):
            result = dict(result, status="no_route", reason="shared_state_would_reassign_endpoint",
                          points=[], lane_ids=[])
        elif result["status"] != "planned":
            result["reason"] = "shared_state_no_directed_detour"
    result.update(blocked_lane_ids=sorted(blocked), blocking_object_ids=sorted(obstacle_ids),
                  scope="source_directed_lanes_with_shared_state_corridor_exclusion")
    return result, filtered


@dataclass
class _Subscriber:
    vehicle_id: str
    goal: list
    controller: LaneGraphController
    features: dict
    enabled: bool = True
    objects: list = field(default_factory=list)
    sequence: int = -1
    received_version: str | None = None
    received_step: int | None = None
    published_step: int | None = None
    source_timestamp_ns: int | None = None
    map_hash: str | None = None
    state_hash: str | None = None
    map_key: str = "original"
    offline_prior: bool = False
    dirty_reason: str | None = "initial_plan"
    replan_count: int = 0
    last_replan_reason: str | None = None
    previous_route: dict = field(default_factory=dict)
    current_route: dict = field(default_factory=dict)
    filtered_features: dict | None = None
    width: float | None = None
    stopped_reason: str | None = None
    braking: dict = field(default_factory=dict)
    last_step: int | None = None
    last_delivery_affects_route: bool | None = None


class SharedMapService:
    """Central publication plus independently cached per-vehicle subscribers.

``old`` receives original structure and no attached state; ``structure`` gets
updated structure only; ``full`` gets both. Local actor braking is available in
every variant. In online mode all variants start with original structure until
an explicit ``publish_structure=True`` publication. The caller owns the clock,
publication schedule and delivery schedule; no wall-time thread is involved.
"""
    def __init__(self, original_map, updated_map, *, scene_id, mode="offline", variant="full"):
        if mode not in {"offline", "online"} or variant not in {"old", "structure", "full"}:
            raise ValueError("mode must be offline/online; variant must be old/structure/full")
        self.scene_id, self.mode, self.variant = str(scene_id), mode, variant
        self._original, self._updated = _plain(original_map), _plain(updated_map)
        self._original_hash, self._updated_hash = _digest(self._original), _digest(self._updated)
        self._state_semantic_hash = _digest([])
        self._last_publish_step = 0
        self._subscribers = {}
        self._structure_published = mode == "offline" and variant != "old"
        features = self._updated if self._structure_published else self._original
        self._message = self._make_message(0, 0, features, [], None, 0, mode == "offline")

    def _make_message(self, sequence, step, features, objects, timestamp, observed_step, prior):
        message = {"scene_id": self.scene_id, "sequence": sequence, "published_step": step,
                   "observed_step": observed_step, "source_timestamp_ns": timestamp,
                   "offline_prior": prior, "coordinate_frame": "source_city_m",
                   "map_key": "updated" if self._structure_published else "original",
                   "map_features": features, "state_objects": objects,
                   "map_hash": self._updated_hash if self._structure_published else self._original_hash,
                   "state_hash": _digest(objects)}
        message["version"] = _digest(message)
        return message

    def subscribe(self, vehicle_id, goal, *, cruise_speed_m_s=3.0, vehicle_class="vehicle", enabled=True):
        vehicle_id = str(vehicle_id)
        if vehicle_id in self._subscribers:
            raise ValueError(f"Vehicle {vehicle_id} already has an independent subscription")
        subscriber = _Subscriber(vehicle_id, list(_xy(goal)),
                                 LaneGraphController(cruise_speed_m_s, vehicle_class=vehicle_class),
                                 copy.deepcopy(self._original), bool(enabled))
        self._subscribers[vehicle_id] = subscriber
        return self.vehicle_status(vehicle_id)

    def set_subscription(self, vehicle_id, enabled):
        self._subscribers[str(vehicle_id)].enabled = bool(enabled)

    def set_goal(self, vehicle_id, goal):
        subscriber = self._subscribers[str(vehicle_id)]
        goal = list(_xy(goal))
        if subscriber.goal != goal:
            subscriber.goal, subscriber.dirty_reason = goal, "goal_changed"

    def publish(self, step, *, state_objects=(), source_timestamp_ns=None,
                observed_step=None, offline_prior=False, publish_structure=False):
        step = int(step)
        observed_step = step if observed_step is None else int(observed_step)
        if step < self._last_publish_step or observed_step > step or observed_step < 0:
            raise ValueError("Publication must follow the simulation clock without future observations")
        if offline_prior and self.mode != "offline":
            raise ValueError("Online delivery cannot use an offline full-scene prior")
        if source_timestamp_ns is not None:
            if isinstance(source_timestamp_ns, bool) or not str(source_timestamp_ns).isdigit():
                raise ValueError("source_timestamp_ns must be an integer nanosecond timestamp")
            source_timestamp_ns = int(source_timestamp_ns)
        objects = _plain(list(state_objects))
        self._validate_objects(objects, source_timestamp_ns, offline_prior)
        if publish_structure and self.variant != "old":
            self._structure_published = True
        features = self._updated if self._structure_published else self._original
        objects = objects if self.variant == "full" else []
        map_hash = self._updated_hash if self._structure_published else self._original_hash
        state_semantic_hash = _digest(_state_semantics(objects))
        self._last_publish_step = step
        if (map_hash == self._message["map_hash"] and state_semantic_hash == self._state_semantic_hash):
            return None
        self._state_semantic_hash = state_semantic_hash
        self._message = self._make_message(self._message["sequence"]+1, step, features, objects,
                                          source_timestamp_ns, observed_step, bool(offline_prior))
        return copy.deepcopy(self._message)

    def _validate_objects(self, objects, timestamp, offline_prior):
        if timestamp is not None and (not isinstance(timestamp, int) or timestamp < 0):
            raise ValueError("source_timestamp_ns must be a nonnegative integer")
        for obj in objects:
            if not isinstance(obj, dict):
                raise ValueError("Shared state objects must be dictionaries")
            preview = (obj.get("scope") == "full_scene_overview" or
                       obj.get("planning_eligible") is False or
                       obj.get("may_include_later_observations") is True)
            if preview and not offline_prior:
                raise ValueError("Full-scene preview is not causal Planning input; declare an offline prior")
            if self.mode == "online":
                observed = obj.get("last_observed_timestamp_ns", obj.get("source_timestamp_ns"))
                if timestamp is None or observed is None or int(observed) > timestamp:
                    raise ValueError("Online state needs an observation timestamp at or before publication cutoff")
            _geometry(obj)  # Reject malformed geometry before publishing any part.

    def latest_message(self):
        return copy.deepcopy(self._message)

    def deliver(self, vehicle_id, step):
        subscriber = self._subscribers[str(vehicle_id)]
        # Internal publications are immutable; do not serialize/hash a whole
        # city map twice per physics tick when no new version exists.
        if not subscriber.enabled or self._message["sequence"] <= subscriber.sequence:
            return False
        return self.receive(vehicle_id, self._message, step)

    def receive(self, vehicle_id, message, step):
        subscriber = self._subscribers[str(vehicle_id)]
        message = _plain(message)
        if message.get("scene_id") != self.scene_id or message.get("coordinate_frame") != "source_city_m":
            raise ValueError("Message scene or coordinate frame does not match this vehicle")
        signed = {key: value for key, value in message.items() if key != "version"}
        if (message.get("version") != _digest(signed) or
                message.get("map_hash") != _digest(message["map_features"]) or
                message.get("state_hash") != _digest(message["state_objects"])):
            raise ValueError("Shared map message content does not match its version")
        if message["published_step"] > int(step) or message["observed_step"] > message["published_step"]:
            raise ValueError("Cannot receive a future publication")
        if subscriber.received_step is not None and int(step) < subscriber.received_step:
            raise ValueError("Reset the shared-map service before rewinding delivery")
        if message["offline_prior"] and self.mode != "offline":
            raise ValueError("Online subscribers cannot accept offline priors")
        self._validate_objects(message["state_objects"], message["source_timestamp_ns"], message["offline_prior"])
        if message["sequence"] <= subscriber.sequence:
            return False
        changed_map = subscriber.map_hash != message["map_hash"]
        changed_state = subscriber.state_hash != message["state_hash"]
        affects_route = changed_map or (changed_state and _state_change_affects_route(subscriber, message["state_objects"]))
        subscriber.features, subscriber.objects = message["map_features"], message["state_objects"]
        subscriber.sequence, subscriber.received_version = message["sequence"], message["version"]
        subscriber.received_step, subscriber.published_step = int(step), message["published_step"]
        subscriber.source_timestamp_ns = message["source_timestamp_ns"]
        subscriber.map_hash, subscriber.state_hash = message["map_hash"], message["state_hash"]
        subscriber.map_key, subscriber.offline_prior = message["map_key"], message["offline_prior"]
        subscriber.last_delivery_affects_route = bool(affects_route)
        if affects_route:
            subscriber.dirty_reason = ("initial_delivery" if subscriber.replan_count == 0 else
                                       "map_structure_received" if changed_map else "attached_state_received")
        return True

    def action(self, vehicle_id, state):
        subscriber = self._subscribers[str(vehicle_id)]
        position = _xy(state["position"])
        heading, speed = float(state["heading_rad"]), float(state["speed_m_s"])
        if not math.isfinite(heading) or not math.isfinite(speed) or speed < 0:
            raise ValueError("Vehicle heading/speed must be finite and speed nonnegative")
        step = int(state.get("step", 0))
        width = _positive(state.get("width_m", 1.8), "vehicle width")
        if subscriber.last_step is not None and step < subscriber.last_step:
            raise ValueError("Reset the shared-map service when rewinding the simulation")
        if subscriber.width != width:
            subscriber.dirty_reason = subscriber.dirty_reason or "vehicle_geometry_changed"
        if subscriber.dirty_reason:
            subscriber.previous_route = copy.deepcopy(subscriber.current_route)
            plan, filtered = _obstacle_plan(subscriber.features, position, subscriber.goal, heading,
                                           subscriber.controller.vehicle_class, subscriber.objects, width)
            subscriber.current_route, subscriber.filtered_features = plan, filtered
            subscriber.width = width
            subscriber.replan_count += 1
            subscriber.last_replan_reason, subscriber.dirty_reason = subscriber.dirty_reason, None
            # New controller per knowledge revision; no module-level singleton.
            subscriber.controller = LaneGraphController(subscriber.controller.cruise_speed_m_s,
                                                        vehicle_class=subscriber.controller.vehicle_class)
        subscriber.last_step = step
        subscriber.braking = {"shared_map": None, "local_actor": None}
        subscriber.stopped_reason = None
        if subscriber.current_route.get("status") == "no_route":
            subscriber.stopped_reason = subscriber.current_route["reason"]
            subscriber.braking["shared_map"] = subscriber.stopped_reason
            subscriber.braking["local_actor"] = self._brake_horizon(
                subscriber, position, speed, state, state.get("nearby_actors", []))
            return [0.0, -1.0]
        controller_state = {**state, "step": step, "position_runtime_m": list(position),
                            "map_features_runtime": subscriber.filtered_features,
                            # A receipt may only concern a distant obstacle.
                            # Invalidate this controller on actual route work,
                            # not every independently recorded map receipt.
                            "map_version": subscriber.replan_count, "scenario_id": self.scene_id,
                            "goal_scenario_m": subscriber.goal, "runtime_origin_in_scenario_m": [0, 0]}
        action = subscriber.controller(controller_state)
        diagnostics = subscriber.controller.diagnostics()
        subscriber.current_route.update({key: value for key, value in diagnostics.items()
                                         if key in {"status", "progress_m", "lateral_error_m", "target_speed_m_s"}})
        if diagnostics["status"] not in {"planned", "tracking"}:
            subscriber.stopped_reason = diagnostics.get("reason", diagnostics["status"])
            if diagnostics["status"] == "goal_reached":
                subscriber.stopped_reason = "goal_reached"
        for source, objects in (("shared_map", subscriber.objects),
                                ("local_actor", state.get("nearby_actors", []))):
            hit = self._brake_horizon(subscriber, position, speed, state, objects)
            if hit:
                subscriber.braking[source] = hit
                subscriber.stopped_reason = f"{source}_braking_horizon"
                action = [action[0], -1.0]
        return action

    @staticmethod
    def _brake_horizon(subscriber, position, speed, state, objects):
        points = subscriber.current_route.get("points", [])
        length = _positive(state.get("length_m", 4.5), "vehicle length")
        horizon = length/2 + 1.0 + speed*0.4 + speed*speed/6.0
        if len(points) < 2:
            # Emergency detection still works while the map planner has no
            # route; this line is a sensor corridor, never a driving route.
            heading = float(state["heading_rad"])
            points = [position, (position[0]+horizon*math.cos(heading),
                                 position[1]+horizon*math.sin(heading))]
        _, progress, _, _ = _project(position, points)
        end = min(_length(points), progress+horizon)
        if end <= progress + 1e-8:
            return None
        corridor = LineString(_slice(points, progress, end)).buffer(subscriber.width/2+0.25, cap_style=2)
        for obj in objects:
            if str(obj.get("id", obj.get("object_id", ""))) == subscriber.vehicle_id:
                continue
            for key, geometry in _active_objects([obj]):
                if corridor.intersects(geometry):
                    return {"object_id": key, "horizon_m": round(horizon, 3),
                            "distance_m": round(Point(position).distance(geometry), 3)}
        return None

    def get_received_snapshot(self, vehicle_id, *, include_map=True):
        subscriber = self._subscribers[str(vehicle_id)]
        map_hash = subscriber.map_hash or self._original_hash
        snapshot = {"scene_id": self.scene_id, "vehicle_id": subscriber.vehicle_id,
                              "coordinate_frame": "source_city_m",
                              "map_key": subscriber.map_key, "map_features_sha256": map_hash,
                              "offline_prior": subscriber.offline_prior,
                              "version": subscriber.received_version,
                              "received_version": subscriber.received_version,
                              "received_sequence": subscriber.sequence,
                              "received_step": subscriber.received_step,
                              "published_step": subscriber.published_step,
                              "source_timestamp_ns": subscriber.source_timestamp_ns,
                              "state_objects": subscriber.objects}
        if include_map:
            snapshot["map_features"] = subscriber.features
        return copy.deepcopy(snapshot)

    def planning_input(self, vehicle_id, vehicle_state, *, include_map=True):
        """Export only this receiver's knowledge, own pose/goal and local sensing."""
        snapshot = self.get_received_snapshot(vehicle_id, include_map=include_map)
        subscriber = self._subscribers[str(vehicle_id)]
        own_state = {key: copy.deepcopy(vehicle_state[key]) for key in (
            "step", "position", "heading_rad", "speed_m_s", "length_m", "width_m",
            "wheelbase_m", "max_steering_rad", "nearby_actors") if key in vehicle_state}
        snapshot.update(goal_scenario_m=list(subscriber.goal), vehicle_state=own_state,
                        delivery_scope="this_vehicle_last_received_snapshot",
                        uses_recorded_trajectory=False)
        return snapshot

    def vehicle_status(self, vehicle_id):
        subscriber = self._subscribers[str(vehicle_id)]
        return copy.deepcopy({
            "vehicle_id": subscriber.vehicle_id, "goal_scenario_m": subscriber.goal,
            "subscribed": subscriber.enabled, "received_version": subscriber.received_version,
            "received_sequence": subscriber.sequence, "received_step": subscriber.received_step,
            "published_step": subscriber.published_step, "source_timestamp_ns": subscriber.source_timestamp_ns,
            "map_hash": subscriber.map_hash, "state_hash": subscriber.state_hash,
            "map_key": subscriber.map_key, "offline_prior": subscriber.offline_prior,
            "known_state_objects": len(subscriber.objects), "replan_count": subscriber.replan_count,
            "last_replan_reason": subscriber.last_replan_reason,
            "last_delivery_affects_route": subscriber.last_delivery_affects_route,
            "previous_route": subscriber.previous_route, "current_route": subscriber.current_route,
            "stopped_reason": subscriber.stopped_reason, "braking": subscriber.braking,
            "coordinate_frame": "source_city_m", "uses_recorded_trajectory": False,
        })

    def status(self):
        return {"scene_id": self.scene_id, "mode": self.mode, "variant": self.variant,
                "published_version": self._message["version"], "published_sequence": self._message["sequence"],
                "published_step": self._message["published_step"],
                "structure_published": self._structure_published,
                "offline_prior": self._message["offline_prior"],
                "coordinate_frame": "source_city_m",
                "planning_scope": "source_lane_graph_with_corridor_obstacles_and_local_emergency_braking",
                "traffic_signal_compliance": False,
                "per_vehicle": {key: self.vehicle_status(key) for key in self._subscribers}}
