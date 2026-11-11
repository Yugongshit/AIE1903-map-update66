"""Non-colliding visual annotations for actual exported simulation map edits.

Only the export's map_patch_log is highlighted. State-event observations are
not promoted to map edits. Panda3D and pygame are imported only when drawing.
"""
from __future__ import annotations

from math import dist, isfinite, radians, tan


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


def prepare_update_overlay(patch_log, *, source_to_runtime_offset=(0, 0), synthetic=False,
                           update_source=None, reference_operations=None):
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
              "update_source": update_source, "reference_operations": reference_operations,
              "state_events_highlighted_as_map_edits": False,
              "legend": [text for text, _ in legend],
              "items": [{key: value for key, value in item.items() if key != "paths"} for item in items]}
    return {"items": items, "legend": legend, "report": report}


class UpdateOverlay:
    """Own annotation nodes; call close before closing the MetaDrive engine."""
    def __init__(self, prepared, world_node=None, legend_node=None, *, env=None, patch_log=None,
                 label_nodes=None):
        self.prepared = prepared
        self.world_node = world_node
        self.legend_node = legend_node
        self.env = env
        self.patch_log = patch_log
        self.label_nodes = label_nodes or {}
        self.selected_index = 0
        self.key_listener = None
        self.distance_text = None
        self.distance_m = None
        self._keys_down = set()
        self.visible = True
        self._prepare_comparison()

    def _prepare_comparison(self):
        from .simulation_comparison import prepare_comparison
        self.comparison = prepare_comparison(self.prepared, self.selected_index,
                                             patch_log=self.patch_log, panel_size=(460, 300))
        self.prepared["report"]["comparison_ui"] = {
            "persistent_local_view": True, "same_viewport_before_after": True,
            "selected_index": self.selected_index,
            "selected_feature_id": self.comparison.get("feature_id"),
            "viewport": self.comparison.get("viewport"),
            "translation": self.comparison.get("translation"),
            "attribute_changes": self.comparison.get("attribute_changes", []),
            "summary_lines": self.comparison.get("summary_lines", []),
            "keys": {"J": "previous edit", "K": "next edit", "U": "focus edit in 3D", "Q": "follow car"},
            "distance_to_edit_m": self.distance_m,
            "world_labels": "selected_edit_only; all_changed_geometry_remains_visible",
        }
        for feature_id, node in self.label_nodes.items():
            node.show() if feature_id == self.comparison.get('feature_id') else node.hide()

    def select_change(self, delta):
        """Change the comparison target without moving the car or its camera."""
        count = len(self.prepared["items"])
        if not count:
            return
        self.selected_index = (self.selected_index + int(delta)) % count
        self._prepare_comparison()
        if self.legend_node is not None:
            self._draw_comparison_3d()
        self.refresh()

    def focus_selected(self):
        """Use MetaDrive's existing bird-view camera; leave replay untouched."""
        if self.comparison["empty"] or self.env is None:
            return False
        camera = getattr(self.env, "main_camera", None)
        if camera is None:
            return False
        viewport = self.comparison["viewport"]
        xmin, ymin, xmax, ymax = viewport["bounds_xyxy"]
        # MainCamera itself obtains its lens through engine.cam.node().getLens().
        horizontal, vertical = self.env.engine.cam.node().getLens().getFov()
        height = max(12.0, 1.3 * (xmax-xmin) / (2*tan(radians(horizontal)/2)),
                     1.3 * (ymax-ymin) / (2*tan(radians(vertical)/2)))
        x, y = viewport["center_xy"]
        camera.stop_track(bird_view_on_current_position=False)
        camera.set_bird_view_pos([x, y, height])
        # Apply immediately as well as through the camera's next task update,
        # so a screenshot taken directly after U shows the requested framing.
        camera.camera.setPos(x, y, height)
        camera.camera.lookAt(x, y, 0)
        self.prepared["report"]["comparison_ui"]["last_focus_runtime_m"] = [x, y, height]
        return True

    def refresh(self):
        """Update only the distance text; the local comparison stays fixed."""
        if self.comparison["empty"] or self.env is None:
            return
        agent = getattr(self.env, "agent", None)
        if agent is None:
            return
        self.distance_m = dist(list(agent.position)[:2], self.comparison["viewport"]["center_xy"])
        self.prepared["report"]["comparison_ui"]["distance_to_edit_m"] = self.distance_m
        if self.distance_text is not None:
            self.distance_text.setText(self._distance_label())

    def _distance_label(self):
        return "Car to edit: {:.1f} m | U: focus".format(self.distance_m) if self.distance_m is not None else "U: focus this edit in 3D"

    def _summary(self):
        translation = self.comparison.get("translation", {})
        if translation.get("kind") == "uniform_translation":
            return "Shift: dx {:+.2f} m / dy {:+.2f} m".format(translation["dx_m"], translation["dy_m"])
        lines = self.comparison.get("summary_lines", [])
        if translation.get("kind") == "unchanged":
            changes = self.comparison.get("attribute_changes", [])
            if changes:
                # The model already abbreviates topology changes as counts;
                # complete before/after IDs remain available in the report.
                return lines[1] + (" (+{} more)".format(len(changes)-1) if len(changes) > 1 else "")
            return "No geometry or supported attribute difference"
        return lines[0] if lines else self.comparison.get("operation", "").upper()

    def _source_label(self):
        report = self.prepared['report']
        if report['synthetic']:
            return 'DEMO: controlled edit, not a detected change'
        if report.get('update_source') == 'reference_update':
            count = report.get('reference_operations')
            return '{} reference updates applied'.format(count) if count is not None else 'SOURCE: delivered reference updates'
        return 'Applied map edit; replay continues'

    def _draw_comparison_3d(self):
        """Draw a fixed, shared-scale before/after card at the top right."""
        from panda3d.core import CardMaker, LineSegs, TextNode, TransparencyAttrib
        root = self.legend_node
        for child in list(root.getChildren()):
            child.removeNode()
        self.distance_text = None

        def card(name, left, right, bottom, top, color):
            maker = CardMaker(name)
            maker.setFrame(left, right, bottom, top)
            node = root.attachNewNode(maker.generate())
            node.setColor(*color)
            node.setTransparency(TransparencyAttrib.MAlpha)
            node.setBin("fixed", 100)

        def text(name, value, x, z, size=.033, color=(.88, .92, .96, 1)):
            label = TextNode(name)
            label.setText(value)
            label.setTextColor(*color)
            node = root.attachNewNode(label)
            node.setPos(x, 0, z)
            node.setScale(size)
            node.setBin("fixed", 102)
            return label

        model = self.comparison
        card("comparison background", 0, 1.08, -.23 if model['empty'] else -.81, 0, (.025, .04, .06, .96))
        text("comparison title", "MAP CHANGE" + ("  /  DEMO" if model.get("synthetic") else ""), .035, -.055, .041)
        if model["empty"]:
            text("no map changes", "NO MAP CHANGES APPLIED", .035, -.12)
            text("no fabricated differences", "No before/after difference to display.", .035, -.18, .029)
            return
        text("selected map change", "{}  |  {}  |  {}/{}".format(
            model["label"], model["operation"].upper(), self.selected_index+1, len(self.prepared["items"])), .035, -.108, .033)
        text("change summary", self._summary()[:62], .035, -.154, .030)
        self.distance_text = text("distance to selected change", self._distance_label(), .035, -.198, .029)
        xmin, ymin, xmax, ymax = model["viewport"]["bounds_xyxy"]
        plot_width, plot_height, bottom = .46, .30, -.565
        for side_index, side in enumerate(("before", "after")):
            left = .035 + side_index*.55
            color = COLORS["removed"] if model["operation"] == "delete" and side == "before" else COLORS["previous" if side == "before" else "updated"]
            text(side + " title", side.upper(), left, -.245, .034, color)
            card(side + " plot", left, left+plot_width, bottom, bottom+plot_height, (.065, .085, .11, 1))
            grid = LineSegs(side + " reference grid")
            grid.setColor(.20, .25, .30, 1)
            grid.setThickness(1)
            for fraction in (.25, .5, .75):
                x, z = left+plot_width*fraction, bottom+plot_height*fraction
                grid.moveTo(x, 0, bottom); grid.drawTo(x, 0, bottom+plot_height)
                grid.moveTo(left, 0, z); grid.drawTo(left+plot_width, 0, z)
            root.attachNewNode(grid.create()).setBin("fixed", 101)
            def point(value):
                return left+(value[0]-xmin)/(xmax-xmin)*plot_width, 0, bottom+(value[1]-ymin)/(ymax-ymin)*plot_height
            lines = LineSegs(side + " geometry")
            lines.setColor(*color)
            lines.setThickness(4)
            for path in model[side]["paths"]:
                paths = dashed_segments(path["points"], dash=(xmax-xmin)/22, gap=(xmax-xmin)/40) if path["dashed"] else [path["points"]]
                for segment in paths:
                    lines.moveTo(*point(segment[0]))
                    for value in segment[1:]:
                        lines.drawTo(*point(value))
            root.attachNewNode(lines.create()).setBin("fixed", 101)
            if not model[side]["present"]:
                text(side + " missing", model[side]["missing_label"], left+.025, bottom+.135, .029)
            scale = model["viewport"]["scale_bar_m"]
            length = scale/(xmax-xmin)*plot_width
            bar = LineSegs(side + " scale")
            bar.setColor(.88, .92, .96, 1)
            bar.setThickness(2)
            bar.moveTo(left, 0, -.600); bar.drawTo(left+length, 0, -.600)
            root.attachNewNode(bar.create()).setBin("fixed", 101)
            text(side + " scale label", "{:g} m".format(scale), left+length+.018, -.610, .027)
        text("same view explanation", "Same position / scale  |  X right, Y up", .035, -.655, .028)
        text("comparison selection keys", "J / K: select edit    U: focus    Q: follow car", .035, -.701, .028)
        text("comparison status", self._source_label(), .035, -.752, .028,
             (1, .79, .37, 1) if model.get("synthetic") else (.7, .78, .84, 1))

    def close(self):
        if self.key_listener is not None:
            self.key_listener.ignoreAll()
            self.key_listener = None
        for node in (self.world_node, self.legend_node):
            if node is not None:
                node.removeNode()
        self.world_node = self.legend_node = None
        self.label_nodes = {}
        self.env = None

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
        if not renderer.no_window:
            keys = pygame.key.get_pressed()
            pressed = {key for key in (pygame.K_j, pygame.K_k) if keys[key]}
            for key, delta in ((pygame.K_j, -1), (pygame.K_k, 1)):
                if key in pressed and key not in self._keys_down:
                    self.select_change(delta)
            self._keys_down = pressed
        self.refresh()
        font = pygame.font.Font(None, 24)
        for item in self.prepared["items"]:
            for path in item["paths"]:
                color = [int(value * 255) for value in COLORS[path["role"]][:3]]
                segments = dashed_segments(path["points"]) if path["dashed"] else [path["points"]]
                for segment in segments:
                    pygame.draw.lines(surface, color, False, [pixel(point) for point in segment], 3)
            if item['feature_id'] == self.comparison.get('feature_id'):
                label = font.render(item["label"], True, (255, 255, 255), (15, 20, 24))
                pos = pixel(item["label_position"])
                surface.blit(label, (pos[0] + 7, pos[1] - 20))
        self._draw_comparison_2d(surface)
        renderer.blit()

    def _draw_comparison_2d(self, surface):
        import pygame
        model = self.comparison
        left, top = max(12, surface.get_width()-478), 12
        pygame.draw.rect(surface, (7, 11, 16), (left, top, 466, 70 if model['empty'] else 316), border_radius=8)
        font, small = pygame.font.Font(None, 24), pygame.font.Font(None, 20)
        def text(value, x, y, color=(225, 235, 245), compact=False):
            surface.blit((small if compact else font).render(value, True, color), (left+x, top+y))
        text("MAP CHANGE" + (" / DEMO" if model.get("synthetic") else ""), 12, 10)
        if model["empty"]:
            text("NO MAP CHANGES APPLIED", 12, 40)
            return
        text("{} | {} | {}/{}".format(model["label"], model["operation"].upper(),
                                     self.selected_index+1, len(self.prepared["items"])), 12, 35)
        text(self._summary()[:58], 12, 60, compact=True)
        xmin, ymin, xmax, ymax = model["viewport"]["bounds_xyxy"]
        plot_w, plot_h, plot_y = 200, 130, 110
        for side_index, side in enumerate(("before", "after")):
            plot_x = 12 + side_index*232
            rgba = COLORS["removed"] if model["operation"] == "delete" and side == "before" else COLORS["previous" if side == "before" else "updated"]
            color = tuple(round(x*255) for x in rgba[:3])
            text(side.upper(), plot_x, 86, color)
            rectangle = (left+plot_x, top+plot_y, plot_w, plot_h)
            pygame.draw.rect(surface, (17, 22, 29), rectangle)
            for fraction in (.25, .5, .75):
                x, y = left+plot_x+int(plot_w*fraction), top+plot_y+int(plot_h*fraction)
                pygame.draw.line(surface, (51, 64, 77), (x, top+plot_y), (x, top+plot_y+plot_h))
                pygame.draw.line(surface, (51, 64, 77), (left+plot_x, y), (left+plot_x+plot_w, y))
            def point(value):
                return [round(left+plot_x+(value[0]-xmin)/(xmax-xmin)*plot_w),
                        round(top+plot_y+plot_h-(value[1]-ymin)/(ymax-ymin)*plot_h)]
            for path in model[side]["paths"]:
                segments = dashed_segments(path["points"], dash=(xmax-xmin)/22, gap=(xmax-xmin)/40) if path["dashed"] else [path["points"]]
                for segment in segments:
                    pygame.draw.lines(surface, color, False, [point(p) for p in segment], 3)
            if not model[side]["present"]:
                text(model[side]["missing_label"], plot_x+10, plot_y+55, compact=True)
            scale = model["viewport"]["scale_bar_m"]
            length = round(scale/(xmax-xmin)*plot_w)
            pygame.draw.line(surface, (225, 235, 245), (left+plot_x, top+plot_y+10),
                             (left+plot_x+length, top+plot_y+10), 2)
            text("{:g} m".format(scale), plot_x+length+5, plot_y+1, compact=True)
        distance = " | Car: {:.1f} m away".format(self.distance_m) if self.distance_m is not None else ""
        text("Same position / scale"+distance, 12, 248, compact=True)
        text("J / K: select edit  |  U / Q: focus / follow in 3D", 12, 270, compact=True)
        text(self._source_label(), 12, 294, compact=True)


