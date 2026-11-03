"""Small, dependency-free geometry helpers for local metric City coordinates."""

from math import atan2, degrees, hypot, isfinite


def validate_geometry(geometry, line_only=False):
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a GeoJSON-like geometry object")
    kind = geometry.get("type")
    if kind not in ({"LineString"} if line_only else {"LineString", "Polygon"}):
        raise ValueError("only LineString / Polygon geometry is supported")
    coords = geometry.get("coordinates")
    rings = [coords] if kind == "LineString" else coords
    if not isinstance(rings, list) or not rings:
        raise ValueError("geometry has no coordinates")
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < (2 if kind == "LineString" else 4):
            raise ValueError("geometry has too few points")
        for point in ring:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("MVP geometry must contain exactly x,y in metres")
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x) for x in point):
                raise ValueError("geometry coordinates must be finite numbers")
        if sum(hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(ring, ring[1:])) < 1e-6:
            raise ValueError("degenerate geometry")
        if kind == "Polygon" and ring[0] != ring[-1]:
            raise ValueError("polygon rings must be closed")
    return geometry


def point_segment_distance(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx + dy*dy
    t = max(0., min(1., ((p[0]-a[0])*dx + (p[1]-a[1])*dy)/length2)) if length2 else 0.
    return hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def samples(line, spacing=.5):
    result = [line[0]]
    for a, b in zip(line, line[1:]):
        count = max(1, int(hypot(b[0]-a[0], b[1]-a[1])/spacing)+1)
        result.extend([[a[0]+(b[0]-a[0])*i/count, a[1]+(b[1]-a[1])*i/count] for i in range(1, count+1)])
    return result


def line_distance(a, b):
    """Symmetric sampled Hausdorff distance; orientation / point density independent."""
    def directed(src, dst):
        return max(min(point_segment_distance(p, q, r) for q, r in zip(dst, dst[1:])) for p in samples(src))
    return max(directed(a, b), directed(b, a))


def direction_difference(a, b):
    def angle(line):
        return degrees(atan2(line[-1][1]-line[0][1], line[-1][0]-line[0][0]))
    d = abs(angle(a)-angle(b)) % 180
    return min(d, 180-d)


def overlap_ratio(a, b):
    dx, dy = a[-1][0]-a[0][0], a[-1][1]-a[0][1]
    length = hypot(dx, dy)
    if length < 1e-6:
        return 0.
    def interval(line):
        values = [(p[0]*dx+p[1]*dy)/length for p in line]
        return min(values), max(values)
    lo1, hi1 = interval(a)
    lo2, hi2 = interval(b)
    return max(0., min(hi1, hi2)-max(lo1, lo2))/max(hi1-lo1, hi2-lo2, 1e-6)


def lines_match(a, b, distance=1.5):
    return (line_distance(a, b) <= distance and direction_difference(a, b) <= 15
            and overlap_ratio(a, b) >= .6)
