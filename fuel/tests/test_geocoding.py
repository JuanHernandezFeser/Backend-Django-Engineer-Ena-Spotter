from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from fuel.models import FuelStation
from fuel.services.geocoding.offline import GeocodingError, PhotonClient
from fuel.services.geocoding.providers import OpenMeteoClient
from fuel.services.geocoding.request import get_text_geocoder, resolve_location

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

OPEN_METEO_PAYLOAD = {
    "results": [
        {
            "name": "Paris",
            "latitude": 48.85341,
            "longitude": 2.3488,
            "country_code": "FR",
            "admin1": "Île-de-France",
            "country": "France",
        },
        {
            "name": "New York",
            "latitude": 40.71427,
            "longitude": -74.00597,
            "country_code": "US",
            "admin1": "New York",
            "country": "United States",
        },
    ]
}


def photon_response(payload: dict) -> Mock:
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def open_meteo_response(payload: dict) -> Mock:
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class OpenMeteoClientTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("fuel.services.geocoding.providers.requests.get")
    def test_search_parses_first_us_result_and_sends_expected_params(self, mock_get):
        mock_get.return_value = open_meteo_response(OPEN_METEO_PAYLOAD)

        result = OpenMeteoClient().search("New York, NY")

        self.assertEqual(result["lat"], 40.71427)
        self.assertEqual(result["lon"], -74.00597)
        self.assertEqual(result["country_code"], "us")
        self.assertEqual(result["display_name"], "New York, United States")
        mock_get.assert_called_once_with(
            settings.OPEN_METEO_URL,
            params={"name": "New York, NY", "count": 20,
                    "language": "en", "format": "json"},
            headers={
                "User-Agent": settings.PHOTON_USER_AGENT,
                "Accept": "application/json",
            },
            timeout=settings.OPEN_METEO_TIMEOUT_SECONDS,
        )

    @patch("fuel.services.geocoding.providers.requests.get")
    def test_search_caches_empty_results_and_filters_non_us(self, mock_get):
        mock_get.return_value = open_meteo_response(
            {"results": [OPEN_METEO_PAYLOAD["results"][0]]}
        )
        client = OpenMeteoClient()

        self.assertIsNone(client.search("Paris"))
        self.assertIsNone(client.search("Paris"))
        self.assertEqual(mock_get.call_count, 1)

    @patch("fuel.services.geocoding.providers.requests.get")
    def test_search_falls_back_to_city_name_and_matches_state(self, mock_get):
        mock_get.side_effect = [
            open_meteo_response({"results": []}),
            open_meteo_response({
                "results": [
                    {
                        "name": "Aberdeen", "latitude": 45.4647,
                        "longitude": -98.48648, "country_code": "US",
                        "admin1": "South Dakota", "country": "United States",
                    },
                    {
                        "name": "Aberdeen", "latitude": 39.50956,
                        "longitude": -76.16412, "country_code": "US",
                        "admin1": "Maryland", "country": "United States",
                    },
                ]
            }),
        ]

        result = OpenMeteoClient().search("Aberdeen, MD")

        self.assertEqual(result["lat"], 39.50956)
        self.assertEqual(result["display_name"], "Aberdeen, Maryland, United States")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"]["name"], "Aberdeen")

    @patch("fuel.services.geocoding.providers.OpenMeteoClient.search")
    def test_resolve_location_uses_open_meteo_by_default(self, mock_search):
        mock_search.return_value = {
            "lat": 40.71427,
            "lon": -74.00597,
            "country_code": "us",
            "display_name": "New York, New York, United States",
        }

        resolved = resolve_location("New York, NY")

        self.assertEqual(resolved, (40.71427, -74.00597,
                                    "New York, New York, United States"))
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(resolve_location("New York, NY"), resolved)
        self.assertEqual(mock_search.call_count, 1)

    @override_settings(TEXT_GEOCODER="photon")
    def test_request_time_geocoder_rejects_photon_provider(self):
        from fuel.exceptions import ServerMisconfiguredError

        with self.assertRaises(ServerMisconfiguredError) as ctx:
            get_text_geocoder()
        self.assertEqual(ctx.exception.code, "server_misconfigured")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_get_text_geocoder_rejects_unknown_provider_as_server_misconfiguration(self):
        from fuel.exceptions import ServerMisconfiguredError

        with override_settings(TEXT_GEOCODER="nope"):
            with self.assertRaises(ServerMisconfiguredError) as ctx:
                get_text_geocoder()
        self.assertEqual(ctx.exception.code, "server_misconfigured")
        self.assertEqual(ctx.exception.status_code, 500)


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

        with patch("fuel.services.geocoding.offline.time.sleep") as mock_sleep:
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

        with patch("fuel.services.geocoding.offline.time.sleep"):
            with self.assertRaises(GeocodingError) as ctx:
                self.make_client().search("Big Cabin, OK, USA")

        self.assertIn("Big Cabin, OK, USA", str(ctx.exception))
        self.assertEqual(self.session.get.call_count, settings.PHOTON_RETRIES + 1)

    @patch(
        "fuel.services.geocoding.providers.OpenMeteoClient.search",
        return_value={
            "lat": 48.8,
            "lon": 2.3,
            "country_code": "fr",
            "display_name": "Paris, France",
        },
    )
    def test_resolve_location_non_us_text_returns_location_not_found(self, mock_search):
        from fuel.exceptions import LocationNotFoundError as DomainLocationNotFoundError

        with self.assertRaises(DomainLocationNotFoundError) as ctx:
            resolve_location("Paris")
        self.assertEqual(ctx.exception.code, "location_not_found")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.details, {"input": "Paris"})
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

    @patch("fuel.management.commands.geocode_fuel_stations.OpenMeteoClient.search")
    def test_command_geocodes_city_with_open_meteo(self, mock_search):
        mock_search.return_value = {
            "lat": 33.4484,
            "lon": -112.074,
            "country_code": "us",
            "display_name": "Phoenix, Arizona, United States",
        }
        phoenix = FuelStation.objects.create(
            opis_id=1, name="AZ Stop", address="Exit 1", city="Phoenix",
            state="AZ", price_per_gallon="3.00",
        )
        other = FuelStation.objects.create(
            opis_id=2, name="IL Stop", address="Exit 2", city="Phoenix",
            state="IL", price_per_gallon="3.50",
        )

        call_command("geocode_fuel_stations", city=["Phoenix,AZ"])

        phoenix.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(phoenix.lat, 33.4484)
        self.assertEqual(phoenix.lon, -112.074)
        self.assertIsNone(other.lat)
        mock_search.assert_called_once_with("Phoenix, AZ")

    @patch("fuel.management.commands.geocode_fuel_stations.OpenMeteoClient.search")
    def test_command_geocodes_all_pending_cities(self, mock_search):
        results = {
            "Phoenix, AZ": {
                "lat": 33.4484, "lon": -112.074, "country_code": "us",
                "display_name": "Phoenix, Arizona, United States",
            },
            "Houston, TX": {
                "lat": 29.7604, "lon": -95.3698, "country_code": "us",
                "display_name": "Houston, Texas, United States",
            },
        }
        mock_search.side_effect = lambda query: results[query]
        phoenix = FuelStation.objects.create(
            opis_id=1, name="PHX", address="Exit 1", city="Phoenix",
            state="AZ", price_per_gallon="3.00",
        )
        houston = FuelStation.objects.create(
            opis_id=2, name="HOU", address="Exit 2", city="Houston",
            state="TX", price_per_gallon="3.10",
        )
        already = FuelStation.objects.create(
            opis_id=3, name="DEN", address="Exit 3", city="Denver",
            state="CO", lat=39.7, lon=-104.9, price_per_gallon="3.20",
        )

        call_command("geocode_fuel_stations", all_cities=True)

        phoenix.refresh_from_db()
        houston.refresh_from_db()
        already.refresh_from_db()
        self.assertEqual((phoenix.lat, phoenix.lon), (33.4484, -112.074))
        self.assertEqual((houston.lat, houston.lon), (29.7604, -95.3698))
        self.assertEqual((already.lat, already.lon), (39.7, -104.9))
        self.assertEqual(mock_search.call_count, 2)


