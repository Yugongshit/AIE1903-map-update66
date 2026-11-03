"""Canonical context/window adapter. The real M3 supplement uses reliability.py."""

from copy import deepcopy

from .schema import timestamp, require_text


def adapt_proposal(payload):
    """Join explicit context + ChangeRecord + M3 by identity and time window.

    No timestamp, visit, prior-map role, missing support count, or geometry is
    inferred from filenames, score, screenshots, or numeric change codes.
    geometry_city must already be a geometry object in the canonical contract.
    """
    context = payload["context"]
    for key in ("map_id", "map_version", "visit_id", "source"):
        require_text(context.get(key), key)
    if "coordinate_reference" not in context:
        raise ValueError("explicit coordinate_reference is required")
    rows = []
    for source in payload["records"]:
        r = deepcopy(source)
        for key in ("map_id", "map_version", "visit_id", "source", "coordinate_reference"):
            if key in r and r[key] != context[key]:
                raise ValueError(f"record/context mismatch: {key}")
            r[key] = deepcopy(context[key])
        for key in ("record_id", "change_id"):
            require_text(r.get(key), key)
        time = timestamp(r.get("timestamp_ns"))
        r["timestamp_ns"] = str(time)
        matches = []
        for rel in payload.get("reliability_records", []):
            if all(rel.get(key) == r.get(key) for key in ("map_id", "map_version", "visit_id", "change_id", "map_element_id", "coordinate_reference")):
                start, end = timestamp(rel["window_start_ns"]), timestamp(rel["window_end_ns"])
                if start > end:
                    raise ValueError("invalid reliability time window")
                if start <= time <= end:
                    matches.append(rel)
        if len(matches) > 1:
            raise ValueError("ambiguous M3 match: overlapping windows for one candidate")
        # Missing M3 is explicitly unknown; never convert an upstream score to M3 reliability.
        r["reliability"] = ({k: matches[0][k] for k in ("decision", "effective_coverage", "alignment_valid", "reason")}
                            if matches else None)
        rows.append(r)
    return rows
