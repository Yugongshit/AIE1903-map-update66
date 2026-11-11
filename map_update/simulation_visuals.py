"""Non-colliding visual annotations for actual exported simulation map edits.

Only the export's map_patch_log is highlighted. State-event observations are
not promoted to map edits. Panda3D and pygame are imported only when drawing.
"""
from __future__ import annotations

from math import dist, isfinite


COLORS = {"updated": (0.0, 1.0, 0.85, 1.0),
          "previous": (1.0, 0.72, 0.12, 1.0),
          "removed": (1.0, 0.22, 0.25, 1.0)}


def _xy(point):
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise ValueError("Overlay points require two finite coordinates")
    result = [float(point[0]), float(point[1])]
    if not all(isfinite(value) for value in result):
        raise ValueError("Overlay points require two finite coordinates")
    return result


def _paths(feature, offset):
    if not isinstance(feature, dict):
        raise ValueError("Patch geometry must be a feature object")
    paths = []
    for key in ("polyline", "polygon"):
        if key not in feature:
            continue
        points = [_xy(point) for point in feature[key]]
        if len(points) < 2 or not any(dist(points[0], point) > 1e-9 for point in points[1:]):
            raise ValueError("Overlay geometry must have distinct points")
        if key == "polygon" and points[0] != points[-1]:
            points.append(list(points[0]))
        paths.append([[point[0] + offset[0], point[1] + offset[1]] for point in points])
    if not paths:
        raise ValueError("Patch feature has no polyline or polygon to highlight")
    return paths


def dashed_segments(points, dash=0.75, gap=0.5):
    """Split a path into fixed metre-length dashes, continuous around corners."""
    if dash <= 0 or gap <= 0 or not isfinite(dash + gap):
        raise ValueError("Dash and gap lengths must be finite and positive")
    period, phase = dash + gap, 0.0
    segments = []
    for start, end in zip(points, points[1:]):
        length = dist(start, end)
        if length < 1e-9:
            continue
        traveled = 0.0
        while traveled < length - 1e-9:
            drawing = phase < dash - 1e-9
            remaining_phase = (dash if drawing else period) - phase
            length_here = min(remaining_phase, length - traveled)
            if drawing and length_here > 1e-9:
                fractions = (traveled / length, (traveled + length_here) / length)
                segments.append([[start[i] + f * (end[i] - start[i]) for i in range(2)] for f in fractions])
            traveled += length_here
            phase = (phase + length_here) % period
            if period - phase < 1e-9:
                phase = 0.0
    return segments


def _midpoint(points):
    target = sum(dist(a, b) for a, b in zip(points, points[1:])) / 2
    for a, b in zip(points, points[1:]):
        length = dist(a, b)
        if length >= target and length > 0:
            return [a[i] + target / length * (b[i] - a[i]) for i in range(2)]
        target -= length
    return list(points[-1])


