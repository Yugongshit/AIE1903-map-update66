"""Render supplied environment geometry without inventing scenery.

The nuScenes map polygons are XY surfaces. Their display Z is an explicitly
reported simulation-ground convention, not surveyed elevation. In particular,
the third component of nuScenes lane centerlines is heading, never terrain Z.
Panda3D is imported only when attaching meshes to a running engine.
"""
from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path


SURFACES = {
    "LANE_SURFACE_STREET": (0.30, 0.32, 0.35, 1.0),
    "LANE_SURFACE_UNSTRUCTURE": (0.30, 0.32, 0.35, 1.0),
    "ROAD_EDGE_SIDEWALK": (0.62, 0.61, 0.58, 1.0),
    "CROSSWALK": (0.83, 0.83, 0.80, 1.0),
}
DISPLAY_OFFSETS = {"CROSSWALK": 0.04, "ROAD_EDGE_SIDEWALK": 0.03}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be finite")
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a nonempty string")
    return value


def _read_extra(extra):
    if isinstance(extra, (str, Path)):
        def invalid(value):
            raise ValueError("Nonfinite environment JSON: " + value)
        return json.loads(Path(extra).read_text(encoding="utf-8"), parse_constant=invalid)
    return extra


def supplement_source_lane_polygons(map_features, *, raw_map_path, map_name,
                                    origin_in_scenario=(0, 0), edited_ids=()):
    """Recover converter-omitted surfaces by exact tokens from the same city map.

The returned copy is only for drawing. Edited features are excluded so an old
source polygon can never overwrite a newly supplied lane design.
"""
    path = Path(raw_map_path)
    if path.name != map_name + ".json":
        raise ValueError("Raw map filename does not match the scenario city")
    payload = path.read_bytes()
    raw = json.loads(payload)
    nodes = {row["token"]: row for row in raw.get("node", [])}
    polygons = {row["token"]: row for row in raw.get("polygon", [])}
    lanes = {row["token"]: row for row in raw.get("lane_connector", [])}
    result = dict(map_features)
    edited_ids = set(edited_ids)
    supplemented, skipped = [], []
    for feature_id, feature in map_features.items():
        if feature.get("type") != "LANE_SURFACE_UNSTRUCTURE" or "polygon" in feature:
            continue
        if feature_id in edited_ids:
            skipped.append({"feature_id": str(feature_id), "reason": "feature_modified_by_patch"})
            continue
        lane = lanes.get(feature_id)
        if lane is None:
            continue
        polygon = polygons.get(lane.get("polygon_token"))
        if polygon is None:
            skipped.append({"feature_id": str(feature_id), "reason": "missing_raw_polygon_token"})
            continue
        # Preserve holes in a separate drawing-only field; never alter map_data.
        token_rings = [polygon.get("exterior_node_tokens", [])]
        token_rings.extend(hole.get("node_tokens", []) for hole in polygon.get("holes", []))
        rings = []
        try:
            for tokens in token_rings:
                ring = [[float(nodes[token]["x"])-origin_in_scenario[0],
                         float(nodes[token]["y"])-origin_in_scenario[1]] for token in tokens]
                if ring and ring[-1] != ring[0]:
                    ring.append(list(ring[0]))
                rings.append(ring)
            _polygon(rings, dimensions=2)
        except (KeyError, ValueError, TypeError) as error:
            skipped.append({"feature_id": str(feature_id), "reason": "invalid_raw_polygon: " + str(error)})
            continue
        result[feature_id] = {**feature, "polygon": rings[0], "_source_polygon_holes": rings[1:]}
        supplemented.append(str(feature_id))
    return result, {"source_path": str(path.resolve()), "source_sha256": sha256(payload).hexdigest(),
                    "city": map_name, "match_rule": "exact_lane_connector_token_same_city",
                    "supplemented_count": len(supplemented), "supplemented_feature_ids": supplemented,
                    "skipped": skipped, "simulation_map_modified": False}


