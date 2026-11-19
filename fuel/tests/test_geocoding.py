from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from fuel.models import FuelStation
from fuel.services.geocoding import (
    GeocodingError,
    LocationNotInUS,
    PhotonClient,
    resolve_location,
)

PHOTON_PAYLOAD = {
    "features": [
        {
            "geometry": {"coordinates": [-95.221358, 36.5378651]},
            "properties": {
                "name": "Big Cabin",
                "city": "Big Cabin",
                "state": "Oklahoma",
                "country": "United States",
                "countrycode": "US",
            },
        }
    ]
}


def photon_response(payload: dict) -> Mock:
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class PhotonClientTests(TestCase):
    def setUp(self):
        cache.clear()
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.cache_file = f"{temp_dir.name}/geocode_cache.json"
        self.session = Mock()

    def make_client(self) -> PhotonClient:
        return PhotonClient(
            cache_file=self.cache_file, delay=0, autosave=False, session=self.session
        )

    def test_search_parses_geojson_and_sends_expected_params(self):
        self.session.get.return_value = photon_response(PHOTON_PAYLOAD)
        client = self.make_client()

        result = client.search("Big Cabin, OK, USA")

        self.assertEqual(result["lat"], 36.5378651)
        self.assertEqual(result["lon"], -95.221358)
        self.assertEqual(result["country_code"], "us")
        self.assertEqual(result["display_name"], "Big Cabin, Oklahoma, United States")
        self.session.get.assert_called_once_with(
            settings.PHOTON_URL,
            params={
                "q": "Big Cabin, OK, USA",
                "limit": 1,
                "lang": "en",
                "countrycode": "US",
                "bbox": "-125.0,24.4,-66.9,49.4",
            },
            timeout=(
                settings.PHOTON_CONNECT_TIMEOUT_SECONDS,
                settings.PHOTON_TIMEOUT_SECONDS,
            ),
        )
        self.session.headers.update.assert_called_once_with(
            {
                "User-Agent": settings.PHOTON_USER_AGENT,
                "Accept": "application/json",
            }
        )

    def test_search_caches_results_and_empty_results(self):
        self.session.get.side_effect = [
            photon_response(PHOTON_PAYLOAD),
            photon_response({"features": []}),
        ]
        client = self.make_client()

        first = client.search("Big Cabin, OK, USA")
        self.assertEqual(client.search("Big Cabin, OK, USA"), first)
        self.assertIsNone(client.search("Nowhere, OK, USA"))
        self.assertIsNone(client.search("Nowhere, OK, USA"))
        self.assertEqual(self.session.get.call_count, 2)

    def test_search_retries_and_honors_retry_after(self):
        throttled = Mock()
        throttled.raise_for_status.side_effect = requests.HTTPError(
            "429", response=Mock(status_code=429, headers={"Retry-After": "7"})
        )
        self.session.get.side_effect = [throttled, photon_response(PHOTON_PAYLOAD)]

        with patch("fuel.services.geocoding.time.sleep") as mock_sleep:
            result = self.make_client().search("Big Cabin, OK, USA")

        self.assertEqual(result["lat"], 36.5378651)
        self.assertEqual(self.session.get.call_count, 2)
        mock_sleep.assert_called_once_with(7.0)

    def test_search_raises_after_all_retries_fail(self):
        forbidden = Mock()
        forbidden.raise_for_status.side_effect = requests.HTTPError(
            "403", response=Mock(status_code=403, headers={})
        )
        self.session.get.return_value = forbidden

        with patch("fuel.services.geocoding.time.sleep"):
            with self.assertRaises(GeocodingError) as ctx:
                self.make_client().search("Big Cabin, OK, USA")

        self.assertIn("Big Cabin, OK, USA", str(ctx.exception))
        self.assertEqual(self.session.get.call_count, settings.PHOTON_RETRIES + 1)

    @patch(
        "fuel.services.geocoding.PhotonClient.search",
        return_value={
            "lat": 48.8,
            "lon": 2.3,
            "country_code": "fr",
            "display_name": "Paris, France",
        },
    )
    def test_resolve_location_rejects_non_us_result(self, mock_search):
        with self.assertRaises(LocationNotInUS):
            resolve_location("Paris")
        self.assertEqual(mock_search.call_count, 1)


class GeocodeCommandTests(TestCase):
    @override_settings(PHOTON_MAX_CONSECUTIVE_FAILURES=2)
    @patch("fuel.management.commands.geocode_fuel_stations.PhotonClient.search")
    def test_command_aborts_after_consecutive_failures(self, mock_search):
        mock_search.side_effect = GeocodingError("boom")
        for opis_id in range(1, 4):
            FuelStation.objects.create(
                opis_id=opis_id,
                name=f"Stop {opis_id}",
                address="Exit 1",
                city="Lincoln",
                state="NE",
                price_per_gallon="3.00",
            )

        with self.assertRaises(CommandError):
            call_command("geocode_fuel_stations", limit=3, stderr=StringIO())

        self.assertEqual(mock_search.call_count, 2)
