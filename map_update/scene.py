"""Load the original nuScenes map and frame-specific evidence, keeping write gates closed."""

from collections import Counter, defaultdict
from hashlib import sha256
from math import cos, sin, radians
from pathlib import Path

from .delivery import inspect_delivery, read_csv, read_json
from .geometry import validate_geometry


def index_unique(rows, key):
    indexed = {key(r): r for r in rows}
    if len(indexed) != len(rows):
        raise ValueError("duplicate source identifier")
    return indexed


def footprint(row):
    x, y = float(row["x_global_m"]), float(row["y_global_m"])
    length, width = float(row["length_m"]), float(row["width_m"])
    if length <= 0 or width <= 0:
        raise ValueError("nonpositive object footprint size")
    angle = radians(float(row["yaw_global_deg"]))
    coords = [[x+u*cos(angle)-v*sin(angle), y+u*sin(angle)+v*cos(angle)]
              for u, v in [(-length/2, -width/2), (length/2, -width/2), (length/2, width/2), (-length/2, width/2)]]
    return validate_geometry({"type": "Polygon", "coordinates": [coords+[coords[0]]]}, line_only=False)


def load_map(path, bounds):
    raw = read_json(path)
    nodes = index_unique(raw["node"], lambda n: n["token"])
    lines = index_unique(raw["line"], lambda n: n["token"])
    polygons = index_unique(raw["polygon"], lambda n: n["token"])

    def xy(tokens, closed=False):
        points = [[nodes[t]["x"], nodes[t]["y"]] for t in tokens]
        if closed and points and points[0] != points[-1]:
            points.append(points[0])
        return points

    def overlaps(points):
        return (min(p[0] for p in points) <= bounds[2] and max(p[0] for p in points) >= bounds[0]
                and min(p[1] for p in points) <= bounds[3] and max(p[1] for p in points) >= bounds[1])

    elements = []
    for layer in ("road_segment", "lane", "lane_connector", "ped_crossing", "road_divider", "lane_divider"):
        for record in raw[layer]:
            if "line_token" in record:
                geometry = {"type": "LineString", "coordinates": xy(lines[record["line_token"]]["node_tokens"])}
                points = geometry["coordinates"]
            else:
                polygon = polygons[record["polygon_token"]]
                outer = xy(polygon["exterior_node_tokens"], closed=True)
                geometry = {"type": "Polygon", "coordinates": [outer]+[xy(h["node_tokens"], closed=True) for h in polygon["holes"]]}
                points = outer
            if not points or not overlaps(points):
                continue
            validate_geometry(geometry)
            elements.append({"element_id": layer+":"+record["token"], "layer": layer, "map_token": record["token"],
                             "geometry_city": geometry, "properties": record, "active": True})
    digest = sha256(Path(path).read_bytes()).hexdigest()
    return {"schema_version": "reference-map-1.0", "map_id": Path(path).stem,
            "version": f"nuscenes-{raw['version']}-{digest[:12]}", "source_version": str(raw['version']), "source_sha256": digest,
            "map_role": "reference_unverified_temporal_role", "coordinate_reference": {"frame": "nuScenes_global", "city_id": Path(path).stem, "units": "m"},
            "bounds_xyxy": bounds, "selection_method": "bbox_intersection; original feature geometry retained without clipping",
            "elements": elements, "write_eligible": False}


