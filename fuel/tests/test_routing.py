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


class RoutingErrorTests(SimpleTestCase):
    def _osrm_ok(self, code="Ok", routes=None, distance=1000, coords=None):
        import unittest.mock

        if routes is None:
            routes = [
                {
                    "geometry": {"type": "LineString", "coordinates": coords or [[-100.0, 40.0], [-99.0, 40.0]]},
                    "distance": distance,
                }
            ]
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": code, "routes": routes}
        return response

    def test_unroutable_no_segment_returns_route_not_feasible(self):
        from unittest.mock import Mock, patch

        import requests

        from fuel.exceptions import RouteNotFeasibleError
        from fuel.services.routing import get_route

        response = Mock()
        response.status_code = 400
        response.headers = {}
        response.json.return_value = {"code": "NoSegment", "message": "Could not find a matching segment for coordinate"}
        http_error = requests.HTTPError("400", response=response)
        response.raise_for_status.side_effect = http_error

        with patch("fuel.services.routing.requests.get", return_value=response):
            with self.assertRaises(RouteNotFeasibleError) as ctx:
                get_route(40.0, -100.0, 40.0, -89.0)
        self.assertEqual(ctx.exception.code, "route_not_feasible")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_timeout_returns_504(self):
        from unittest.mock import patch

        import requests

        from fuel.exceptions import ExternalServiceTimeoutError
        from fuel.services.routing import get_route

        with patch("fuel.services.routing.requests.get", side_effect=requests.Timeout("t")):
            with self.assertRaises(ExternalServiceTimeoutError) as ctx:
                get_route(40.0, -100.0, 40.0, -89.0)
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.code, "external_service_timeout")

    def test_rate_limited_returns_503_with_retry_after(self):
        from unittest.mock import Mock, patch

        import requests

        from fuel.exceptions import ExternalServiceRateLimitedError
        from fuel.services.routing import get_route

        response = Mock()
        response.status_code = 429
        response.headers = {"Retry-After": "60"}
        response.json.return_value = {}
        response.raise_for_status.side_effect = requests.HTTPError("429", response=response)

        with patch("fuel.services.routing.requests.get", return_value=response):
            with self.assertRaises(ExternalServiceRateLimitedError) as ctx:
                get_route(40.0, -100.0, 40.0, -89.0)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.code, "external_rate_limited")
        self.assertEqual(ctx.exception.retry_after, "60")

    def test_connection_error_returns_503_unavailable(self):
        from unittest.mock import patch

        import requests

        from fuel.exceptions import ExternalServiceUnavailableError
        from fuel.services.routing import get_route

        with patch("fuel.services.routing.requests.get", side_effect=requests.ConnectionError("c")):
            with self.assertRaises(ExternalServiceUnavailableError) as ctx:
                get_route(40.0, -100.0, 40.0, -89.0)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.code, "external_service_unavailable")

    def test_invalid_response_returns_502(self):
        from unittest.mock import patch

        from fuel.exceptions import ExternalServiceBadResponseError
        from fuel.services.routing import get_route

        # Successful OSRM code but missing routes -> invalid upstream response.
        with patch("fuel.services.routing.requests.get", return_value=self._osrm_ok(routes=[])):
            with self.assertRaises(ExternalServiceBadResponseError) as ctx:
                get_route(40.0, -100.0, 40.0, -89.0)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.code, "external_service_bad_response")
