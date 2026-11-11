"""Source-only scenery preserves geometry and rejects invented elevations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from map_update.simulation_environment import prepare_source_environment, supplement_source_lane_polygons

try:
    from shapely import constrained_delaunay_triangles
except ImportError:
    constrained_delaunay_triangles = None

requires_geometry = unittest.skipIf(
    constrained_delaunay_triangles is None,
    "Optional simulation dependency Shapely 2.1+ is not installed")


def extra(features):
    return {"schema": "simulation-environment-1.0", "scene_id": "scene-0980",
            "map": "singapore-onenorth", "coordinate": "nuScenes_global", "units": "m",
            "source": "survey-example", "features": features}


def surface(kind="building"):
    f = {"id": "b1", "kind": kind,
         "geometry": {"type": "Polygon", "coordinates": [[[100, 200], [104, 200], [104, 203], [100, 203], [100, 200]]]},
         "height_m": 7, "base_z_m": 2}
    return f


class EnvironmentGeometryTests(unittest.TestCase):
    def prepare(self, features=None, additional=None):
        return prepare_source_environment(features or {}, scene_id="scene-0980", map_name="singapore-onenorth",
                                          origin_in_scenario=[100, 200], extra=additional)

    def test_empty_source_does_not_invent_terrain_or_buildings(self):
        meshes, report = self.prepare()
        self.assertEqual(meshes, [])
        self.assertTrue(report["buildings_missing"])
        self.assertFalse(report["arbitrary_terrain_generated"])
        self.assertEqual(report["unmapped_surroundings"], "left_empty")

    @requires_geometry
    def test_concave_polygon_covers_only_source_area_and_ignores_yaw(self):
        ring = [[0, 0], [3, 0], [3, 1], [1, 1], [1, 3], [0, 3], [0, 0]]
        features = {"road": {"type": "LANE_SURFACE_STREET", "polygon": ring,
                             "polyline": [[0, 0, -3.14], [1, 1, 3.14]]}}
        meshes, report = self.prepare(features)
        triangles = meshes[0]["triangles"]
        area = sum(abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2 for a, b, c in triangles)
        self.assertAlmostEqual(area, 5)
        self.assertTrue(all(p[2] == .02 for t in triangles for p in t))
        self.assertEqual(report["source_map_surfaces"], 1)

    @requires_geometry
    def test_invalid_source_polygon_is_reported_not_repaired(self):
        meshes, report = self.prepare({"bad": {"type": "CROSSWALK", "polygon": [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]}})
        self.assertEqual(meshes, [])
        self.assertEqual(report["skipped_source_polygons"][0]["feature_id"], "bad")

    @requires_geometry
    def test_building_uses_real_footprint_height_and_origin(self):
        source = extra([surface()])
        before = copy.deepcopy(source)
        meshes, report = self.prepare(additional=source)
        points = [p for t in meshes[0]["triangles"] for p in t]
        self.assertEqual({p[2] for p in points}, {2., 9.})
        self.assertEqual(min(p[0] for p in points), 0)
        self.assertEqual(max(p[1] for p in points), 3)
        self.assertEqual(report["buildings_rendered"], 1)
        self.assertFalse(report["collision_geometry_created"])
        self.assertEqual(source, before)

    @requires_geometry
    def test_terrain_preserves_explicit_z(self):
        feature = {"id": "slope", "kind": "terrain", "geometry": {"type": "Polygon", "coordinates": [
            [[100, 200, 1], [104, 200, 2], [104, 203, 3], [100, 203, 2], [100, 200, 1]]]}}
        meshes, report = self.prepare(additional=extra([feature]))
        self.assertEqual({p[2] for t in meshes[0]["triangles"] for p in t}, {1., 2., 3.})
        self.assertEqual(report["terrain_surfaces_rendered"], 1)

    @requires_geometry
    def test_holes_remain_unfilled(self):
        feature = surface()
        feature["geometry"]["coordinates"].append([[101, 201], [102, 201], [102, 202], [101, 202], [101, 201]])
        meshes, _ = self.prepare(additional=extra([feature]))
        roof = [t for t in meshes[0]["triangles"] if all(p[2] == 9 for p in t)]
        area = sum(abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2 for a, b, c in roof)
        self.assertAlmostEqual(area, 11)

    @requires_geometry
    def test_wrong_identity_missing_height_and_2d_terrain_rejected(self):
        for field, value in [("scene_id", "foreign"), ("map", "other"), ("units", "degrees"), ("source", "")]:
            data = extra([surface()]); data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(additional=data)
        feature = surface(); feature.pop("height_m")
        with self.assertRaises(ValueError):
            self.prepare(additional=extra([feature]))
        feature = surface("terrain")
        with self.assertRaises(ValueError):
            self.prepare(additional=extra([feature]))

    @requires_geometry
    def test_xyz_building_keeps_base_elevation_and_rejects_conflicting_base(self):
        feature = surface()
        feature["geometry"]["coordinates"] = [[[x, y, 4] for x, y in feature["geometry"]["coordinates"][0]]]
        with self.assertRaises(ValueError):
            self.prepare(additional=extra([feature]))
        feature.pop("base_z_m")
        meshes, _ = self.prepare(additional=extra([feature]))
        self.assertEqual({p[2] for t in meshes[0]["triangles"] for p in t}, {4., 11.})

    @requires_geometry
    def test_exact_source_polygon_recovery_leaves_engine_map_unchanged(self):
        raw = {"node": [{"token": str(i), "x": x, "y": y}
                        for i, (x, y) in enumerate([[100, 200], [104, 200], [104, 203], [100, 203]])],
               "polygon": [{"token": "p", "exterior_node_tokens": ["0", "1", "2", "3"], "holes": []}],
               "lane_connector": [{"token": "connector", "polygon_token": "p"}]}
        features = {"connector": {"type": "LANE_SURFACE_UNSTRUCTURE", "polyline": [[0, 0], [4, 3]]},
                    "nearby": {"type": "LANE_SURFACE_UNSTRUCTURE", "polyline": [[0, 0], [4, 3]]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "singapore-onenorth.json"
            path.write_text(json.dumps(raw))
            changed, report = supplement_source_lane_polygons(features, raw_map_path=path,
                map_name="singapore-onenorth", origin_in_scenario=[100, 200])
            self.assertEqual(changed["connector"]["polygon"][0], [0., 0.])
            self.assertNotIn("polygon", changed["nearby"])
            self.assertNotIn("polygon", features["connector"])
            self.assertEqual(report["supplemented_count"], 1)
            self.assertEqual(len(report["source_sha256"]), 64)
            changed, report = supplement_source_lane_polygons(features, raw_map_path=path,
                map_name="singapore-onenorth", origin_in_scenario=[100, 200], edited_ids={"connector"})
            self.assertNotIn("polygon", changed["connector"])
            self.assertEqual(report["skipped"][0]["reason"], "feature_modified_by_patch")
            with self.assertRaises(ValueError):
                supplement_source_lane_polygons(features, raw_map_path=path, map_name="other-city")


if __name__ == "__main__":
    unittest.main()