def attach_update_overlay(env, patch_log, *, synthetic=False, update_source=None, reference_operations=None):
    """Return ``(handle, serializable_report)`` for the currently loaded scene.

For 2D, also call ``handle.draw_2d(env.top_down_renderer)`` after each top-down
render. A physics-only run returns the report without importing Panda3D here.
"""
    metadata = env.engine.data_manager.current_scenario["metadata"]
    prepared = prepare_update_overlay(patch_log,
                                      source_to_runtime_offset=metadata.get("old_origin_in_current_coordinate", [0, 0]),
                                      synthetic=synthetic, update_source=update_source,
                                      reference_operations=reference_operations)
    if env.engine.mode == "none":
        return UpdateOverlay(prepared, env=env, patch_log=patch_log), prepared["report"]

    from panda3d.core import LineSegs, TextNode, TransparencyAttrib
    world = env.engine.render.attachNewNode("map-update-overlay")
    world.setLightOff(1)
    world.setShaderOff(1)
    legend = env.engine.a2dTopRight.attachNewNode("map-update-comparison")
    legend.setPos(-1.115, 0, -.035)
    legend.setLightOff(1)
    legend.setShaderOff(1)
    legend.setDepthTest(False)
    legend.setDepthWrite(False)
    label_nodes = {}
    try:
        for item in prepared["items"]:
            for path in item["paths"]:
                lines = LineSegs(item["label"] + " " + path["role"])
                lines.setColor(*COLORS[path["role"]])
                lines.setThickness(2 if path["dashed"] else 3)
                segments = dashed_segments(path["points"]) if path["dashed"] else [path["points"]]
                for segment in segments:
                    lines.moveTo(segment[0][0], segment[0][1], 0.18)
                    for point in segment[1:]:
                        lines.drawTo(point[0], point[1], 0.18)
                world.attachNewNode(lines.create())
            position = item["label_position"]
            label_root = world.attachNewNode('selected-update-' + item['feature_id'])
            label_nodes[item['feature_id']] = label_root
            text = TextNode(item["label"])
            text.setText(item["label"] + " " + item["operation"].upper())
            text.setTextColor(1, 1, 1, 1)
            text.setAlign(TextNode.ACenter)
            text.setShadow(.06, .06)
            text.setShadowColor(0, 0, 0, 1)
            node = label_root.attachNewNode(text)
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
            label_root.attachNewNode(pole.create())
        handle = UpdateOverlay(prepared, world, legend, env=env, patch_log=patch_log,
                               label_nodes=label_nodes)
        handle._draw_comparison_3d()
        from direct.showbase.DirectObject import DirectObject
        handle.key_listener = DirectObject()
        handle.key_listener.accept("j", handle.select_change, [-1])
        handle.key_listener.accept("k", handle.select_change, [1])
        handle.key_listener.accept("u", handle.focus_selected)
        handle.refresh()
        return handle, prepared["report"]
    except Exception:
        world.removeNode()
        legend.removeNode()
        raise
