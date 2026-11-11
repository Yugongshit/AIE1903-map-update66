"""Portable, source-timed attached map-state observations for simulation.

Road topology is never modified here. Existing traffic is joined by exact
instance identity; an object without a simulator model still has its source
footprint. The bundle includes historical observations, not just final shapes.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from hashlib import sha256
import json
from math import isfinite, radians
from pathlib import Path

from .scene import footprint
from .state_events import build_state_event_layer


SCHEMA = "simulation-state-source-1.0"


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _timestamp(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("State source timestamps must be exact decimal strings")
    return int(value)


def _same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return isfinite(a) and isfinite(b) and abs(a-b) <= 1e-7
    return a == b


def _finite(row, key, positive=False):
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Missing object source field: " + key) from error
    if not isfinite(value) or positive and value <= 0:
        raise ValueError("Invalid object source field: " + key)
    return value


def _read_csv(root, relative):
    path = root / relative
    raw = path.read_bytes()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, {"path": relative, "sha256": sha256(raw).hexdigest()}


def _objects_in_sample(sample):
    tokens = sample.get("instance_tokens", [])
    if not isinstance(tokens, list) or any(not isinstance(t, str) or not t for t in tokens):
        raise ValueError("State sample instance_tokens must be explicit identities")
    if len(set(tokens)) != len(tokens):
        raise ValueError("Duplicate object identity in one state sample")
    if not tokens:
        return []
    source_tokens = [source[9:] for source in sample.get("source_ids", []) if source.startswith("instance:")]
    if source_tokens != tokens:
        raise ValueError("State geometry instance order differs from source identities")
    geometry = sample.get("geometry_city", {})
    shapes = geometry.get("geometries", []) if geometry.get("type") == "GeometryCollection" else [geometry]
    if len(shapes) != len(tokens) or any(shape.get("type") != "Polygon" for shape in shapes):
        raise ValueError("State object identities do not match source footprint geometry")
    return list(zip(tokens, shapes))


def build_state_source(scenario, dataset, source_root):
    """Bundle exact keyframe clocks, complete eligible events and object sources.

