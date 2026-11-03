"""Controlled synthetic visits for integration checks; not experimental results."""

from copy import deepcopy

REFERENCE = {"frame": "City", "city_id": "synthetic-local-road", "units": "m"}
T0 = 1800000000000000000


def line(x, y0=0, y1=45):
    return {"type": "LineString", "coordinates": [[x, y0], [x, y1]]}


def base_map():
    return {"schema_version": "1.0", "map_id": "demo-road", "version": "v0",
            "coordinate_reference": deepcopy(REFERENCE), "map_role": "controlled_synthetic_prior",
            "provenance": "Synthetic integration fixture; no real-world permanence ground truth.",
            "elements": [{"element_id": f"line-{i}", "element_type": "lane_marking",
                          "geometry_city": line(x), "active": True,
                          "properties": {"marking_type": "solid_white"}} for i, x in enumerate([0., 3.5, 7., 10.5])]}


def record(identifier="r1", visit="visit-1", hour=0, kind="geometry_change", target="line-1", geometry=None):
    return {"record_id": identifier, "change_id": "change-"+str(target or kind), "map_id": "demo-road",
            "map_version": "v0", "coordinate_reference": deepcopy(REFERENCE), "visit_id": visit,
            "timestamp_ns": str(T0+int(hour*3600*1e9)), "map_element_id": target, "change_type": kind,
            "geometry_city": deepcopy(geometry if geometry is not None else line(4.3)),
            "observation": "supports_change", "evidence_score": 1., "valid_frame_count": 12,
            "support_frame_count": 11, "fully_observed": True, "source": "controlled_synthetic_fixture",
            "reliability": {"decision": "Suspected Map Change", "effective_coverage": .95,
                            "alignment_valid": True, "reason": "synthetic quality gate"}}


def demo_records():
    rows = []
    for i, hour in enumerate([0, 12, 26]):
        rows.append(record(f"shift-{i}", f"visit-{i}", hour, geometry=line(4.3 + .03*i)))
        occupancy = record(f"occupancy-{i}", f"visit-{i}", hour+.01, "occupancy", None,
                           {"type": "Polygon", "coordinates": [[[1., 13.], [3., 13.], [3., 17.], [1., 17.], [1., 13.]]]})
        rows.append(occupancy)
        ld = record(f"ld-{i}", f"visit-{i}", hour+.02, "line_discrepancy", "line-2")
        ld["geometry_city"] = None
        rows.append(ld)
        bad = record(f"bad-alignment-{i}", f"visit-{i}", hour+.03, target="line-0", geometry=line(.9))
        bad["reliability"]["alignment_valid"] = False
        rows.append(bad)
    # A fully observed return to prior clears occupancy, never mere silence.
    recovered = record("occupancy-cleared", "visit-3", 27, "occupancy", None,
                       {"type": "Polygon", "coordinates": [[[1., 13.], [3., 13.], [3., 17.], [1., 17.], [1., 13.]]]})
    recovered["observation"] = "supports_prior"
    recovered["reliability"]["decision"] = "No Significant Mismatch"
    recovered["support_frame_count"] = 0
    rows.append(recovered)
    return rows
