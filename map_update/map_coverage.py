"""Coverage diagnostics from explicit source polygons, with no inferred lane width."""
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
import math

from .simulation_bridge import LANE_TYPES


class MapCoverage:
    def __init__(self, features):
        polygons = []
        self.lanes = 0
        for feature in features.values():
            if feature.get('type') not in LANE_TYPES:
                continue
            self.lanes += 1
            points = feature.get('polygon')
            if points is None or len(points) < 3:
                continue
            polygon = Polygon([p[:2] for p in points])
            if polygon.is_valid and not polygon.is_empty:
                polygons.append(polygon)
        self.count = len(polygons)
        self.region = unary_union(polygons)

    def actor(self, actor):
        if not self.count:
            return dict(status='unavailable', reason='source_lane_polygons_missing')
        position = actor['position_m']
        dims = actor.get('dimensions_m', {})
        length, width = dims.get('length'), dims.get('width')
        result = dict(status='available', position_covered=self.region.covers(Point(position)),
                      polygon_lane_count=self.count, total_lane_count=self.lanes,
                      scope='union_of_valid_explicit_source_lane_polygons')
        if length is not None and width is not None and length > 0 and width > 0:
            c, s = math.cos(actor['heading_rad']), math.sin(actor['heading_rad'])
            corners = [[position[0]+c*x-s*y, position[1]+s*x+c*y]
                       for x,y in [(length/2,width/2),(-length/2,width/2),
                                   (-length/2,-width/2),(length/2,-width/2)]]
            result['footprint_covered'] = self.region.covers(Polygon(corners))
        else:
            result['footprint_covered'] = None
        return result