class GeocodingBoundaryTests(SimpleTestCase):
    def test_request_time_module_dependency_graph(self):
        import ast
        from pathlib import Path

        pkg = Path(__file__).resolve().parents[1] / "services" / "geocoding"
        allowed = {
            "common": set(),
            "providers": {"common"},
            "request": {"common", "providers"},
            "offline": {"common"},
            "__init__": set(),
        }

        for mod, allowed_deps in allowed.items():
            path = pkg / f"{mod}.py"
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            deps = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level == 1 and node.module:
                        deps.add(node.module.split(".")[0])
                    elif node.level == 0 and node.module and node.module.startswith(
                        "fuel.services.geocoding."
                    ):
                        deps.add(node.module.split(".")[-1])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("fuel.services.geocoding."):
                            deps.add(alias.name.split(".")[-1])
            package_deps = {d for d in deps if d in allowed}
            self.assertEqual(
                package_deps,
                allowed_deps,
                f"{mod} imports {package_deps}, expected {allowed_deps}",
            )
            if mod == "offline":
                self.assertNotIn("request", package_deps)

    def test_request_time_module_does_not_import_offline(self):
        import os
        import subprocess
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[2]
        code = (
            "import os, sys; "
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
            "import fuel.services.geocoding.request; "
            "print('loaded' if 'fuel.services.geocoding.offline' in sys.modules else 'not-loaded')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        )
        self.assertEqual(result.stdout.strip(), "not-loaded")