def prepare_update_overlay(patch_log, *, source_to_runtime_offset=(0, 0), synthetic=False):
    """Create drawing instructions from source-world geometries, never the map.

``source_to_runtime_offset`` is the existing scenario metadata field
``old_origin_in_current_coordinate``. Adding it once places source geometry in
the same centered frame as the engine. Z values are deliberately ignored:
nuScenes lane polylines can store yaw in their third component.
"""
    if not isinstance(patch_log, list):
        raise ValueError("map_patch_log must be a list")
    offset = _xy(list(source_to_runtime_offset))
    items, seen = [], set()
    for index, patch in enumerate(patch_log, 1):
        if not isinstance(patch, dict):
            raise ValueError("Patch entries must be objects")
        identifier, operation = patch.get("feature_id"), patch.get("operation")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Patch feature IDs must be nonempty and unique")
        seen.add(identifier)
        if operation not in {"add", "replace", "delete"}:
            raise ValueError("Unknown map operation: " + str(operation))
        before, after = patch.get("before"), patch.get("after")
        expected = {"add": (False, True), "replace": (True, True), "delete": (True, False)}[operation]
        if (before is not None, after is not None) != expected:
            raise ValueError("Patch before/after geometry does not match its operation")
        paths = []
        if before is not None:
            role = "removed" if operation == "delete" else "previous"
            paths.extend({"role": role, "points": points, "dashed": True}
                         for points in _paths(before, offset))
        if after is not None:
            paths.extend({"role": "updated", "points": points, "dashed": False}
                         for points in _paths(after, offset))
        label = ("DEMO " if synthetic else "") + f"U{index:02d}"
        items.append({"label": label, "feature_id": identifier, "operation": operation,
                      "synthetic": bool(synthetic), "before_type": before.get("type") if before else None,
                      "after_type": after.get("type") if after else None,
                      "paths": paths, "label_position": _midpoint(paths[-1]["points"])})
    title = "MAP UPDATE OVERLAY" + (" - DEMO" if synthetic else "")
    legend = [(title, (1, 1, 1, 1))]
    if items:
        legend.extend([
            ("CYAN: updated / added", COLORS["updated"]),
            ("AMBER DASH: previous geometry", COLORS["previous"]),
            ("RED DASH: removed geometry", COLORS["removed"]),
            (f"{len(items)} changed feature(s); U labels identify edits", (1, 1, 1, 1)),
        ])
        if synthetic:
            legend.append(("Controlled demonstration; not a detected change", (1, 0.85, 0.5, 1)))
    else:
        legend.append(("NO MAP CHANGES APPLIED", (1, 1, 1, 1)))
        legend.append(("State-event observations are a separate layer", (0.8, 0.8, 0.8, 1)))
    report = {"coordinate_frame": "metadrive_centered_m",
              "source_to_runtime_offset_m": offset,
              "height_above_ground_m": 0.18,
              "overlay_only": True, "affects_physics_or_planning": False,
              "synthetic": bool(synthetic), "changed_features": len(items),
              "state_events_highlighted_as_map_edits": False,
              "legend": [text for text, _ in legend],
              "items": [{key: value for key, value in item.items() if key != "paths"} for item in items]}
    return {"items": items, "legend": legend, "report": report}


class UpdateOverlay:
    """Own annotation nodes; call close before closing the MetaDrive engine."""
    def __init__(self, prepared, world_node=None, legend_node=None):
        self.prepared = prepared
        self.world_node = world_node
        self.legend_node = legend_node
        self.visible = True

    def close(self):
        for node in (self.world_node, self.legend_node):
            if node is not None:
                node.removeNode()
        self.world_node = self.legend_node = None

    def set_visible(self, visible):
        self.visible = bool(visible)
        for node in (self.world_node, self.legend_node):
            if node is not None:
                node.show() if self.visible else node.hide()

    def draw_2d(self, renderer):
        """Draw after env.render(top_down), then refresh the unrotated viewport.

The runtime uses target_vehicle_heading_up=False. This adapter intentionally
rejects a rotated renderer rather than displaying a displaced annotation.
"""
        if not self.visible:
            return
        if renderer.target_agent_heading_up:
            raise ValueError("Update overlay requires target_vehicle_heading_up=False")
        import pygame
        canvas = renderer._frame_canvas
        surface = renderer.screen_canvas
        width, height = surface.get_size()
        vehicle = renderer.current_track_agent
        if renderer.position is not None or vehicle is not None:
            if renderer.center_on_map:
                position = [value / 2 for value in canvas.get_size()]
            else:
                camera = renderer.position if renderer.position is not None else vehicle.position
                position = canvas.pos2pix(*camera)
        else:
            position = (width / 2, height / 2)
        offset = [position[0] - width / 2, position[1] - height / 2]
        def pixel(point):
            value = canvas.pos2pix(*point)
            return [int(round(value[i] - offset[i])) for i in range(2)]
        if not pygame.font.get_init():
            pygame.font.init()
        font = pygame.font.Font(None, 24)
        for item in self.prepared["items"]:
            for path in item["paths"]:
                color = [int(value * 255) for value in COLORS[path["role"]][:3]]
                segments = dashed_segments(path["points"]) if path["dashed"] else [path["points"]]
                for segment in segments:
                    pygame.draw.lines(surface, color, False, [pixel(point) for point in segment], 5)
            label = font.render(item["label"], True, (255, 255, 255), (15, 20, 24))
            pos = pixel(item["label_position"])
            surface.blit(label, (pos[0] + 7, pos[1] - 20))
        for index, (text, rgba) in enumerate(self.prepared["legend"]):
            label = font.render(text, True, [int(v * 255) for v in rgba[:3]], (15, 20, 24))
            surface.blit(label, (12, 12 + 24 * index))
        renderer.blit()


