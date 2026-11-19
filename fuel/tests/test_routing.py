from django.test import SimpleTestCase

from fuel.services.routing import simplify


class SimplifyTests(SimpleTestCase):
    def test_straight_line_collapses_to_endpoints(self):
        points = [(float(i), 0.0) for i in range(100)]
        self.assertEqual(simplify(points, 0.0001), [points[0], points[-1]])

    def test_sharp_turn_is_kept(self):
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (2.0, 1.0)]
        result = simplify(points, 0.0001)
        self.assertEqual(result, points)