def inspect_scene(raw_root, delivery_root):
    root = Path(raw_root).resolve()
    processed = root/"processed"
    intake = inspect_delivery(delivery_root)
    scene_info = read_json(root/"scene_info.json")
    if scene_info["scene"] != intake["scene_id"]:
        raise ValueError("raw scene and delivered scene differ")
    city = scene_info["location"]
    # Map name is an identifier, never a path supplied by a document.
    if Path(city).name != city or city in {".", ".."}:
        raise ValueError("invalid map location")
    used = set()
    checks = []

    def require(name, condition):
        checks.append({"check": name, "passed": bool(condition)})
        if not condition:
            raise ValueError("raw scene validation failed: "+name)

    def csv_file(relative):
        path = root/relative
        used.add(path)
        return read_csv(path)

    def json_file(relative):
        path = root/relative
        used.add(path)
        return read_json(path)

    used.add(root/"scene_info.json")
    expected_hashes = read_json(Path(delivery_root)/"outputs/stage3/input_hashes.json")
    for name, expected in expected_hashes.items():
        path = (processed/name).resolve()
        require("source_path_within_processed:"+name, path.is_relative_to(processed))
        used.add(path)
        require("source_sha256:"+name, path.is_file() and sha256(path.read_bytes()).hexdigest() == expected)
    frames = csv_file("processed/frames.csv")
    frame_index = index_unique(frames, lambda r: int(r["frame_index"]))
    times = [int(r["timestamp_us"]) for r in frames]
    require("frames_have_increasing_absolute_time", all(b > a for a, b in zip(times, times[1:])))
    require("frame_elapsed_matches_absolute_time", all(abs((int(r["timestamp_us"])-times[0])/1e6-float(r["time_s"])) <= 1e-6 for r in frames))
    logs = json_file("v1.0-trainval/log.json")
    scenes = json_file("v1.0-trainval/scene.json")
    scene = next((s for s in scenes if s["name"] == scene_info["scene"]), None)
    require("scene_metadata_found", scene is not None)
    log = next((r for r in logs if r["token"] == scene["log_token"]), None)
    require("scene_log_city_matches_map", log is not None and log["location"] == city)
    require("frame_count_matches_scene", len(frames) == scene["nbr_samples"] == scene_info["sample_count"])
    candidates = index_unique(csv_file("processed/scene0980_unified_candidates_v1.csv"), lambda r: r["candidate_id"])
    require("candidate_ids_match_delivery", set(candidates) == {c["candidate_id"] for c in intake["candidates"]})
    all_points = [p for c in intake["candidates"] for p in c["geometry"]["coordinates"]]
    all_points += [[float(p["x_m"]), float(p["y_m"])] for p in intake["vehicle_features"]]
    all_points += [[float(f["ego_x_m"]), float(f["ego_y_m"])] for f in frames]
    bounds = [min(p[0] for p in all_points)-15, min(p[1] for p in all_points)-15,
              max(p[0] for p in all_points)+15, max(p[1] for p in all_points)+15]
    map_path = root/"maps/expansion"/(city+".json")
    used.add(map_path)
    context_map = load_map(map_path, bounds)
    map_elements = index_unique(context_map["elements"], lambda e: e["element_id"])
    # Confirm supplied divider vertices share the exact coordinates/order of map records.
    vertex_groups = defaultdict(list)
    for row in csv_file("processed/road_perception/scene0980_hdmap_divider_vertices.csv"):
        vertex_groups[(row["layer"], row["map_token"], row["part_id"])].append(row)
    for (layer, token, part), rows in vertex_groups.items():
        source = [[float(r["global_x_m"]), float(r["global_y_m"])] for r in sorted(rows, key=lambda r: int(r["vertex_id"]))]
        feature = map_elements.get(layer+":"+token)
        require("divider_matches_source_map:"+token, feature is not None and feature["geometry_city"]["coordinates"] == source)

    replay = {i: {"frame_index": i, "timestamp_ns": str(int(f["timestamp_us"])*1000), "elapsed_s": float(f["time_s"]),
                  "ego": {"xy": [float(f["ego_x_m"]), float(f["ego_y_m"])], "yaw_deg": float(f["ego_yaw_deg"])},
                  "observed_lines": [], "occupancy": [], "vehicles": [], "candidate_ids": []} for i, f in frame_index.items()}
    for row in csv_file("processed/road_perception/scene0980_sensor_hdmap_line_metrics.csv"):
        i, line_id = int(row["frame"]), int(row["line_id"])
        require(f"curve_frame_exists:{i}:{line_id}", i in replay)
        filename = f"frame{i:03d}_multi_lane_{line_id:02d}.csv"
        points = [[float(p["global_x_m"]), float(p["global_y_m"])] for p in csv_file("processed/road_perception/"+filename)]
        geometry = validate_geometry({"type": "LineString", "coordinates": points}, line_only=True)
        ids = [c["candidate_id"] for c in candidates.values() if c["evidence_family"] == "LANE_MARKING_GEOMETRY"
               and c["track_id"] == row["track_id"] and int(c["first_frame"]) <= i <= int(c["last_frame"])]
        replay[i]["observed_lines"].append({"line_id": line_id, "track_id": row["track_id"], "candidate_ids": ids,
                                           "geometry_city": geometry, "source_file": "processed/road_perception/"+filename,
                                           "nearest_map_median_m": float(row["nearest_map_median_m"]),
                                           "best_map_token": row["best_map_token"] or None,
                                           "note": "nearest map reference is not a verified change target"})
        replay[i]["candidate_ids"].extend(ids)

    regions = index_unique(json_file("processed/occupancy_regions.json")["regions"], lambda r: r["event_id"])
    objects = index_unique(csv_file("processed/objects.csv"), lambda r: (r["instance_token"], int(r["frame_index"])))
    impacts = index_unique(csv_file("processed/lane_impact_frames_v2.csv"), lambda r: (r["event_id"], int(r["frame_index"])))
    for c in intake["candidates"]:
        original = candidates[c["candidate_id"]]
        c["source_context"] = original
        start, end = int(original["first_frame"]), int(original["last_frame"])
        c["frame_window"] = [start, end]
        c["window_timestamp_ns"] = [replay[start]["timestamp_ns"], replay[end]["timestamp_ns"]]
        c["geometry_scope"] = "pooled_summary_retained; frame-specific geometry available in replay"
        c["reasons"] = ["single_clip_not_independent_revisits", "M3_reliability_not_supplied", "visibility_denominator_unavailable",
                         "reference_map_temporal_role_not_verified"]
        if c["change_type"] == "occupancy":
            c["map_element_id"] = original["map_layer"]+":"+original["map_token"]
            require("occupancy_map_target_exists:"+c["candidate_id"], c["map_element_id"] in map_elements)
            region = regions[original["source_event_id"]]
            require("occupancy_region_map_target_matches:"+c["candidate_id"], region["lane_token"] == original["map_token"] and region["lane_layer"] == original["map_layer"])
            c["reasons"].append("occupancy_does_not_establish_structural_map_change")
            for i in range(start, end+1):
                impact = impacts.get((original["source_event_id"], i))
                if impact is None:
                    continue
                shapes, seen = [], set()
                for member in region["member_episodes"]:
                    if i not in {int(f) for f in member["frame_ids"]} or member["instance"] in seen:
                        continue
                    obj = objects.get((member["instance"], i))
                    require(f"occupancy_object_present:{c['candidate_id']}:{i}:{member['instance']}", obj is not None)
                    seen.add(member["instance"])
                    shapes.append({"instance_token": member["instance"], "category": obj["category"],
                                   "geometry_city": footprint(obj), "source": "nuScenes annotated oriented box"})
                require(f"occupancy_geometry_present:{c['candidate_id']}:{i}", bool(shapes))
                replay[i]["occupancy"].append({"candidate_id": c["candidate_id"], "map_element_id": c["map_element_id"],
                                               "objects": shapes, "lane_overlap_area_m2": float(impact["lane_overlap_area_m2"]),
                                               "occupied_width_fraction": float(impact["occupied_width_fraction"]),
                                               "width_geometry_valid": impact["width_geometry_valid"] == "True"})
                replay[i]["candidate_ids"].append(c["candidate_id"])
        else:
            c["reasons"].append("LD_discrepancy_not_verified_divider_edit_target")
        c["frame_indices"] = [i for i, f in replay.items() if c["candidate_id"] in f["candidate_ids"]]
        require("candidate_frame_count_matches_delivered:"+c["candidate_id"], len(c["frame_indices"]) == c["observed_frames"])
    for row in intake["vehicle_features"]:
        i = int(row["frame"])
        require("vehicle_timestamp_matches_frame:"+row["track_id"]+":"+str(i), abs(float(row["time_s"])-replay[i]["elapsed_s"]) <= 1e-6)
        obj = objects.get((row["track_id"], i))
        require("vehicle_annotation_exists:"+row["track_id"]+":"+str(i), obj is not None)
        require("vehicle_center_matches_annotation:"+row["track_id"]+":"+str(i),
                abs(float(obj["x_global_m"])-float(row["x_m"])) <= 0.001
                and abs(float(obj["y_global_m"])-float(row["y_m"])) <= 0.001)
        replay[i]["vehicles"].append({"track_id": row["track_id"], "xy": [float(row["x_m"]), float(row["y_m"])],
                                     "speed_mps": float(row["speed_mps"]) if row["speed_mps"] else None,
                                     "geometry_city": footprint(obj), "category": obj["category"],
                                     "yaw_deg": float(obj["yaw_global_deg"]),
                                     "source": "NUSCENES_GT_DERIVED"})
    for f in replay.values():
        f["candidate_ids"] = sorted(set(f["candidate_ids"]))
    alignment = json_file("processed/alignment_check.json")
    intake.update(schema_version="perception-scene-1.0", reference_map=context_map, replay=list(replay.values()),
                  coordinate_reference=context_map["coordinate_reference"],
                  time_reference="timestamp_us from frames.csv multiplied exactly by 1000; clip elapsed retained",
                  visit_id=scene["token"], scene_metadata={"scene": scene_info["scene"], "location": city, "scene_token": scene["token"], "log_token": scene["log_token"]},
                  source_files=[{"path": p.relative_to(root).as_posix(), "sha256": sha256(p.read_bytes()).hexdigest()} for p in sorted(used)],
                  raw_validation={"status": "PASS", "checks": checks},
                  alignment_diagnostics={"max_roundtrip_error_m": alignment["max_roundtrip_error_m"], "note": "Algebraic round-trip and sensor timestamp checks do not establish physical projection accuracy or M3 alignment_valid."},
                  missing_requirements=["M3 reliability / effective coverage / physical alignment assessment", "true independent revisits",
                                        "verified prior/current temporal map semantics", "verified map-divider edit correspondence for LD candidates"])
    intake["summary"].update(frames=len(replay), duration_s=float(frames[-1]["time_s"]),
                            context_map_features=len(context_map["elements"]), context_map_layers=dict(Counter(e["layer"] for e in context_map["elements"])),
                            frame_line_observations=sum(len(f["observed_lines"]) for f in replay.values()),
                            occupancy_frame_events=sum(len(f["occupancy"]) for f in replay.values()))
    intake["limitations"] = ["Reference map geometry is available, but its verified prior/current temporal relationship remains unknown.",
                             "No M3 reliability or calibrated physical alignment result; algebraic roundtrip is not physical calibration accuracy.",
                             "All data are one visit. Candidate scores and behavior associations use whole intervals; retrospective use only.",
                             "OCC references lane polygons; this is not a lane-divider edit correspondence. LD target identities remain unverified.",
                             "No geometry or lane connectivity is changed. Missing frame observations are not recovery evidence."]
    return intake