def _polygon(rings, *, dimensions=None):
    """Validate a closed polygon including holes; never repair its geometry."""
    from shapely.geometry import Polygon
    from shapely.validation import explain_validity

    if not isinstance(rings, (list, tuple)) or not rings:
        raise ValueError("Polygon requires coordinate rings")
    cleaned, dimension = [], None
    for ring in rings:
        if not isinstance(ring, (list, tuple)) or len(ring) < 4:
            raise ValueError("Polygon rings require at least four closed vertices")
        points = []
        for point in ring:
            if not isinstance(point, (list, tuple)) or len(point) not in (2, 3):
                raise ValueError("Polygon vertices require XY or XYZ")
            dimension = len(point) if dimension is None else dimension
            if len(point) != dimension or dimensions is not None and dimension != dimensions:
                raise ValueError("Polygon coordinate dimensions do not match")
            points.append([_number(v, "coordinate") for v in point])
        if points[0] != points[-1]:
            raise ValueError("Polygon rings must be closed")
        cleaned.append(points)
    polygon = Polygon([p[:2] for p in cleaned[0]], [[p[:2] for p in ring] for ring in cleaned[1:]])
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 1e-10:
        raise ValueError("Invalid polygon: " + explain_validity(polygon))
    return cleaned, polygon


def _triangles(rings, polygon):
    """Triangulate concave surfaces while excluding exterior space and holes."""
    try:
        from shapely import constrained_delaunay_triangles
    except ImportError as error:
        raise RuntimeError("Source geometry rendering requires Shapely 2.1+") from error

    heights = {}
    for ring in rings:
        for x, y, z in ring:
            previous = heights.setdefault((x, y), z)
            if previous != z:
                raise ValueError("A surface cannot have multiple heights at one XY vertex")
    pieces = list(constrained_delaunay_triangles(polygon).geoms)
    if any(not polygon.covers(piece) for piece in pieces):
        raise ValueError("Polygon triangulation extends outside the supplied surface")
    if not math.isclose(sum(p.area for p in pieces), polygon.area, rel_tol=1e-8, abs_tol=1e-7):
        raise ValueError("Polygon triangulation did not cover the supplied surface")
    return [[[float(x), float(y), heights[(x, y)]] for x, y in list(t.exterior.coords)[:3]]
            for t in pieces]


