from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from fuel.models import FuelStation

COORDS = [[-100.0 + i * 0.5, 40.0] for i in range(23)]
ROUTE_METERS = 900000


def osrm_response() -> Mock:
    response = Mock()
    response.json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "geometry": {"type": "LineString", "coordinates": COORDS},
                "distance": ROUTE_METERS,
            }
        ],
    }
    return response


def geo_result(lat: float, lon: float, country_code: str = "us") -> dict:
    return {
        "lat": lat,
        "lon": lon,
        "country_code": country_code,
        "display_name": "test location",
    }


class RouteAPITests(TestCase):
    def setUp(self):
        cache.clear()
        FuelStation.objects.create(
            opis_id=10, name="Origin Stop", address="Start Rd", city="Startville",
            state="NE", lat=40.0, lon=-100.0, price_per_gallon=Decimal("2.50"),
        )
        FuelStation.objects.create(
            opis_id=1, name="Cheap Stop", address="Exit 4", city="Lincoln",
            state="NE", lat=40.0, lon=-96.0, price_per_gallon=Decimal("3.00"),
        )
        FuelStation.objects.create(
            opis_id=2, name="Dear Stop", address="Exit 8", city="North Platte",
            state="NE", lat=40.0, lon=-92.0, price_per_gallon=Decimal("4.00"),
        )

    def test_post_with_coordinates_uses_one_routing_call(self):
        with patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ) as mock_get:
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(len(data["route"]["geometry"]["coordinates"]), 2)
        self.assertAlmostEqual(data["distance_miles"], ROUTE_METERS / 1609.344, places=2)
        self.assertEqual([s["name"] for s in data["stops"]], ["Origin Stop", "Dear Stop"])
        self.assertAlmostEqual(data["stops"][0]["miles_from_start"], 0.0, delta=1)
        self.assertEqual(data["stops"][0]["gallons_purchased"], 50.0)
        self.assertEqual(data["stops"][0]["cost"], "125.00")
        self.assertAlmostEqual(data["stops"][1]["miles_from_start"], 423.16, delta=2)
        self.assertAlmostEqual(data["stops"][1]["gallons_purchased"], 5.92, delta=0.05)
        self.assertAlmostEqual(float(data["stops"][1]["cost"]), 23.68, delta=0.15)
        self.assertAlmostEqual(float(data["total_fuel_cost"]), 148.68, delta=0.15)
        self.assertEqual(mock_get.call_count, 1)

    def test_cached_request_makes_no_external_calls(self):
        with patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ) as mock_get:
            payload = {"start": "40.0,-100.0", "finish": "40.0,-89.0"}
            self.client.post("/api/route/", payload, content_type="application/json")
            self.client.post("/api/route/", payload, content_type="application/json")
            self.assertEqual(mock_get.call_count, 1)

    def test_free_text_uses_two_geocode_calls_and_one_routing_call(self):
        with patch(
            "fuel.services.geocoding.providers.OpenMeteoClient.search",
            side_effect=[geo_result(40.0, -100.0), geo_result(40.0, -89.0)],
        ) as mock_search, patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ) as mock_get:
            response = self.client.post(
                "/api/route/",
                {"start": "Start City, NE", "finish": "End City, NE"},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_search.call_count, 2)
            self.assertEqual(mock_get.call_count, 1)

            self.client.post(
                "/api/route/",
                {"start": "Start City, NE", "finish": "End City, NE"},
                content_type="application/json",
            )
            self.assertEqual(mock_search.call_count, 2)
            self.assertEqual(mock_get.call_count, 1)

    def test_non_us_location_returns_404_location_not_found(self):
        with patch(
            "fuel.services.geocoding.providers.OpenMeteoClient.search",
            return_value=geo_result(48.8, 2.3, "fr"),
        ) as mock_search, self.assertLogs("fuel.exceptions", level="WARNING") as logs:
            response = self.client.post(
                "/api/route/",
                {"start": "Paris", "finish": "Lyon"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "location_not_found",
                    "message": "Location not found.",
                    "details": {"input": "Paris"},
                }
            },
        )
        self.assertEqual(mock_search.call_count, 1)
        self.assertTrue(any("domain_error" in line and "location_not_found" in line for line in logs.output))

    def test_missing_field_returns_400(self):
        response = self.client.post(
            "/api/route/", {"start": "40,-100"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "bad_request")
        self.assertEqual(response.json()["error"]["message"], "Bad request.")
        self.assertIn("finish", response.json()["error"]["details"])

    def test_outside_usa_coordinates_return_400_invalid_location(self):
        with self.assertLogs("fuel.exceptions", level="WARNING") as logs:
            response = self.client.post(
                "/api/route/",
                {"start": "51.5,-0.1", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "invalid_location",
                    "message": "Invalid location.",
                    "details": {
                        "input": "51.5,-0.1",
                        "reason": "outside_continental_us",
                        "supported_area": "continental USA (lat 24.4-49.4, lon -125.0 to -66.9)",
                    },
                }
            },
        )
        self.assertTrue(any("invalid_location" in line for line in logs.output))
        self.assertNotIn("Coordinates outside supported continental USA area.", response.content.decode())

    def test_map_page_renders_leaflet_and_stops(self):
        with patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ):
            response = self.client.get(
                "/api/route/map/?start=40.0,-100.0&finish=40.0,-89.0"
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "leaflet")
        self.assertContains(response, "Origin Stop")
        self.assertContains(response, "Dear Stop")

    def test_same_location_returns_zero_cost(self):
        with patch(
            "fuel.services.routing.requests.get",
            return_value=Mock(
                json=lambda: {
                    "code": "Ok",
                    "routes": [
                        {
                            "geometry": {"type": "LineString", "coordinates": COORDS[:2]},
                            "distance": 0,
                        }
                    ],
                }
            ),
        ) as mock_get:
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-100.0"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stops"], [])
        self.assertEqual(data["total_fuel_cost"], "0.00")
        self.assertEqual(mock_get.call_count, 1)


class RouteApiErrorContractTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_uniform_error_format(self):
        response = self.client.post(
            "/api/route/",
            {"start": "51.5,-0.1", "finish": "40.0,-89.0"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(set(body.keys()), {"error"})
        self.assertEqual(set(body["error"].keys()), {"code", "message", "details"})
        self.assertIsInstance(body["error"]["details"], dict)

    def test_route_not_feasible_returns_422(self):
        with patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "route_not_feasible",
                "message": "Route is not feasible.",
                "details": {"reason": "no_origin_station"},
            },
        )

    def test_routing_timeout_returns_504(self):
        import requests

        with patch(
            "fuel.services.routing.requests.get", side_effect=requests.Timeout("slow")
        ):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 504)
        self.assertEqual(
            response.json()["error"],
            {"code": "external_service_timeout", "message": "External service timed out.", "details": {}},
        )

    def test_routing_rate_limited_returns_503_with_retry_after(self):
        import requests

        err_response = Mock()
        err_response.status_code = 429
        err_response.headers = {"Retry-After": "120"}
        err_response.json.return_value = {}
        err_response.raise_for_status.side_effect = requests.HTTPError("429", response=err_response)

        with patch("fuel.services.routing.requests.get", return_value=err_response):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "external_rate_limited",
                "message": "External service rate limit exceeded.",
                "details": {},
            },
        )
        self.assertEqual(response["Retry-After"], "120")

    def test_routing_rate_limited_with_invalid_retry_after_omits_header(self):
        import requests

        err_response = Mock()
        err_response.status_code = 429
        err_response.headers = {"Retry-After": "not-a-valid-value"}
        err_response.json.return_value = {}
        err_response.raise_for_status.side_effect = requests.HTTPError("429", response=err_response)

        with patch("fuel.services.routing.requests.get", return_value=err_response):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "external_rate_limited")
        self.assertNotIn("Retry-After", response)

    def test_routing_bad_response_returns_502(self):
        bad = Mock()
        bad.raise_for_status.return_value = None
        bad.json.return_value = {"code": "Ok", "routes": []}

        with patch("fuel.services.routing.requests.get", return_value=bad):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "external_service_bad_response",
                "message": "External service returned an invalid response.",
                "details": {},
            },
        )

    @override_settings(DEBUG=True)
    def test_unexpected_error_returns_500_without_trace_even_in_debug(self):
        with patch("fuel.views.plan_route", side_effect=RuntimeError("secret boom")):
            response = self.client.post(
                "/api/route/",
                {"start": "40.0,-100.0", "finish": "40.0,-89.0"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["error"],
            {"code": "internal_error", "message": "Internal server error.", "details": {}},
        )
        body = response.content.decode()
        self.assertNotIn("Traceback", body)
        self.assertNotIn("secret boom", body)

    def test_map_missing_params_returns_400_with_code(self):
        response = self.client.get("/api/route/map/?start=40.0,-100.0")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "bad_request", status_code=400)
        self.assertContains(response, "Bad request.", status_code=400)