def attach_update_overlay(env, patch_log, *, synthetic=False):
    """Return ``(handle, serializable_report)`` for the currently loaded scene.

For 2D, also call ``handle.draw_2d(env.top_down_renderer)`` after each top-down
render. A physics-only run returns the report without importing Panda3D here.
"""
    metadata = env.engine.data_manager.current_scenario["metadata"]
    prepared = prepare_update_overlay(patch_log,
                                      source_to_runtime_offset=metadata.get("old_origin_in_current_coordinate", [0, 0]),
                                      synthetic=synthetic)
    if env.engine.mode == "none":
        return UpdateOverlay(prepared), prepared["report"]

    from panda3d.core import LineSegs, TextNode, TransparencyAttrib
    world = env.engine.render.attachNewNode("map-update-overlay")
    world.setLightOff(1)
    world.setShaderOff(1)
    legend = env.engine.a2dTopLeft.attachNewNode("map-update-legend")
    try:
        for item in prepared["items"]:
            for path in item["paths"]:
                lines = LineSegs(item["label"] + " " + path["role"])
                lines.setColor(*COLORS[path["role"]])
                lines.setThickness(5 if path["dashed"] else 7)
                segments = dashed_segments(path["points"]) if path["dashed"] else [path["points"]]
                for segment in segments:
                    lines.moveTo(segment[0][0], segment[0][1], 0.18)
                    for point in segment[1:]:
                        lines.drawTo(point[0], point[1], 0.18)
                world.attachNewNode(lines.create())
            position = item["label_position"]
            text = TextNode(item["label"])
            text.setText(item["label"] + " " + item["operation"].upper())
            text.setTextColor(1, 1, 1, 1)
            text.setAlign(TextNode.ACenter)
            text.setShadow(.06, .06)
            text.setShadowColor(0, 0, 0, 1)
            node = world.attachNewNode(text)
            node.setTransparency(TransparencyAttrib.MAlpha)
            # Draw glyphs as an annotation after the 3D scene. In the offscreen
            # PBR path, a transparent card at the same depth can hide the text.
            node.setTwoSided(True)
            node.setDepthTest(False)
            node.setDepthWrite(False)
            node.setBin("fixed", 70)
            node.setLightOff(10)
            node.setShaderOff(10)
            node.setPos(position[0], position[1], 1.25)
            node.setScale(.4)
            node.setBillboardPointEye()
            pole = LineSegs("update label anchor")
            pole.setColor(1, 1, 1, 1)
            pole.setThickness(2)
            pole.moveTo(position[0], position[1], .2)
            pole.drawTo(position[0], position[1], 1.1)
            world.attachNewNode(pole.create())
        for index, (line, color) in enumerate(prepared["legend"]):
            text = TextNode("map update legend " + str(index))
            text.setText(line)
            text.setTextColor(*color)
            text.setCardColor(.03, .06, .08, .92)
            text.setCardAsMargin(.25, .35, .25, .25)
            node = legend.attachNewNode(text)
            node.setTransparency(TransparencyAttrib.MAlpha)
            node.setPos(.05, 0, -.075 - index * .06)
            node.setScale(.044 if index == 0 else .038)
        return UpdateOverlay(prepared, world, legend), prepared["report"]
    except Exception:
        world.removeNode()
        legend.removeNode()
        raise