Source paths are provenance only. ``state_snapshot`` needs no CSVs, repository,
NumPy or simulator installation once this JSON-serializable bundle is exported.
"""
    metadata = scenario.get("metadata", {})
    scene_id = scenario.get("id")
    map_name = metadata.get("map")
    expected = {"frame": "nuScenes_global", "city_id": map_name, "units": "m"}
    if (metadata.get("dataset") != "nuscenes" or metadata.get("coordinate") != "right-handed"
            or not isinstance(scene_id, str) or not scene_id or not isinstance(map_name, str) or not map_name
            or dataset.get("scene_id") != scene_id):
        raise ValueError("State source and scenario identities do not match")
    if dataset.get("provenance", {}).get("coordinate_reference") != expected:
        raise ValueError("State source coordinate reference differs from the scenario")
    all_events = dataset.get("events")
    if not isinstance(all_events, list):
        raise ValueError("State source requires full pipeline events")
    supplied_layer = dataset.get("state_event_layer")
    if not isinstance(supplied_layer, dict):
        raise ValueError("State source requires the validated pipeline state layer")
    rebuilt = build_state_event_layer(all_events, supplied_layer.get("as_of_timestamp_ns"))
    if _digest(rebuilt) != _digest(supplied_layer):
        raise ValueError("State layer differs from its full event observations")
    events = deepcopy([event for event in all_events
                       if event.get("map_layer", {}).get("predicted_layer") == "map_state_event"
                       and event.get("reliability", {}).get("status") == "supported_conflict"])
    root = Path(source_root)
    rows, frames_source = _read_csv(root, "processed/frames.csv")
    rows.sort(key=lambda row: int(row["frame_index"]))
    frames, times, sample_tokens = [], [], set()
    for index, row in enumerate(rows):
        if str(index) != row.get("frame_index"):
            raise ValueError("Source keyframes must have unique consecutive indices starting at zero")
        token = row.get("sample_token")
        if not isinstance(token, str) or not token or token in sample_tokens:
            raise ValueError("Source keyframes require unique sample tokens")
        sample_tokens.add(token)
        micros = _timestamp(row.get("timestamp_us"))
        stamp = micros * 1000
        if times and stamp <= times[-1]:
            raise ValueError("Source keyframe timestamps must increase strictly")
        times.append(stamp)
        frames.append({"frame_index": index, "frame_id": str(index), "sample_token": token,
                       "timestamp_ns": str(stamp), "replay_step": index * 5})
    length = scenario.get("length")
    if len(frames) < 2 or type(length) is not int or length != (len(frames)-1)*5+1:
        raise ValueError("Scenario length must match five replay intervals per source keyframe interval")
    ts = metadata.get("ts", [])
    if len(ts) != length or any(not isfinite(float(t)) or abs(float(t)-i/10) > 1e-7 for i, t in enumerate(ts)):
        raise ValueError("Scenario replay clock does not match its 0.1s converter sampling")
    object_rows, objects_source = _read_csv(root, "processed/objects.csv")
    object_index = defaultdict(list)
    for row in object_rows:
        object_index[(row.get("instance_token"), row.get("frame_index"))].append(row)
    catalog, observation_count = {}, 0
    for event in events:
        if event.get("coordinate_reference") != expected:
            raise ValueError("An event uses a different coordinate reference")
        for sample in event.get("samples", []):
            frame_id = sample.get("frame_id")
            if not isinstance(frame_id, str) or not frame_id.isdecimal() or str(int(frame_id)) != frame_id:
                raise ValueError("State samples require canonical source frame IDs")
            index = int(frame_id)
            if index >= len(frames) or sample.get("timestamp_ns") != frames[index]["timestamp_ns"]:
                raise ValueError("State sample does not match its exact source keyframe timestamp")
            if sample.get("observation") != "supports_change" or sample.get("quality", 0) <= 0:
                continue
            observation_count += 1
            for token, geometry in _objects_in_sample(sample):
                candidates = object_index[(token, frame_id)]
                if len(candidates) != 1:
                    raise ValueError("Missing or ambiguous exact source object/frame identity: " + token)
                row = candidates[0]
                if not row.get("annotation_token") or row.get("sample_token") != frames[index]["sample_token"]:
                    raise ValueError("Object annotation does not match its source sample")
                if not _same(footprint(row), geometry):
                    raise ValueError("State footprint differs from its exact source annotation")
                category = row.get("category")
                if not isinstance(category, str) or not category:
                    raise ValueError("Object source category is missing")
                track = scenario.get("tracks", {}).get(token)
                if track is not None and track.get("metadata", {}).get("original_id", token) != token:
                    raise ValueError("Converted track identity differs from state instance")
                item = {"instance_id": token, "category": category,
                        "type": track.get("type") if track is not None else "UNSUPPORTED_SOURCE_OBJECT",
                        "matched_track_ids": [token] if track is not None else [], "missing_model": track is None}
                if token not in catalog:
                    catalog[token] = {**item, "observations": {}}
                elif any(catalog[token][key] != value for key, value in item.items()):
                    raise ValueError("State instance has inconsistent source identity or category")
                normalized = {"frame_id": frame_id, "timestamp_ns": sample["timestamp_ns"],
                              "annotation_token": row["annotation_token"], "sample_token": row["sample_token"],
                              "center_xy": [_finite(row, "x_global_m"), _finite(row, "y_global_m")],
                              "length_m": _finite(row, "length_m", True), "width_m": _finite(row, "width_m", True),
                              "height_m": _finite(row, "height_m", True), "yaw_rad": radians(_finite(row, "yaw_global_deg")),
                              "source_file": "processed/objects.csv"}
                catalog[token]["observations"][frame_id] = normalized
    return {"schema": SCHEMA, "dataset": "nuscenes", "scene_id": scene_id, "map": map_name,
            "coordinate_reference": expected, "scenario_length": length, "source_frames": frames,
            "events": events, "object_sources": catalog,
            "clock": {"replay_intervals_per_source_interval": 5, "nominal_replay_step_ns": 100_000_000,
                      "mapping": "piecewise_integer_interpolation_between_exact_source_keyframes",
                      "state_observation_sampling": "sample_hold_at_latest_arrived_source_keyframe",
                      "after_recording": "source_time_unavailable_presence_unknown_no_new_observations"},
            "summary": {"events": len(events), "observations": observation_count, "historical_instances": len(catalog),
                        "matched_instances": sum(not item["missing_model"] for item in catalog.values()),
                        "missing_model_instances": sum(item["missing_model"] for item in catalog.values()),
                        "categories": dict(sorted(Counter(item["category"] for item in catalog.values()).items()))},
            "provenance": {"source_files": [frames_source, objects_source],
                           "pipeline_events_sha256": _digest(all_events), "pipeline_state_layer_sha256": _digest(supplied_layer),
                           "identity_join": "instance_token_and_exact_source_frame_not_nearest_geometry",
                           "source_paths_required_at_runtime": False, "base_map_modified": False,
                           "new_obstacles_spawned": False, "traffic_is_not_automatically_a_map_state_event": True,
                           "temporal_scope": "source_observations_for_offline_classified_events"}}


def _translate(geometry, offset):
    if geometry["type"] == "GeometryCollection":
        return {"type": "GeometryCollection", "geometries": [_translate(g, offset) for g in geometry["geometries"]]}
    rings = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    moved = [[[float(p[0])+offset[0], float(p[1])+offset[1]] for p in ring] for ring in rings]
    return {"type": geometry["type"], "coordinates": moved[0] if geometry["type"] == "LineString" else moved}


def state_snapshot(bundle, step, source_to_runtime_offset=(0, 0)):
    """Return attached state annotations at one replay step, without mutation.

