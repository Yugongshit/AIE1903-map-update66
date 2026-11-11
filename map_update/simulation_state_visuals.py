"""Attach source map-state observations to existing simulation instances.

These are annotations: no objects, collision shapes, map edits, or policies are
created. Source footprints stay at their observation coordinates. Identity
markers can follow an existing simulator object without claiming a new source
observation. Panda3D and pygame are imported only by drawing methods.
"""
from __future__ import annotations

from collections import Counter
from math import dist, isfinite, radians, tan

from .simulation_visuals import dashed_segments


STATE_COLORS = {"observed_active": (1., .91, .12, 1.),
                "unknown": (.78, .40, 1., 1.), "stale": (.78, .40, 1., 1.)}


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError("State annotation needs finite runtime XY")
    point = [float(v) for v in value[:2]]
    if not all(isfinite(v) for v in point):
        raise ValueError("State annotation needs finite runtime XY")
    return point


def _center(paths):
    points = [point for path in paths for point in path]
    return [(min(p[i] for p in points)+max(p[i] for p in points))/2 for i in (0, 1)]


def prepare_state_annotations(snapshot, *, bindings=None, labels=None):
    """Build deduplicated drawing instructions from a current source snapshot.

Bindings contain only live simulator object identities/positions keyed by
scenario track ID. ``runtime_paths`` is already centered: never offset it again.
"""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("objects"), list):
        raise ValueError("State annotations require a state snapshot")
    layer = snapshot.get("layer", {})
    cutoff = snapshot.get("observation_cutoff_ns")
    cutoff_value = int(cutoff) if cutoff is not None else None
    events = []
    for feature in layer.get("features", []):
        prop = feature.get("properties", {})
        identifier = prop.get("event_id", feature.get("id"))
        state = prop.get("state")
        if not isinstance(identifier, str) or not identifier or state not in STATE_COLORS:
            raise ValueError("Unsupported source state event")
        seen = prop.get("last_seen_timestamp_ns")
        if cutoff_value is not None and seen is not None and int(seen) > cutoff_value:
            raise ValueError("Future event cannot appear in a state annotation")
        events.append({"event_id": identifier, "state": state,
                       "change_type": prop.get("change_type", "state"),
                       "last_seen_timestamp_ns": seen})
    event_ids = {event["event_id"] for event in events}
    if len(event_ids) != len(events):
        raise ValueError("State event identities must be unique")
    bindings, labels = bindings or {}, labels or {}
    objects = {}
    for row in snapshot["objects"]:
        identifier = row.get("instance_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("State objects require a source instance identity")
        associated = sorted(set(row.get("event_ids", [])) & event_ids)
        # Cleared events and unrelated traffic never acquire state markers.
        if not associated:
            continue
        state = row.get("status")
        if state not in STATE_COLORS:
            raise ValueError("Unsupported source object state")
        seen = row.get("last_observed_timestamp_ns")
        if cutoff_value is not None and seen is not None and int(seen) > cutoff_value:
            raise ValueError("Future instance cannot appear in a state annotation")
        paths = [[_point(p) for p in path] for path in row.get("runtime_paths", [])]
        if not paths or any(len(path) < 2 for path in paths):
            raise ValueError("State object requires its source footprint")
        track_ids = sorted(set(row.get("matched_track_ids", [])))
        live = {bindings[key]["object_id"]: bindings[key] for key in track_ids if key in bindings}
        center = _center(paths)
        item = {"instance_id": identifier, "event_ids": associated, "status": state,
                "category": row.get("category", row.get("type", "unknown")),
                "historical": bool(row.get("historical", state != "observed_active")),
                "last_observed_timestamp_ns": seen, "runtime_paths": paths,
                "source_footprint_center_runtime_m": center,
                "anchor_runtime_m": _point(next(iter(live.values()))["position_runtime_m"]) if live else center,
                "bound_object_ids": sorted(live), "matched_track_ids": track_ids,
                "missing_model": bool(row.get("missing_model")),
                "footprint_only": not bool(live),
                "geometry_role": "source_observation_footprint_not_current_extent"}
        if identifier in objects:
            previous = objects[identifier]
            if any(previous[key] != item[key] for key in ("status", "runtime_paths", "last_observed_timestamp_ns")):
                raise ValueError("One instance has conflicting source state geometry")
            previous["event_ids"] = sorted(set(previous["event_ids"]) | set(associated))
        else:
            objects[identifier] = item
    for event in events:
        event["label"] = labels.get(event["event_id"], event["event_id"])
        event["instance_ids"] = sorted(key for key, row in objects.items() if event["event_id"] in row["event_ids"])
    counts = Counter(event["state"] for event in events)
    return {"events": events, "objects": list(objects.values()),
            "source_timestamp_ns": snapshot.get("source_timestamp_ns"),
            "observation_cutoff_ns": cutoff,
            "clock_mode": snapshot.get("clock_mode"),
            "source_snapshot_age_s": snapshot.get("source_snapshot_age_s"),
            "summary": {"events_seen": len(events), "source_instances": len(objects),
                        "observed_active": counts["observed_active"], "unknown": counts["unknown"],
                        "stale": counts["stale"], "cleared": len(layer.get("cleared_events", [])),
                        "bound_instances": sum(bool(o["bound_object_ids"]) for o in objects.values()),
                        "footprint_only_instances": sum(o["footprint_only"] for o in objects.values()),
                        "missing_model_instances": sum(o["missing_model"] for o in objects.values())}}


def _live_bindings(env, snapshot):
    manager = getattr(env.engine, "traffic_manager", None)
    mapping = getattr(manager, "scenario_id_to_obj_id", {}) or {}
    objects = env.engine.get_objects() if hasattr(env.engine, "get_objects") else {}
    tracks = env.engine.data_manager.current_scenario.get("tracks", {})
    bindings = {}
    for row in snapshot["objects"]:
        for track_id in row.get("matched_track_ids", []):
            if track_id not in tracks:
                raise ValueError("State annotation refers to a missing scenario track")
            original_id = tracks[track_id].get("metadata", {}).get("original_id", track_id)
            if original_id != row["instance_id"]:
                raise ValueError("State annotation instance differs from its scenario track")
            object_id = mapping.get(track_id)
            obj = objects.get(object_id)
            if obj is not None:
                bindings[track_id] = {"object_id": object_id,
                                      "position_runtime_m": [float(v) for v in obj.position[:2]]}
    return bindings


def state_label_groups(prepared, selected_event_id=None):
    """One label per event, keeping every individual footprint and binding.

The selected event gets one aggregate label. Unselected missing-model instances
are grouped under one of their observed event identities, so adjacent debris
does not produce several overlapping copies of ``FOOTPRINT ONLY``.
"""
    events = {event["event_id"]: event for event in prepared["events"]}
    grouped = {}
    for row in prepared["objects"]:
        if selected_event_id in row["event_ids"]:
            key = selected_event_id
        elif row["missing_model"]:
            key = row["event_ids"][0]
        else:
            continue
        grouped.setdefault(key, []).append(row)
    groups = []
    order = sorted(grouped, key=lambda key: (key != selected_event_id, events[key]["label"]))
    for key in order:
        rows, event = grouped[key], events[key]
        missing_only = all(row["missing_model"] for row in rows)
        status = event["state"] if key == selected_event_id else (
            "observed_active" if all(row["status"] == "observed_active" for row in rows) else "unknown")
        if missing_only:
            categories = {str(row["category"]).split(".")[-1] for row in rows}
            category = next(iter(categories)) if len(categories) == 1 else "source"
            text = "{}: {} {} footprint{}".format(event["label"], len(rows), category, "" if len(rows) == 1 else "s")
        else:
            text = "{} {} | {} source objects".format(event["label"], status.upper(), len(rows))
        groups.append({"event_id": key, "label": text, "status": status,
                       "anchor_runtime_m": _center([[row["anchor_runtime_m"] for row in rows]]),
                       "instance_ids": [row["instance_id"] for row in rows],
                       "missing_model_only": missing_only})
    return groups


class StateOverlay:
    def __init__(self, env):
        self.env = env
        self.world_node = self.panel_node = self.key_listener = None
        self.visible = True
        self.labels = {}
        self.selected_event_id = None
        self.prepared = {"events": [], "objects": [], "summary": {}}
        self.report = {"schema": "simulation-state-annotations-1.0", "overlay_only": True,
                       "objects_spawned": 0, "affects_physics_or_planning": False,
                       "footprint_coordinates": "metadrive_centered_m; no further translation",
                       "keys": {"N": "previous state", "M": "next state", "I": "focus state",
                                "V": "toggle state annotations only", "Q": "follow car"}}
        self._keys_down = set()

    def refresh(self, snapshot):
        prepared = prepare_state_annotations(snapshot, bindings=_live_bindings(self.env, snapshot))
        for event in prepared["events"]:
            if event["event_id"] not in self.labels:
                self.labels[event["event_id"]] = "S{:02d}".format(len(self.labels)+1)
            event["label"] = self.labels[event["event_id"]]
        ids = [event["event_id"] for event in prepared["events"]]
        if self.selected_event_id not in ids:
            self.selected_event_id = ids[0] if ids else None
        self.prepared = prepared
        self._update_report()
        if self.world_node is not None:
            self._draw_world()
            self._draw_panel()

    def _update_report(self):
        self.report.update(self.prepared)
        self.report.update(selected_event_id=self.selected_event_id, annotations_visible=self.visible)
        selected = self.selected()
        self.report["selected_event_last_seen_timestamp_ns"] = selected["last_seen_timestamp_ns"] if selected else None
        self.report["selected_event_last_seen_age_s"] = self._selected_age()
        self.report["label_groups"] = state_label_groups(self.prepared, self.selected_event_id)

    def selected(self):
        return next((event for event in self.prepared["events"] if event["event_id"] == self.selected_event_id), None)

    def selected_objects(self):
        return [row for row in self.prepared["objects"] if self.selected_event_id in row["event_ids"]]

    def _selected_age(self):
        selected = self.selected()
        now = self.prepared.get("source_timestamp_ns")
        seen = selected.get("last_seen_timestamp_ns") if selected else None
        return (int(now)-int(seen))/1_000_000_000 if now is not None and seen is not None else None

    def select_state(self, delta):
        ids = [event["event_id"] for event in self.prepared["events"]]
        if not ids:
            return
        self.selected_event_id = ids[(ids.index(self.selected_event_id)+int(delta)) % len(ids)]
        self._update_report()
        if self.world_node is not None:
            self._draw_world()
            self._draw_panel()

    def set_visible(self, visible):
        self.visible = bool(visible)
        if self.world_node is not None:
            self.world_node.show() if self.visible else self.world_node.hide()
            self._draw_panel()
        self._update_report()

    def toggle_visible(self):
        self.set_visible(not self.visible)

    def focus_selected(self):
        rows = self.selected_objects()
        camera = getattr(self.env, "main_camera", None)
        if not rows or camera is None:
            return False
        # Focus source footprints, including historical ones; no object is moved.
        points = [p for row in rows for path in row["runtime_paths"] for p in path]
        low = [min(p[i] for p in points)-3 for i in (0, 1)]
        high = [max(p[i] for p in points)+3 for i in (0, 1)]
        x, y = [(low[i]+high[i])/2 for i in (0, 1)]
        hfov, vfov = self.env.engine.cam.node().getLens().getFov()
        height = max(12., 1.3*(high[0]-low[0])/(2*tan(radians(hfov)/2)),
                     1.3*(high[1]-low[1])/(2*tan(radians(vfov)/2)))
        camera.stop_track(bird_view_on_current_position=False)
        camera.set_bird_view_pos([x, y, height])
        camera.camera.setPos(x, y, height)
        camera.camera.lookAt(x, y, 0)
        self.report["last_focus_runtime_m"] = [x, y, height]
        return True

    def _panel_lines(self):
        summary = self.prepared["summary"]
        age = self.prepared.get("source_snapshot_age_s")
        selected, rows = self.selected(), self.selected_objects()
        categories = Counter(str(row["category"]).split(".")[-1] for row in rows)
        status = selected["state"] if selected else "unknown"
        category_text = ", ".join("{} {}".format(name, count) for name, count in sorted(categories.items()))
        distance = None
        if rows and getattr(self.env, "agent", None) is not None:
            distance = dist(list(self.env.agent.position)[:2], _center([p for row in rows for p in row["runtime_paths"]]))
        bound = sum(bool(row["bound_object_ids"]) for row in rows)
        white = (.88, .93, .98, 1)
        clock_label = ("Past recording; no new observations" if self.prepared.get("clock_mode") == "outside_recorded_window"
                       else "Frame snapshot age: {:.3f} s".format(age) if age is not None
                       else "No source observation reached")
        event_age = self._selected_age()
        event_age_label = ("Event last seen: {:.1f} s ago".format(event_age) if event_age is not None
                           else "Event last seen: age unknown")
        return [("ATTACHED MAP STATES", white),
                (clock_label, white),
                ("Events: {} | source instances: {}".format(summary["events_seen"], summary["source_instances"]), white),
                ("Observed {} | unknown {} | stale {} | cleared {}".format(summary["observed_active"], summary["unknown"], summary["stale"], summary["cleared"]), white),
                (("{} {} | {}".format(selected["label"], str(selected["change_type"]).upper(), status.upper())) if selected else "NO OBSERVED STATE EVENTS", STATE_COLORS[status]),
                (event_age_label + (" | " + category_text if category_text else ""), white),
                ("Bound {} | footprint only {}".format(bound, len(rows)-bound)+( " | {:.1f} m".format(distance) if distance is not None else ""), white),
                ("N/M: state  I: focus  Q: follow car", white),
                ("V: {} state annotations [{}]".format("hide" if self.visible else "show", "ON" if self.visible else "OFF"), white),
                ("Yellow: observed / Purple: unverified", (.85, .68, 1., 1)),
                ("Historical footprints; no new observation" if status != "observed_active" else "Source footprints; no duplicate objects", white)]

    def _draw_panel(self):
        from panda3d.core import CardMaker, TextNode, TransparencyAttrib
        for child in list(self.panel_node.getChildren()):
            child.removeNode()
        card = CardMaker("state summary background")
        card.setFrame(0, .97, -.56, 0)
        node = self.panel_node.attachNewNode(card.generate())
        node.setColor(.025, .04, .06, .96)
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setBin("fixed", 100)
        for index, (line, color) in enumerate(self._panel_lines()):
            text = TextNode("state summary " + str(index))
            text.setText(line[:65])
            text.setTextColor(*color)
            node = self.panel_node.attachNewNode(text)
            node.setPos(.025, 0, -.047-index*.047)
            node.setScale(.036 if index == 0 else .028)
            node.setBin("fixed", 102)

    def _draw_world(self):
        from panda3d.core import LineSegs, TextNode
        for child in list(self.world_node.getChildren()):
            child.removeNode()
        for row in self.prepared["objects"]:
            chosen = self.selected_event_id in row["event_ids"]
            lines = LineSegs("state footprint " + row["instance_id"])
            lines.setColor(*STATE_COLORS[row["status"]])
            lines.setThickness(4 if chosen else 2)
            for path in row["runtime_paths"]:
                paths = [path] if row["status"] == "observed_active" else dashed_segments(path, dash=.35, gap=.20)
                for segment in paths:
                    lines.moveTo(segment[0][0], segment[0][1], .22)
                    for point in segment[1:]:
                        lines.drawTo(point[0], point[1], .22)
            anchor = row["anchor_runtime_m"]
            if row["bound_object_ids"]:
                # A small identity mark tracks the existing simulator instance;
                # source footprint geometry above never moves with this mark.
                lines.moveTo(anchor[0]-.18, anchor[1], .26)
                lines.drawTo(anchor[0]+.18, anchor[1], .26)
                lines.moveTo(anchor[0], anchor[1]-.18, .26)
                lines.drawTo(anchor[0], anchor[1]+.18, .26)
            self.world_node.attachNewNode(lines.create())
        for index, group in enumerate(state_label_groups(self.prepared, self.selected_event_id)):
            anchor = group["anchor_runtime_m"]
            # Separate the few event-level labels vertically as well as merging
            # adjacent instances. This offset belongs only to the annotation.
            height = 1.45 + index*.65
            text = TextNode("source event state")
            text.setText(group["label"])
            text.setTextColor(*STATE_COLORS[group["status"]])
            text.setAlign(TextNode.ACenter)
            text.setShadow(.06, .06)
            text.setShadowColor(0, 0, 0, 1)
            node = self.world_node.attachNewNode(text)
            node.setPos(anchor[0], anchor[1], height)
            node.setScale(.24)
            node.setBillboardPointEye()
            node.setTwoSided(True)
            node.setDepthTest(False)
            node.setDepthWrite(False)
            node.setBin("fixed", 69)
            stem = LineSegs("state event label anchor")
            stem.setColor(*STATE_COLORS[group["status"]])
            stem.setThickness(1)
            stem.moveTo(anchor[0], anchor[1], .24)
            stem.drawTo(anchor[0], anchor[1], height-.15)
            self.world_node.attachNewNode(stem.create())
        self.world_node.show() if self.visible else self.world_node.hide()

    def draw_2d(self, renderer):
        if renderer.target_agent_heading_up:
            raise ValueError("State annotations require an unrotated top-down view")
        import pygame
        if not pygame.font.get_init():
            pygame.font.init()
        if not renderer.no_window:
            keys = pygame.key.get_pressed()
            pressed = {key for key in (pygame.K_n, pygame.K_m, pygame.K_v) if keys[key]}
            for key, delta in ((pygame.K_n, -1), (pygame.K_m, 1)):
                if key in pressed and key not in self._keys_down:
                    self.select_state(delta)
            if pygame.K_v in pressed and pygame.K_v not in self._keys_down:
                self.toggle_visible()
            self._keys_down = pressed
        surface, canvas = renderer.screen_canvas, renderer._frame_canvas
        width, height = surface.get_size()
        vehicle = renderer.current_track_agent
        if renderer.position is not None or vehicle is not None:
            if renderer.center_on_map:
                center = [v/2 for v in canvas.get_size()]
            else:
                position = renderer.position if renderer.position is not None else vehicle.position
                center = canvas.pos2pix(*position)
        else:
            center = [width/2, height/2]
        offset = [center[0]-width/2, center[1]-height/2]
        def pixel(point):
            value = canvas.pos2pix(*point)
            return [round(value[i]-offset[i]) for i in (0, 1)]
        if self.visible:
            for row in self.prepared["objects"]:
                color = tuple(round(v*255) for v in STATE_COLORS[row["status"]][:3])
                for path in row["runtime_paths"]:
                    segments = [path] if row["status"] == "observed_active" else dashed_segments(path, dash=.35, gap=.20)
                    for segment in segments:
                        pygame.draw.lines(surface, color, False, [pixel(p) for p in segment], 4 if self.selected_event_id in row["event_ids"] else 2)
        pygame.draw.rect(surface, (7, 11, 16), (12, 43, 430, 266), border_radius=8)
        font, title = pygame.font.Font(None, 21), pygame.font.Font(None, 24)
        for index, (line, color) in enumerate(self._panel_lines()):
            text = (title if index == 0 else font).render(line[:65], True, tuple(round(v*255) for v in color[:3]))
            surface.blit(text, (24, 55+23*index))
        renderer.blit()

    def close(self):
        if self.key_listener is not None:
            self.key_listener.ignoreAll()
            self.key_listener = None
        for node in (self.world_node, self.panel_node):
            if node is not None:
                node.removeNode()
        self.world_node = self.panel_node = None
        self.env = None


def attach_state_overlay(env, snapshot):
    """Return an annotation owner and its live, serializable audit report."""
    handle = StateOverlay(env)
    handle.refresh(snapshot)
    if env.engine.mode == "none":
        return handle, handle.report
    try:
        handle.world_node = env.engine.render.attachNewNode("attached-map-state-annotations")
        handle.world_node.setLightOff(1)
        handle.world_node.setShaderOff(1)
        handle.panel_node = env.engine.a2dTopLeft.attachNewNode("attached-map-state-summary")
        handle.panel_node.setPos(.035, 0, -.10)
        handle.panel_node.setLightOff(1)
        handle.panel_node.setShaderOff(1)
        handle.panel_node.setDepthTest(False)
        handle.panel_node.setDepthWrite(False)
        from direct.showbase.DirectObject import DirectObject
        handle.key_listener = DirectObject()
        handle.key_listener.accept("n", handle.select_state, [-1])
        handle.key_listener.accept("m", handle.select_state, [1])
        handle.key_listener.accept("i", handle.focus_selected)
        handle.key_listener.accept("v", handle.toggle_visible)
        handle._draw_world()
        handle._draw_panel()
        return handle, handle.report
    except Exception:
        handle.close()
        raise
