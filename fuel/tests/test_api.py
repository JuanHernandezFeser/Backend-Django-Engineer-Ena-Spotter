from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase

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
        self.assertEqual(len(data["stops"]), 1)
        self.assertEqual(data["stops"][0]["name"], "Cheap Stop")
        self.assertAlmostEqual(data["stops"][0]["miles_from_start"], 212.0, delta=2)
        self.assertGreater(float(data["total_fuel_cost"]), 0)
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
            "fuel.services.geocoding.OpenMeteoClient.search",
            side_effect=[geo_result(40.7, -74.0), geo_result(34.0, -118.0)],
        ) as mock_search, patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ) as mock_get:
            response = self.client.post(
                "/api/route/",
                {"start": "New York, NY", "finish": "Los Angeles, CA"},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_search.call_count, 2)
            self.assertEqual(mock_get.call_count, 1)

            self.client.post(
                "/api/route/",
                {"start": "New York, NY", "finish": "Los Angeles, CA"},
                content_type="application/json",
            )
            self.assertEqual(mock_search.call_count, 2)
            self.assertEqual(mock_get.call_count, 1)

    def test_non_us_location_returns_400(self):
        with patch(
            "fuel.services.geocoding.OpenMeteoClient.search",
            return_value=geo_result(48.8, 2.3, "fr"),
        ) as mock_search:
            response = self.client.post(
                "/api/route/",
                {"start": "Paris", "finish": "Lyon"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("USA", response.json()["error"])
        self.assertEqual(mock_search.call_count, 1)

    def test_missing_field_returns_400(self):
        response = self.client.post("/api/route/", {"start": "40,-100"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json())

    def test_outside_usa_coordinates_return_400(self):
        response = self.client.post(
            "/api/route/",
            {"start": "51.5,-0.1", "finish": "40.0,-89.0"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_map_page_renders_leaflet_and_stops(self):
        with patch(
            "fuel.services.routing.requests.get", return_value=osrm_response()
        ):
            response = self.client.get(
                "/api/route/map/?start=40.0,-100.0&finish=40.0,-89.0"
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "leaflet")
        self.assertContains(response, "Cheap Stop")