def prepare_source_environment(map_features, *, scene_id, map_name,
                               origin_in_scenario=(0, 0), extra=None):
    """Return mesh specifications and provenance without importing Panda3D.

``map_features`` uses the engine's centered XY. Extra GeoJSON uses original
nuScenes city coordinates. Building footprints may be XY (base_z_m, default
zero) or XYZ (per-vertex base elevation); combining XYZ with base_z_m is rejected
as ambiguous. Terrain requires explicit XYZ. These meshes are visual only.
"""
    scene_id, map_name = _text(scene_id, "scene_id"), _text(map_name, "map")
    if len(origin_in_scenario) != 2:
        raise ValueError("Origin must be scenario XY")
    origin = [_number(v, "origin") for v in origin_in_scenario]
    meshes, skipped = [], []
    counts = {"map_surfaces": 0, "buildings": 0, "terrain": 0}
    for feature_id, feature in map_features.items():
        kind = feature.get("type")
        if kind not in SURFACES or "polygon" not in feature:
            continue
        # Array dimension is not a declaration of elevation. These map polygons
        # are rendered from XY only; never interpret a centerline's yaw as Z.
        try:
            ring = [[float(point[0]), float(point[1])] for point in feature["polygon"]]
            rings, polygon = _polygon([ring] + feature.get("_source_polygon_holes", []), dimensions=2)
            z = DISPLAY_OFFSETS.get(kind, 0.02)
            elevated = [[[x, y, z] for x, y in r] for r in rings]
            triangles = _triangles(elevated, polygon)
        except (ValueError, TypeError, IndexError) as error:
            skipped.append({"feature_id": str(feature_id), "reason": str(error)})
            continue
        meshes.append({"id": str(feature_id), "kind": "map_surface", "source_type": kind,
                       "triangles": triangles, "color": SURFACES[kind]})
        counts["map_surfaces"] += 1

    extra = _read_extra(extra)
    extra_source = None
    if extra is not None:
        if not isinstance(extra, dict) or extra.get("schema") != "simulation-environment-1.0":
            raise ValueError("Unsupported environment geometry schema")
        for key, expected in (("scene_id", scene_id), ("map", map_name),
                              ("coordinate", "nuScenes_global"), ("units", "m")):
            if extra.get(key) != expected:
                raise ValueError("Environment geometry " + key + " does not match this scenario")
        extra_source = _text(extra.get("source"), "Environment geometry source")
        if not isinstance(extra.get("features"), list):
            raise ValueError("Environment features must be a list")
        seen = set()
        for feature in extra["features"]:
            if not isinstance(feature, dict):
                raise ValueError("Environment feature must be an object")
            feature_id = _text(feature.get("id"), "Environment feature id")
            if feature_id in seen:
                raise ValueError("Duplicate environment feature id")
            seen.add(feature_id)
            kind = feature.get("kind")
            if kind not in {"building", "terrain"}:
                raise ValueError("Unsupported environment feature kind")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
                raise ValueError("Environment geometry must be a GeoJSON Polygon")
            rings, _ = _polygon(geometry.get("coordinates"), dimensions=3 if kind == "terrain" else None)
            has_z = len(rings[0][0]) == 3
            if kind == "terrain" and ("height_m" in feature or "base_z_m" in feature):
                raise ValueError("Terrain must use its explicit vertex elevations")
            if has_z and "base_z_m" in feature:
                raise ValueError("XYZ footprint already supplies base elevations")
            base_z = _number(feature.get("base_z_m", 0), "base_z_m")
            centered = [[[p[0]-origin[0], p[1]-origin[1], p[2] if has_z else base_z]
                         for p in ring] for ring in rings]
            _, polygon = _polygon(centered, dimensions=3)
            if kind == "building":
                height = _number(feature.get("height_m"), "Building height_m")
                if height <= 0:
                    raise ValueError("Building height_m must be positive")
                roof = [[[x, y, z+height] for x, y, z in ring] for ring in centered]
                triangles = _triangles(roof, polygon)
                for ring in centered:
                    for a, b in zip(ring, ring[1:]):
                        upper_a, upper_b = [a[0], a[1], a[2]+height], [b[0], b[1], b[2]+height]
                        triangles.extend([[a, b, upper_b], [a, upper_b, upper_a]])
                color = (0.64, 0.65, 0.68, 1.0)
                counts["buildings"] += 1
            else:
                triangles = _triangles(centered, polygon)
                color = (0.48, 0.44, 0.37, 1.0)
                counts["terrain"] += 1
            meshes.append({"id": feature_id, "kind": kind, "source": extra_source,
                           "triangles": triangles, "color": color})
    report = {
        "mode": "source_geometry_only", "scene_id": scene_id, "map": map_name,
        "source_map_surfaces": counts["map_surfaces"], "buildings_rendered": counts["buildings"],
        "terrain_surfaces_rendered": counts["terrain"], "buildings_missing": counts["buildings"] == 0,
        "terrain_elevation_missing": counts["terrain"] == 0,
        "arbitrary_terrain_generated": False, "collision_geometry_created": False,
        "unmapped_surroundings": "left_empty",
        "map_surface_elevation": "source XY; simulation ground reference 0m, display offsets 0.02-0.04m",
        "nuScenes_lane_third_component": "heading; never interpreted as terrain elevation",
        "extra_geometry_source": extra_source, "skipped_source_polygons": skipped,
        "limitations": ["Environment meshes are visual only; no building/terrain collision shapes are created.",
                        "Absent building or terrain data means unknown surroundings, not proof of empty space.",
                        "XY-only building footprints without base_z_m use a declared simulation base of 0m."]}
    return meshes, report