Within a source keyframe interval the observed state is held; its age increases
explicitly. After the recording there are no further observations, and the
existing lifecycle reducer marks unsupported current presence unknown/stale.
"""
    if not isinstance(bundle, dict) or bundle.get("schema") != SCHEMA:
        raise ValueError("Unsupported simulation state source bundle")
    if type(step) is not int or step < 0:
        raise ValueError("State replay step must be a nonnegative integer")
    if len(source_to_runtime_offset) != 2:
        raise ValueError("State runtime offset must contain XY")
    offset = [float(x) for x in source_to_runtime_offset]
    if not all(isfinite(x) for x in offset):
        raise ValueError("State runtime offset must be finite")
    frames = bundle["source_frames"]
    times = [_timestamp(frame["timestamp_ns"]) for frame in frames]
    last_step = bundle["scenario_length"]-1
    if step > last_step:
        keyframe = len(frames)-1
        source_now = None
        observation_cutoff = times[-1]
        evaluation_cutoff = observation_cutoff
        clock_mode = "outside_recorded_window"
    else:
        keyframe, remainder = divmod(step, 5)
        observation_cutoff = times[keyframe]
        source_now = observation_cutoff
        if remainder:
            source_now += (times[keyframe+1]-times[keyframe])*remainder//5
        evaluation_cutoff = observation_cutoff
        clock_mode = "source_keyframe" if not remainder else "interpolated_source_clock"
    layer = build_state_event_layer(bundle["events"], str(evaluation_cutoff))
    if source_now is None:
        for feature in layer["features"]:
            properties = feature["properties"]
            if properties["state"] == "observed_active":
                properties.update(state="unknown", validity="unverified", current_quality=None)
            properties["runtime_reason"] = "OUTSIDE_RECORDED_WINDOW_NO_CURRENT_OBSERVATION"
    else:
        # Holding an observation between keyframes does not stop its declared
        # expiry clock. Apply only known TTL/absolute deadlines at source_now;
        # do not fabricate an intermediate missing observation or future sample.
        for feature in layer["features"]:
            properties = feature["properties"]
            expires = properties.get("expires_at_timestamp_ns")
            if expires is not None and source_now >= _timestamp(expires):
                properties.update(state="stale", validity="expired", current_quality=None,
                                  runtime_reason="EXPLICIT_EXPIRY_REACHED")
    counts = Counter(feature["properties"]["state"] for feature in layer["features"])
    for name in ("observed_active", "unknown", "stale"):
        layer["summary"][name] = counts[name]
    features = {f["properties"]["event_id"]: f for f in layer["features"]}
    associations = defaultdict(dict)
    for event in bundle["events"]:
        eid = event["event_id"]
        if eid not in features:
            continue  # Unobserved and explicitly cleared events have no tags.
        prop = features[eid]["properties"]
        clear_times = [int(h["timestamp_ns"]) for h in prop["history"] if h["state"] == "cleared"]
        latest_clear = max(clear_times, default=-1)
        grouped = defaultdict(list)
        for sample in event.get("samples", []):
            stamp = _timestamp(sample["timestamp_ns"])
            if (latest_clear < stamp <= observation_cutoff and sample.get("observation") == "supports_change"
                    and sample.get("quality", 0) > 0):
                grouped[stamp].append(sample)
        for stamp, samples in sorted(grouped.items()):
            sample = max(samples, key=lambda row: row["quality"])
            for token, geometry in _objects_in_sample(sample):
                status = prop["state"]
                if status == "observed_active" and str(stamp) != prop["last_seen_timestamp_ns"]:
                    status = "unknown"
                associations[token][eid] = {"status": status, "timestamp_ns": str(stamp),
                                            "frame_id": sample["frame_id"], "geometry_city": geometry,
                                            "event_id": eid}
    objects = []
    priority = {"observed_active": 2, "unknown": 1, "stale": 0}
    for token, by_event in sorted(associations.items()):
        latest = max(by_event.values(), key=lambda item: (int(item["timestamp_ns"]), priority[item["status"]]))
        source = bundle["object_sources"][token]
        row = source["observations"][latest["frame_id"]]
        status = max((item["status"] for item in by_event.values()), key=priority.__getitem__)
        geometry = deepcopy(latest["geometry_city"])
        runtime = _translate(geometry, offset)
        objects.append({"instance_id": token, "instance_token": token, "object_id": "instance:"+token,
                        "event_ids": sorted(by_event), "event_states": {eid: item["status"] for eid, item in sorted(by_event.items())},
                        "category": source["category"], "type": source["type"], "status": status,
                        "historical": status != "observed_active", "missing_model": source["missing_model"],
                        "matched_track_ids": list(source["matched_track_ids"]),
                        "last_observed_timestamp_ns": latest["timestamp_ns"], "source_frame_id": latest["frame_id"],
                        "geometry_city": geometry, "geometry_runtime": runtime,
                        "runtime_paths": deepcopy(runtime["coordinates"]),
                        "geometry_role": "last_observed_footprint_not_verified_current_extent",
                        "annotation_token": row["annotation_token"], "sample_token": row["sample_token"],
                        "length_m": row["length_m"], "width_m": row["width_m"], "height_m": row["height_m"],
                        "yaw_rad": row["yaw_rad"], "center_xy": list(row["center_xy"]),
                        "center_runtime_xy": [row["center_xy"][i]+offset[i] for i in range(2)],
                        "base_map_modified": False})
    return {"schema": "simulation-state-snapshot-1.0", "scene_id": bundle["scene_id"], "map": bundle["map"],
            "step": step, "source_timestamp_ns": str(source_now) if source_now is not None else None,
            "observation_cutoff_ns": str(observation_cutoff),
            "state_evaluation_cutoff_ns": str(evaluation_cutoff) if source_now is not None else None,
            "expiry_evaluation_timestamp_ns": str(source_now) if source_now is not None else None,
            "source_frame_id": frames[keyframe]["frame_id"],
            "source_snapshot_age_s": (source_now-observation_cutoff)/1_000_000_000 if source_now is not None else None,
            "sampling_policy": "sample_hold", "clock_mode": clock_mode,
            "coordinate_reference": deepcopy(bundle["coordinate_reference"]),
            "source_to_runtime_offset_m": offset, "layer": layer, "objects": objects,
            "summary": {"events": deepcopy(layer["summary"]), "objects": len(objects),
                        "objects_by_status": dict(sorted(Counter(obj["status"] for obj in objects).items())),
                        "matched_instances": sum(not obj["missing_model"] for obj in objects),
                        "missing_model_instances": sum(obj["missing_model"] for obj in objects)},
            "base_map_modified": False, "new_obstacles_spawned": False,
            "traffic_is_not_automatically_a_map_state_event": True}
