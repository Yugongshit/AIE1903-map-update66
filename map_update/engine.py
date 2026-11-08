"""Conservative rule baseline, not a learned permanence estimator."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite

from .geometry import direction_difference, line_distance, lines_match, overlap_ratio
from .schema import validate_map, validate_records


@dataclass(frozen=True)
class Policy:
    min_visits: int = 3
    min_visit_gap_s: float = 3600
    min_span_s: float = 86400
    min_valid_frames: int = 3
    min_support_ratio: float = .7
    min_coverage: float = .7
    geometry_stability_m: float = .35
    min_geometry_change_m: float = .5
    association_distance_m: float = 1.5
    temporary_stale_after_s: float = 3600

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError(f"invalid policy field {name}")
        if type(self.min_visits) is not int or self.min_visits < 2 or type(self.min_valid_frames) is not int:
            raise ValueError("min_visits >= 2 and min_valid_frames must be integers")
        if self.min_coverage > 1 or self.min_support_ratio > 1:
            raise ValueError("policy ratios must be <= 1")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def quality_reason(r, policy):
    rel = r.get("reliability")
    if rel is None:
        return "missing_reliability"
    if not rel["alignment_valid"]:
        return "invalid_alignment"
    if rel["effective_coverage"] < policy.min_coverage:
        return "insufficient_coverage"
    expected = "No Significant Mismatch" if r["observation"] == "supports_prior" else "Suspected Map Change"
    if rel["decision"] != expected:
        return "reliability_requires_verification"
    if r["valid_frame_count"] < policy.min_valid_frames:
        return "too_few_valid_frames"
    if r["observation"] == "unknown":
        return "unknown_observation"
    if r["observation"] == "supports_prior" and not r.get("fully_observed", False):
        return "recovery_requires_explicit_full_observation"
    if r["observation"] == "supports_change" and r["support_frame_count"]/r["valid_frame_count"] < policy.min_support_ratio:
        return "low_support_ratio"
    return None


def independent_visits(rows, policy):
    """Latest record per visit, then enforce temporal separation across visit IDs."""
    latest = {r["visit_id"]: r for r in rows}
    selected = []
    for r in sorted(latest.values(), key=lambda row: int(row["timestamp_ns"])):
        if not selected or (int(r["timestamp_ns"])-int(selected[-1]["timestamp_ns"]))/1e9 >= policy.min_visit_gap_s:
            selected.append(r)
    return selected


class Maintainer:
    def __init__(self, base, policy=None):
        self.base = validate_map(base)
        self.current = deepcopy(self.base)
        self.policy = policy or Policy()
        self.events = []
        self.audit = []
        self.versions = [{"version": self.base["version"], "parent": None, "map": deepcopy(self.base), "patch": None}]
        self.timeline = []

    def find_event(self, r):
        target, kind = r.get("map_element_id"), r["change_type"]
        for e in self.events:
            if r["change_id"] in e["change_ids"] and (target, kind) != (e["map_element_id"], e["change_type"]):
                raise ValueError("change_id reused for a different target/type")
            if (target, kind) == (e["map_element_id"], e["change_type"]):
                # A road element can contain several independent obstacles.
                # Only an explicit occupancy identity may accumulate or clear
                # that event; sharing a lane does not establish identity.
                if r["change_id"] in e["change_ids"] or (kind != "occupancy" and target is not None):
                    return e
        if kind == "add" and (r.get("geometry_city") or {}).get("type") == "LineString":
            candidates = []
            for e in self.events:
                if e["change_type"] == "add" and (e.get("geometry_city") or {}).get("type") == "LineString":
                    if lines_match(e["geometry_city"]["coordinates"], r["geometry_city"]["coordinates"], self.policy.association_distance_m):
                        candidates.append(e)
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise ValueError("ambiguous new-feature association; supply disambiguated change IDs/geometries")
        e = {"event_id": f"event-{len(self.events)+1:04d}", "change_ids": [], "map_element_id": target,
             "change_type": kind, "status": "insufficient_evidence", "reason": "no_valid_evidence",
             "first_seen_ns": r["timestamp_ns"], "last_seen_ns": r["timestamp_ns"],
             "last_valid_ns": None, "geometry_city": None, "record_ids": [], "support_rows": [],
             "independent_visits": 0, "span_s": 0., "applied_version": None}
        self.events.append(e)
        return e

    def update(self, r):
        e = self.find_event(r)
        if r["change_id"] not in e["change_ids"]:
            e["change_ids"].append(r["change_id"])
        e["record_ids"].append(r["record_id"])
        e["last_seen_ns"] = r["timestamp_ns"]
        rejected = quality_reason(r, self.policy)
        self.audit.append({"record_id": r["record_id"], "event_id": e["event_id"], "source": r["source"],
                           "accepted": rejected is None, "reason": rejected or "quality_gate_passed"})
        if rejected:
            e["last_rejected_reason"] = rejected
            if not e["support_rows"] and not e["applied_version"]:
                e["reason"] = rejected
            return
        e["last_valid_ns"] = r["timestamp_ns"]
        if r["observation"] == "supports_prior":
            e["support_rows"] = []
            e["independent_visits"], e["span_s"] = 0, 0.
            e["status"] = "review_required" if e["applied_version"] else "resolved"
            e["reason"] = "contradiction_after_update_review_or_rollback" if e["applied_version"] else "explicit_prior_observed"
            return
        # A different geometry after a patch is an explicit review item, not a silent overwrite.
        if e["applied_version"]:
            e["reason"] = "already_applied_new_evidence_requires_review"
            e["status"] = "review_required"
            return
        if r.get("geometry_city") is not None:
            e["geometry_city"] = deepcopy(r["geometry_city"])
        e["support_rows"].append(r)
        visits = independent_visits(e["support_rows"], self.policy)
        e["independent_visits"] = len(visits)
        e["span_s"] = (int(visits[-1]["timestamp_ns"])-int(visits[0]["timestamp_ns"]))/1e9 if visits else 0.
        if e["change_type"] == "occupancy" or any(x.get("semantic_hint") == "temporary" for x in e["support_rows"]):
            e["status"], e["reason"] = "temporary", "temporary_layer_only_not_permanence_proof"
            return
        if len(visits) < self.policy.min_visits or e["span_s"] < self.policy.min_span_s:
            e["status"], e["reason"] = "insufficient_evidence", "need_independent_revisits_and_time_span"
            return
        e["status"] = "long_term_candidate"
        reason = self.write_gate(e, visits)
        if reason:
            e["reason"] = reason
            return
        self.apply(e, visits)

    def write_gate(self, e, visits):
        kind, target = e["change_type"], e["map_element_id"]
        if kind == "line_discrepancy":
            return "discrepancy_is_not_a_structural_change_type"
        original = next((x for x in self.base["elements"] if x["element_id"] == target), None)
        current = next((x for x in self.current["elements"] if x["element_id"] == target), None)
        if kind != "add":
            if not original or not current or not current["active"]:
                return "target_missing_or_inactive"
            if current != original:
                return "target_changed_since_baseline"
            if current["element_type"] != "lane_marking" or current["geometry_city"]["type"] != "LineString":
                return "MVP_writes_lane_markings_only"
        if kind == "delete":
            return None if all(x.get("fully_observed") is True for x in visits) else "deletion_requires_explicit_full_visibility_each_visit"
        if kind == "marking_change":
            markings = {x.get("marking_type") for x in visits}
            if len(markings) != 1 or next(iter(markings)) in {None, "", "unknown"}:
                return "missing_or_inconsistent_marking_type"
            if current["properties"].get("marking_type") == next(iter(markings)):
                return "marking_already_matches"
            return None
        geometries = [x.get("geometry_city") for x in visits]
        if any(g is None or g["type"] != "LineString" for g in geometries):
            return "missing_observed_line_geometry"
        lines = [g["coordinates"] for g in geometries]
        if any(line_distance(a, b) > self.policy.geometry_stability_m for i, a in enumerate(lines) for b in lines[i+1:]):
            return "unstable_geometry_across_visits"
        if kind == "geometry_change":
            prior = current["geometry_city"]["coordinates"]
            if any(overlap_ratio(prior, line) < .8 or direction_difference(prior, line) > 30 for line in lines):
                return "partial_or_incompatible_geometry_do_not_replace_whole_feature"
            if line_distance(prior, lines[-1]) < self.policy.min_geometry_change_m:
                return "geometry_already_matches"
        if kind == "add":
            for element in self.current["elements"]:
                if element["active"] and element["geometry_city"]["type"] == "LineString":
                    if lines_match(element["geometry_city"]["coordinates"], lines[-1], self.policy.min_geometry_change_m):
                        return "addition_duplicates_existing_feature"
        return None

    def apply(self, e, visits):
        kind, target = e["change_type"], e["map_element_id"]
        before = next((deepcopy(x) for x in self.current["elements"] if x["element_id"] == target), None)
        if kind == "add":
            target = "new-" + fingerprint({"map": self.base["map_id"], "event": e["event_id"], "geometry": visits[-1]["geometry_city"]})
            after = {"element_id": target, "element_type": "lane_marking", "geometry_city": deepcopy(visits[-1]["geometry_city"]),
                     "active": True, "properties": {"marking_type": visits[-1].get("marking_type", "unknown")}}
            self.current["elements"].append(after)
        else:
            after = next(x for x in self.current["elements"] if x["element_id"] == target)
            if kind == "delete":
                after["active"] = False
            elif kind == "geometry_change":
                after["geometry_city"] = deepcopy(visits[-1]["geometry_city"])
            elif kind == "marking_change":
                after["properties"]["marking_type"] = visits[-1]["marking_type"]
        parent = self.current["version"]
        patch = {"event_id": e["event_id"], "operation": kind, "element_id": target, "before": before,
                 "after": deepcopy(after), "evidence_record_ids": [x["record_id"] for x in visits],
                 "timestamp_ns": visits[-1]["timestamp_ns"], "reason": "configured_revisit_and_geometry_gates_passed"}
        version = f"{self.base['version']}.u{len(self.versions):04d}-{fingerprint(patch)[:8]}"
        self.current["version"] = version
        self.versions.append({"version": version, "parent": parent, "map": deepcopy(self.current), "patch": patch})
        e["status"], e["reason"], e["applied_version"] = "applied", "long_term_rule_passed_not_permanence_guarantee", version

    def public_events(self, now):
        result = []
        for e in self.events:
            item = {k: deepcopy(v) for k, v in e.items() if k != "support_rows"}
            item["stale"] = bool(e["last_valid_ns"] is not None and (now-int(e["last_valid_ns"]))/1e9 > self.policy.temporary_stale_after_s)
            item["clearance_confirmed"] = e["status"] == "resolved"
            result.append(item)
        return result

    def run(self, records, as_of_ns=None):
        if self.audit:
            raise ValueError("use a fresh Maintainer for each complete replay")
        rows = validate_records(records, self.base)
        if as_of_ns is None:
            as_of_ns = max((int(r["timestamp_ns"]) for r in rows), default=0)
        from .schema import timestamp
        as_of_ns = timestamp(as_of_ns)
        rows = [r for r in rows if int(r["timestamp_ns"]) <= as_of_ns]
        for r in rows:
            self.update(r)
            self.timeline.append({"timestamp_ns": r["timestamp_ns"], "record_id": r["record_id"],
                                  "version": self.current["version"], "events": self.public_events(int(r["timestamp_ns"]))})
        events = self.public_events(as_of_ns)
        if not rows or as_of_ns > int(rows[-1]["timestamp_ns"]):
            self.timeline.append({"timestamp_ns": str(as_of_ns), "record_id": "as-of", "version": self.current["version"],
                                  "events": deepcopy(events)})
        return {"schema_version": "1.0", "baseline": self.base, "current_map": self.current,
                "as_of_ns": str(as_of_ns), "policy": asdict(self.policy), "events": events, "evidence": rows,
                "audit": self.audit, "versions": self.versions, "timeline": self.timeline,
                "planning_view": {"map_version": self.current["version"], "coordinate_reference": self.base["coordinate_reference"],
                                  "temporary_events": [e for e in events if e["status"] == "temporary"],
                                  "review_events": [e for e in events if e["status"] in {"long_term_candidate", "review_required", "insufficient_evidence"}],
                                  "note": "No lane closures or traversability inferred. Stale is not cleared. Planning must independently verify obstacles."}}


def rollback(bundle, version):
    """Append a new version restoring an earlier snapshot; never erase history."""
    result = deepcopy(bundle)
    target = next((x for x in result["versions"] if x["version"] == version), None)
    if target is None:
        raise ValueError(f"unknown rollback version: {version}")
    parent = result["current_map"]["version"]
    restored = deepcopy(target["map"])
    new_version = f"{result['baseline']['version']}.r{len(result['versions']):04d}"
    restored["version"] = new_version
    result["current_map"] = restored
    result["versions"].append({"version": new_version, "parent": parent, "map": deepcopy(restored),
                               "patch": {"operation": "rollback", "restored_from": version, "reason": "explicit_user_rollback"}})
    for e in result["events"]:
        if e["applied_version"]:
            e["status"], e["reason"] = "review_required", "rollback_requires_evidence_reassessment"
    result["planning_view"]["map_version"] = new_version
    result["planning_view"]["review_events"] = [deepcopy(e) for e in result["events"] if e["status"] in {"review_required", "long_term_candidate", "insufficient_evidence"}]
    result["timeline"].append({"timestamp_ns": result["as_of_ns"], "record_id": "rollback", "version": new_version, "events": deepcopy(result["events"])})
    return result
