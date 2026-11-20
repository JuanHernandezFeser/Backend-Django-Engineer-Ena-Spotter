"""One-time station geocoding (Photon bulk or Open-Meteo per city). Not used by the API."""

from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from fuel.models import FuelStation
from fuel.services.geocoding import (
    GeocodingError,
    OpenMeteoClient,
    PhotonClient,
    is_in_continental_us,
)


class Command(BaseCommand):
    help = (
        "Geocode stations into lat/lon: Photon bulk (slow) or Open-Meteo "
        "via --city CITY,ST / --all-cities."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Max stations (Photon) or cities (Open-Meteo) to process",
        )
        parser.add_argument("--force", action="store_true", help="Retry already geocoded stations")
        parser.add_argument(
            "--city",
            action="append",
            default=[],
            metavar="CITY,ST",
            help="Geocode all stations in a city with Open-Meteo (repeatable)",
        )
        parser.add_argument(
            "--all-cities",
            action="store_true",
            help="Geocode every pending city/state with Open-Meteo (fast, resumable)",
        )

    def handle(self, *args, **options):
        if options["city"] and options["all_cities"]:
            raise CommandError("Use --city or --all-cities, not both.")
        if options["city"]:
            self._geocode_cities(options["city"], options["force"])
            return
        if options["all_cities"]:
            self._geocode_all_cities(options["limit"], options["force"])
            return

        client = PhotonClient(autosave=False)
        queryset = (
            FuelStation.objects.all()
            if options["force"]
            else FuelStation.objects.filter(lat__isnull=True)
        )
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        updated, processed, failed, consecutive_errors = [], 0, 0, 0
        for station in queryset.iterator():
            processed += 1
            try:
                result = client.search(
                    f"{station.address}, {station.city}, {station.state}, USA"
                )
                if result is None:
                    result = client.search(f"{station.city}, {station.state}, USA")
            except GeocodingError as exc:
                self.stderr.write(str(exc))
                failed += 1
                consecutive_errors += 1
                if consecutive_errors >= settings.PHOTON_MAX_CONSECUTIVE_FAILURES:
                    self._save(updated)
                    client.flush()
                    raise CommandError(
                        f"Aborted after {consecutive_errors} consecutive geocoding failures. "
                        "Check PHOTON_URL/network and re-run to resume."
                    ) from exc
                continue
            consecutive_errors = 0
            if (
                result
                and result.get("country_code") == "us"
                and is_in_continental_us(result["lat"], result["lon"])
            ):
                station.lat = result["lat"]
                station.lon = result["lon"]
                station.geocoded_at = timezone.now()
                updated.append(station)
            else:
                failed += 1

            if processed % 200 == 0:
                self._save(updated)
                client.flush()

        self._save(updated)
        client.flush()
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} stations, geocoded {processed - failed}, "
                f"failed {failed}. Cache: {settings.GEOCODE_CACHE_FILE}"
            )
        )

    def _geocode_all_cities(self, limit: int, force: bool) -> None:
        queryset = (
            FuelStation.objects.all()
            if force
            else FuelStation.objects.filter(lat__isnull=True)
        )
        grouped = defaultdict(list)
        for station in queryset.only("id", "city", "state", "lat", "lon", "geocoded_at"):
            grouped[(station.city, station.state)].append(station)
        pairs = sorted(grouped)
        if limit:
            pairs = pairs[:limit]
        if not pairs:
            self.stdout.write("No pending cities to geocode.")
            return

        client = OpenMeteoClient()
        pending_update, updated_count, failures, consecutive = [], 0, [], 0
        for index, (city, state) in enumerate(pairs, 1):
            try:
                result = client.search(f"{city}, {state}")
            except GeocodingError as exc:
                self.stderr.write(str(exc))
                result = None
            if (
                result
                and result.get("country_code") == "us"
                and is_in_continental_us(result["lat"], result["lon"])
            ):
                consecutive = 0
                for station in grouped[(city, state)]:
                    station.lat = result["lat"]
                    station.lon = result["lon"]
                    station.geocoded_at = timezone.now()
                    pending_update.append(station)
                updated_count += len(grouped[(city, state)])
            else:
                consecutive += 1
                failures.append(f"{city}, {state}")

            if len(pending_update) >= 500:
                self._save(pending_update)
            if index % 100 == 0 or index == len(pairs):
                self.stdout.write(
                    f"Cities {index}/{len(pairs)} | geocoded stations {updated_count} "
                    f"| failed cities {len(failures)}"
                )
            if consecutive >= settings.OPEN_METEO_MAX_CONSECUTIVE_FAILURES:
                self._save(pending_update)
                raise CommandError(
                    f"Aborted after {consecutive} consecutive geocoding failures. "
                    "Re-run to resume."
                )

        self._save(pending_update)
        if failures:
            preview = ", ".join(failures[:20])
            suffix = " ..." if len(failures) > 20 else ""
            self.stderr.write(f"Unresolved cities ({len(failures)}): {preview}{suffix}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(pairs)} cities, geocoded {updated_count} stations, "
                f"failed {len(failures)} cities."
            )
        )

    def _geocode_cities(self, values: list[str], force: bool) -> None:
        client = OpenMeteoClient()
        updated_count = 0
        for value in values:
            parts = [part.strip() for part in value.split(",")]
            if len(parts) != 2 or not all(parts):
                raise CommandError(f"Invalid --city '{value}'. Use CITY,ST (e.g. Phoenix,AZ).")
            city, state = parts
            queryset = FuelStation.objects.filter(city__iexact=city, state__iexact=state)
            if not force:
                queryset = queryset.filter(lat__isnull=True)
            stations = list(queryset)
            if not stations:
                self.stderr.write(f"No stations need geocoding in {city}, {state}.")
                continue

            try:
                result = client.search(f"{city}, {state}")
            except GeocodingError as exc:
                raise CommandError(str(exc)) from exc
            if (
                not result
                or result.get("country_code") != "us"
                or not is_in_continental_us(result["lat"], result["lon"])
            ):
                self.stderr.write(f"Could not geocode {city}, {state} in the USA.")
                continue

            for station in stations:
                station.lat = result["lat"]
                station.lon = result["lon"]
                station.geocoded_at = timezone.now()
            count = len(stations)
            self._save(stations)
            updated_count += count
            self.stdout.write(f"{city}, {state}: {count} stations")

        self.stdout.write(
            self.style.SUCCESS(f"Geocoded {updated_count} stations in {len(values)} cities.")
        )

    @staticmethod
    def _save(updated):
        if updated:
            with transaction.atomic():
                FuelStation.objects.bulk_update(updated, ["lat", "lon", "geocoded_at"])
            updated.clear()
