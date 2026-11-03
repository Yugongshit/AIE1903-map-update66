"""Read the actual stage3/5/6 delivery without inventing map or visibility data.

This intake is deliberately distinct from the canonical map-writing contract:
the supplied MultiPoints and clip-relative time cannot fill its missing fields.
"""

from collections import Counter
import csv
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def inspect_delivery(root):
    root = Path(root).resolve()
    stage3, stage5, stage6 = (root/"outputs"/name for name in ("stage3", "stage5", "stage6"))
    candidates = read_csv(stage3/"scored_candidates.csv")
    frames = read_csv(stage3/"frame_evidence.csv")
    geometries = read_json(stage3/"geometry.json")
    behaviors = read_csv(stage5/"behavior_events.csv")
    tracks = read_csv(stage5/"vehicle_features.csv")
    associations = read_csv(stage6/"associations.csv")
    association_frames = read_csv(stage6/"association_frame_evidence.csv")
    checks = []

    def check(name, passed):
        checks.append({"check": name, "passed": bool(passed)})

    manifest = read_json(root/"manifest.json")
    for entry in manifest["files"]:
        path = (root/entry["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("manifest path escapes delivery root")
        check("sha256:"+entry["path"], path.is_file() and sha256(path.read_bytes()).hexdigest() == entry["sha256"])
    ids = {r["candidate_id"] for r in candidates}
    behavior_ids = {r["behavior_id"] for r in behaviors}
    check("unique_candidates", len(ids) == len(candidates))
    check("geometry_candidate_keys", set(geometries) == ids)
    check("frame_candidate_keys", all(r["candidate_id"] in ids for r in frames))
    check("unique_candidate_frames", len({(r["candidate_id"], r["frame"]) for r in frames}) == len(frames))
    check("unique_behavior_ids", len(behavior_ids) == len(behaviors))
    check("behavior_samples_cover_tracks", sum(int(r["samples"]) for r in behaviors) == len(tracks))
    check("unique_track_frames", len({(r["track_id"], r["frame"]) for r in tracks}) == len(tracks))
    check("association_foreign_keys", all(r["candidate_id"] in ids and r["behavior_id"] in behavior_ids for r in associations))
    pairs = {(r["candidate_id"], r["behavior_id"]) for r in associations}
    check("unique_association_pairs", len(pairs) == len(associations))
    check("association_frame_foreign_keys", all((r["candidate_id"], r["behavior_id"]) in pairs for r in association_frames))
    check("association_distances_finite_nonnegative", all(isfinite(float(r["distance_m"])) and float(r["distance_m"]) >= 0 for r in association_frames))
    normalized = []
    for r in candidates:
        cid = r["candidate_id"]
        local = [f for f in frames if f["candidate_id"] == cid]
        check(cid+":observed_frames", len(local) == int(r["observed_frames"]))
        check(cid+":usable_count", sum(f["observation_usable"] == "True" for f in local) == int(r["usable_source_frames"]))
        check(cid+":support_count", sum(f["supports_hypothesis"] == "True" for f in local) == int(r["support_frames"]))
        score = float(r["evidence_score"])
        check(cid+":score_bounds", isfinite(score) and 0 <= score <= 1)
        g = geometries.get(cid, {})
        valid_points = (g.get("type") == "MultiPoint" and isinstance(g.get("coordinates"), list) and bool(g["coordinates"])
                        and all(isinstance(p, list) and len(p) == 2 and all(type(x) in (int, float) and isfinite(x) for x in p) for p in g["coordinates"]))
        check(cid+":finite_point_geometry", valid_points)
        if not valid_points:
            raise ValueError(f"invalid MultiPoint geometry: {cid}")
        occupancy = r["evidence_family"] == "ROAD_OCCUPANCY"
        check(cid+":known_evidence_family", r["evidence_family"] in {"ROAD_OCCUPANCY", "LANE_MARKING_GEOMETRY"})
        normalized.append({"candidate_id": cid, "change_type": "occupancy" if occupancy else "line_discrepancy",
                           "persistence_status": "insufficient_evidence", "storage_layer": "temporary_candidates" if occupancy else "unverified_candidates",
                           "independent_visits": 1, "write_eligible": False, "map_element_id": None,
                           "window_s": [float(r["first_time_s"]), float(r["last_time_s"])],
                           "evidence_score": score, "score_method": "stage3-v1_weighted_ranking_not_probability",
                           "observed_frames": int(r["observed_frames"]), "usable_source_frames": int(r["usable_source_frames"]),
                           "support_frames": int(r["support_frames"]),
                           "valid_visibility_frames": int(r["valid_visibility_frames"]) if r["valid_visibility_frames"] else None,
                           "detected_curve_support_ratio": float(r["detected_curve_support_ratio"]) if r["detected_curve_support_ratio"] else None,
                           "reliability": None, "geometry": g, "geometry_scope": "whole_candidate_window_not_frame_addressable",
                           "flags": r["flags"].split(";"), "raw": r,
                           "reasons": ["single_clip_not_independent_revisits", "M3_reliability_not_supplied",
                                       "verified_prior_map_and_version_not_supplied", "map_element_correspondence_not_supplied",
                                       "visibility_denominator_unavailable", "centers_are_not_footprints" if occupancy else "pooled_curve_points_are_not_an_ordered_replacement_line"]})
    summary3 = read_json(stage3/"summary.json")
    summary5 = read_json(stage5/"summary.json")
    summary6 = read_json(stage6/"summary.json")
    check("stage3_summary_counts", summary3["candidate_count"] == len(candidates) and summary3["frame_evidence_rows"] == len(frames))
    check("stage5_summary_counts", summary5["vehicle_samples"] == len(tracks) and summary5["episodes"] == len(behaviors))
    check("stage6_summary_counts", summary6["behavior_candidate_pairs"] == len(associations) and summary6["frame_evidence_rows"] == len(association_frames))
    labels = {name: read_csv(root/"evaluation_labels"/(name+".csv")) for name in ("candidate_evidence", "behavior", "association")}
    problems = [x["check"] for x in checks if not x["passed"]]
    if problems:
        raise ValueError("delivery validation failed: "+", ".join(problems))
    return {"schema_version": "perception-intake-1.0", "scene_id": "scene-0980", "dataset_kind": "delivered_development_scene",
            "map": None, "map_updates": [], "write_eligible": False, "time_reference": "clip_relative_seconds_only",
            "coordinate_reference": {"frame": "upstream_global_xy", "units": "m", "city_id": None, "verified": False},
            "candidates": normalized, "frame_evidence": frames, "behaviors": behaviors, "vehicle_features": tracks,
            "associations": associations, "association_frame_evidence": association_frames,
            "summary": {"candidates": len(candidates), "families": dict(Counter(r["evidence_family"] for r in candidates)),
                        "frame_evidence_rows": len(frames), "vehicle_tracks": len({r["track_id"] for r in tracks}),
                        "vehicle_samples": len(tracks), "behavior_events": len(behaviors), "association_pairs": len(associations),
                        "associations": dict(Counter(r["association"] for r in associations)),
                        "independent_visits": 1, "labeled_rows": {k: sum(bool(r["truth"]) for r in v) for k, v in labels.items()},
                        "map_updates": 0},
            "validation": {"status": "PASS", "checks": checks, "scope": "file integrity and cross-file consistency; no accuracy or map validity claim"},
            "missing_requirements": ["verified prior map with element IDs and version", "explicit City/map token correspondence and absolute recording time",
                                     "M3 reliability / coverage / alignment output", "true independent revisits",
                                     "frame-specific ordered line geometry or object footprints"],
            "upstream_unavailable": manifest.get("unavailable", []),
            "limitations": ["All candidate geometry is MultiPoint pooled over a candidate window, not full lane boundaries or obstacle polygons.",
                            "Candidate coverage CSV counts evaluable vehicle-behavior pairs, not effective map visibility.",
                            "Vehicle trajectories are nuScenes annotation-derived, not evaluated detector output.",
                            "Per-candidate score and behavior labels use whole windows; this viewer is retrospective and must not drive online planning.",
                            "Synthetic_rule_checks belong to stage7; their hard-coded NUSCENES_GT_DERIVED tag is not reliable provenance."]}
