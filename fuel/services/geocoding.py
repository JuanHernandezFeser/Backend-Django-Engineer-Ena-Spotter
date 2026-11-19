"""Location resolution and one-time off-line station geocoding (Nominatim)."""

import hashlib
import json
import logging
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CONTINENTAL_US_BBOX = (24.4, 49.4, -125.0, -66.9)


class GeocodingError(Exception):
    pass


class LocationNotInUS(GeocodingError):
    pass


def parse_coordinates(value: str) -> tuple[float, float] | None:
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def is_in_continental_us(lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = CONTINENTAL_US_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


class NominatimClient:
    """Rate-limited, disk-cached geocoder. Never called from a hot loop."""

    def __init__(self, cache_file: str | None = None, delay: float | None = None,
                 autosave: bool = True, session: requests.Session | None = None):
        self.cache_file = Path(cache_file or settings.GEOCODE_CACHE_FILE)
        self.delay = settings.GEOCODE_DELAY_SECONDS if delay is None else delay
        self.autosave = autosave
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.NOMINATIM_USER_AGENT})
        self._cache: dict[str, dict | None] = self._load_cache()
        self._last_call = 0.0
        self._dirty = False

    def _load_cache(self) -> dict[str, dict | None]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Ignoring unreadable geocode cache %s", self.cache_file)
        return {}

    def flush(self) -> None:
        if not self._dirty:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(self._cache, sort_keys=True), encoding="utf-8"
        )
        self._dirty = False

    def search(self, query: str) -> dict | None:
        key = f"v1|us|{query.strip().lower()}"
        if key in self._cache:
            return self._cache[key]

        for attempt in range(settings.NOMINATIM_RETRIES + 1):
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_call = time.monotonic()
            try:
                response = self.session.get(
                    settings.NOMINATIM_URL,
                    params={
                        "q": query,
                        "format": "jsonv2",
                        "countrycodes": "us",
                        "limit": 1,
                    },
                    timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt == settings.NOMINATIM_RETRIES:
                    raise GeocodingError(f"Geocoding failed for '{query}': {exc}") from exc
                time.sleep(2 ** attempt)

        results = response.json()
        first = results[0] if results else None
        result = None
        if first:
            display_name = first.get("display_name", query)
            country_code = (
                first.get("country_code")
                or first.get("address", {}).get("country_code")
                or ("us" if "United States" in display_name else None)
            )
            result = {
                "lat": float(first["lat"]),
                "lon": float(first["lon"]),
                "country_code": country_code,
                "display_name": display_name,
            }
        self._cache[key] = result
        self._dirty = True
        if self.autosave:
            self.flush()
        return result


def resolve_location(value: str) -> tuple[float, float, str]:
    """Return (lat, lon, label) for 'lat,lon' or free text."""
    text = value.strip()
    coords = parse_coordinates(text)
    if coords is not None:
        lat, lon = coords
        if not is_in_continental_us(lat, lon):
            raise LocationNotInUS(
                "Coordinates must be inside the continental USA "
                "(lat 24.4-49.4, lon -125.0 to -66.9)."
            )
        return lat, lon, text

    cache_key = "location:v1:" + hashlib.sha256(text.lower().encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = NominatimClient().search(text)
    if result is None or result.get("country_code") != "us":
        raise LocationNotInUS(f"'{text}' could not be resolved to a USA location.")
    if not is_in_continental_us(result["lat"], result["lon"]):
        raise LocationNotInUS(
            f"'{text}' is in the USA but outside the supported continental area."
        )
    resolved = (result["lat"], result["lon"], result["display_name"])
    cache.set(cache_key, resolved, settings.GEOCODE_CACHE_SECONDS)
    return resolved