class _EnvironmentHandle:
    def __init__(self, node):
        self.node = node

    def close(self):
        if self.node is not None:
            self.node.removeNode()
            self.node = None


def attach_source_environment(env, extra=None):
    """Attach supplied map/building/terrain visual meshes to a running engine."""
    scenario = env.engine.data_manager.current_scenario
    metadata = scenario["metadata"]
    offset = metadata.get("old_origin_in_current_coordinate", [0, 0])
    origin = [-float(offset[0]), -float(offset[1])]
    features = env.engine.current_map.map_data
    supplement_report = None
    map_name = metadata.get("map")
    if metadata.get("dataset") == "nuscenes" and isinstance(map_name, str) and Path(map_name).name == map_name:
        raw_map = Path(__file__).resolve().parents[1] / "AIE1903 原始资料/maps/expansion" / (map_name + ".json")
        if raw_map.is_file():
            edited_ids = set()
            bridge = metadata.get("map_update_bridge", {})
            if bridge.get("structural_operations", 0):
                database = getattr(env, "config", {}).get("data_directory")
                patch_log_path = Path(database) / "map_patch_log.json" if database else None
                if patch_log_path is not None and patch_log_path.is_file():
                    log = json.loads(patch_log_path.read_text(encoding="utf-8"))
                    if not isinstance(log, list) or len(log) != bridge["structural_operations"]:
                        raise ValueError("Cannot establish patched feature identities for source polygon recovery")
                    edited_ids = {item["feature_id"] for item in log}
                else:
                    # A missing audit log must never revive original geometry
                    # for an unknown set of modified lanes.
                    edited_ids = set(features)
            features, supplement_report = supplement_source_lane_polygons(
                features, raw_map_path=raw_map, map_name=map_name,
                origin_in_scenario=origin, edited_ids=edited_ids)
    meshes, report = prepare_source_environment(
        features, scene_id=scenario["id"], map_name=map_name,
        origin_in_scenario=origin, extra=extra)
    report["source_polygon_supplement"] = supplement_report
    from panda3d.core import Geom, GeomNode, GeomTriangles, GeomVertexData, GeomVertexFormat, GeomVertexWriter

    node = env.engine.worldNP.attachNewNode("source-environment-geometry")
    # These audit surfaces use explicit vertex colours. Inheriting MetaDrive's
    # textured PBR shader without its material inputs turns them black.
    node.setShaderOff(1)
    node.setLightOff(1)
    node.setTextureOff(1)
    node.setMaterialOff(1)
    node.setColorOff(1)
    handle = _EnvironmentHandle(node)
    try:
        for index, mesh in enumerate(meshes):
            data = GeomVertexData("source-surface", GeomVertexFormat.getV3n3c4(), Geom.UHStatic)
            vertices = GeomVertexWriter(data, "vertex")
            normals = GeomVertexWriter(data, "normal")
            colors = GeomVertexWriter(data, "color")
            primitive = GeomTriangles(Geom.UHStatic)
            for triangle in mesh["triangles"]:
                a, b, c = triangle
                u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
                normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
                magnitude = math.sqrt(sum(x*x for x in normal))
                if magnitude <= 1e-12:
                    continue
                start = vertices.getWriteRow()
                for point in triangle:
                    vertices.addData3f(*point)
                    normals.addData3f(*(x/magnitude for x in normal))
                    colors.addData4f(*mesh["color"])
                primitive.addVertices(start, start+1, start+2)
            primitive.closePrimitive()
            geom = Geom(data)
            geom.addPrimitive(primitive)
            geom_node = GeomNode("source-surface-" + str(index))
            geom_node.addGeom(geom)
            part = node.attachNewNode(geom_node)
            part.setTwoSided(True)
        report["attached_mesh_count"] = len(meshes)
        return handle, report
    except Exception:
        handle.close()
        raise
