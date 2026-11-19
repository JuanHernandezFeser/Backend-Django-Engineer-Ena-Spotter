"""One-time, rate-limited geocoding of stations. Never called from the API."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from fuel.models import FuelStation
from fuel.services.geocoding import (
    GeocodingError,
    NominatimClient,
    is_in_continental_us,
)


class Command(BaseCommand):
    help = "Geocode stations into lat/lon using Nominatim (slow, run once)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max stations to process")
        parser.add_argument("--force", action="store_true", help="Retry already geocoded stations")

    def handle(self, *args, **options):
        client = NominatimClient(autosave=False)
        queryset = (
            FuelStation.objects.all()
            if options["force"]
            else FuelStation.objects.filter(lat__isnull=True)
        )
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        updated, processed, failed = [], 0, 0
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
                continue
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

            if len(updated) >= 200:
                self._save(updated)

        self._save(updated)
        client.flush()
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} stations, geocoded {processed - failed}, "
                f"failed {failed}. Cache: {settings.GEOCODE_CACHE_FILE}"
            )
        )

    @staticmethod
    def _save(updated):
        if updated:
            with transaction.atomic():
                FuelStation.objects.bulk_update(updated, ["lat", "lon", "geocoded_at"])
            updated.clear()
