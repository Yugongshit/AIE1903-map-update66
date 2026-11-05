"""Invalid input must not reach map-maintenance geometry operations."""
import unittest
from map_update.geometry import validate_geometry


class PolygonValidationTests(unittest.TestCase):
    def polygon(self, ring, *holes):
        return {'type':'Polygon','coordinates':[ring,*holes]}

    def test_zero_area_is_rejected_even_with_nonzero_perimeter(self):
        with self.assertRaisesRegex(ValueError, 'zero area'):
            validate_geometry(self.polygon([[0,0],[1,0],[2,0],[0,0]]))

    def test_bow_tie_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_geometry(self.polygon([[0,0],[2,2],[0,2],[2,0],[0,0]]))

    def test_crossing_with_nonzero_signed_area_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'self-intersecting'):
            validate_geometry(self.polygon([[0,0],[4,3],[0,3],[2,0],[0,0]]))

    def test_nonadjacent_touch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'self-intersecting'):
            validate_geometry(self.polygon([[0,0],[3,0],[3,3],[1,0],[0,3],[0,0]]))

    def test_valid_concave_ring_both_orientations(self):
        ring=[[0,0],[4,0],[4,4],[2,2],[0,4],[0,0]]
        for r in (ring,list(reversed(ring))):
            self.assertEqual(validate_geometry(self.polygon(r)),self.polygon(r))

    def test_valid_hole_and_repeated_consecutive_vertex_are_preserved(self):
        p=self.polygon([[0,0],[5,0],[5,0],[5,5],[0,5],[0,0]],[[1,1],[1,2],[2,2],[2,1],[1,1]])
        self.assertIs(validate_geometry(p),p)

    def test_large_city_coordinates_do_not_cancel_area(self):
        ring=[[500000+x,10000000+y] for x,y in [[0,0],[1,0],[1,1],[0,1],[0,0]]]
        validate_geometry(self.polygon(ring))

    def test_lines_keep_existing_contract(self):
        p={'type':'LineString','coordinates':[[0,0],[1,0],[2,0]]}
        self.assertIs(validate_geometry(p),p)
