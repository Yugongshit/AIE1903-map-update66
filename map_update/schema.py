"""Explicit input contract: reject ambiguous units, unknown versions and invalid data."""

from copy import deepcopy
from math import isfinite

from .geometry import validate_geometry

CHANGE_TYPES = {"geometry_change", "marking_change", "add", "delete", "occupancy", "line_discrepancy"}
DECISIONS = {"No Significant Mismatch", "Suspected Map Change", "Needs Further Verification", "Insufficient Evidence"}


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def timestamp(value):
    # Nanosecond timestamps must not pass through floating-point / JS numbers.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("timestamp_ns must be an integer or decimal string")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("timestamp_ns must be a nonnegative decimal string")
    result = int(value)
    if result < 0:
        raise ValueError("timestamp_ns must be nonnegative")
    return result


def fraction(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a finite number in [0,1]")
    return value


def validate_reference(ref):
    if not isinstance(ref, dict) or ref.get("frame") != "City" or ref.get("units") != "m":
        raise ValueError("coordinate_reference must specify frame=City and units=m")
    require_text(ref.get("city_id"), "city_id")


def validate_map(data):
    data = deepcopy(data)
    if data.get("schema_version") != "1.0":
        raise ValueError("unsupported map schema_version")
    for key in ("map_id", "version"):
        require_text(data.get(key), key)
    validate_reference(data.get("coordinate_reference"))
    if data.get("map_role") not in {"verified_prior", "controlled_synthetic_prior"}:
        raise ValueError("verify prior-map semantics first; current_base_map cannot be silently used as prior")
    if not isinstance(data.get("elements"), list):
        raise ValueError("elements must be a list")
    ids = set()
    for e in data["elements"]:
        key = require_text(e.get("element_id"), "element_id")
        if key in ids:
            raise ValueError(f"duplicate map element {key}")
        ids.add(key)
        require_text(e.get("element_type"), "element_type")
        validate_geometry(e.get("geometry_city"))
        if type(e.get("active", True)) is not bool or not isinstance(e.get("properties", {}), dict):
            raise ValueError("invalid active or properties field")
        e.setdefault("active", True)
        e.setdefault("properties", {})
    return data


def validate_records(rows, base):
    if not isinstance(rows, list):
        raise ValueError("observations must be a JSON list")
    result, seen = [], {}
    for raw in rows:
        r = deepcopy(raw)
        for key in ("record_id", "change_id", "visit_id", "map_id", "map_version", "source"):
            require_text(r.get(key), key)
        if r.get("map_id") != base["map_id"] or r.get("map_version") != base["version"]:
            raise ValueError(f"{r['record_id']}: map ID/version mismatch with input baseline")
        if r.get("coordinate_reference") != base["coordinate_reference"]:
            raise ValueError(f"{r['record_id']}: coordinate reference mismatch")
        r["timestamp_ns"] = str(timestamp(r.get("timestamp_ns")))
        if r.get("change_type") not in CHANGE_TYPES:
            raise ValueError("unsupported change_type; do not guess LD/OCC semantics")
        if r.get("observation") not in {"supports_change", "supports_prior", "unknown"}:
            raise ValueError("observation must be supports_change / supports_prior / unknown")
        target = r.get("map_element_id")
        if target is not None:
            require_text(target, "map_element_id")
        if r["change_type"] in {"geometry_change", "marking_change", "delete"} and target is None:
            raise ValueError("existing-map changes require map_element_id")
        if r["change_type"] == "add" and target is not None:
            raise ValueError("add must have null map_element_id")
        if r.get("geometry_city") is not None:
            validate_geometry(r["geometry_city"])
        for key in ("marking_type", "semantic_hint"):
            if r.get(key) is not None:
                require_text(r[key], key)
        for key in ("valid_frame_count", "support_frame_count"):
            if type(r.get(key)) is not int or r[key] < 0:
                raise ValueError(f"{key} must be a nonnegative integer")
        if r["support_frame_count"] > r["valid_frame_count"]:
            raise ValueError("support_frame_count exceeds valid_frame_count")
        if r.get("evidence_score") is not None:
            fraction(r["evidence_score"], "evidence_score")
        if type(r.get("fully_observed", False)) is not bool:
            raise ValueError("fully_observed must be boolean")
        reliability = r.get("reliability")
        if reliability is not None:
            if not isinstance(reliability, dict) or reliability.get("decision") not in DECISIONS:
                raise ValueError("invalid reliability decision")
            fraction(reliability.get("effective_coverage"), "effective_coverage")
            if type(reliability.get("alignment_valid")) is not bool:
                raise ValueError("alignment_valid must be boolean")
        if r["record_id"] in seen:
            if seen[r["record_id"]] != r:
                raise ValueError("record_id reused with different content")
            continue  # Exact duplicates are idempotent, never extra evidence.
        seen[r["record_id"]] = r
        result.append(r)
    return sorted(result, key=lambda r: (int(r["timestamp_ns"]), r["record_id"]))
