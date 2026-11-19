"""Location resolution and one-time off-line station geocoding (Photon)."""

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


class PhotonClient:
    """Rate-limited, disk-cached geocoder. Never called from a hot loop."""

    def __init__(self, cache_file: str | None = None, delay: float | None = None,
                 autosave: bool = True, session: requests.Session | None = None):
        self.cache_file = Path(cache_file or settings.GEOCODE_CACHE_FILE)
        self.delay = settings.GEOCODE_DELAY_SECONDS if delay is None else delay
        self.autosave = autosave
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": settings.PHOTON_USER_AGENT,
            "Accept": "application/json",
        })
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
        key = f"v2|us|{query.strip().lower()}"
        if key in self._cache:
            return self._cache[key]

        for attempt in range(settings.PHOTON_RETRIES + 1):
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_call = time.monotonic()
            try:
                response = self.session.get(
                    settings.PHOTON_URL,
                    params=self._params(query),
                    timeout=(
                        settings.PHOTON_CONNECT_TIMEOUT_SECONDS,
                        settings.PHOTON_TIMEOUT_SECONDS,
                    ),
                )
                response.raise_for_status()
                break
            except requests.HTTPError as exc:
                if attempt == settings.PHOTON_RETRIES:
                    raise GeocodingError(f"Geocoding failed for '{query}': {exc}") from exc
                time.sleep(self._retry_after(exc, 2 ** attempt))
            except requests.RequestException as exc:
                if attempt == settings.PHOTON_RETRIES:
                    raise GeocodingError(f"Geocoding failed for '{query}': {exc}") from exc
                time.sleep(2 ** attempt)

        result = self._parse(response.json())
        self._cache[key] = result
        self._dirty = True
        if self.autosave:
            self.flush()
        return result

    @staticmethod
    def _params(query: str) -> dict:
        min_lat, max_lat, min_lon, max_lon = CONTINENTAL_US_BBOX
        return {
            "q": query,
            "limit": 1,
            "lang": "en",
            "countrycode": "US",
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        }

    @staticmethod
    def _retry_after(exc: requests.HTTPError, default: float) -> float:
        response = exc.response
        if response is None or response.status_code not in (429, 503):
            return default
        value = response.headers.get("Retry-After")
        if not value:
            return default
        try:
            return max(default, float(value))
        except ValueError:
            return default

    @staticmethod
    def _parse(payload: dict) -> dict | None:
        features = payload.get("features") or []
        if not features:
            return None
        feature = features[0]
        lon, lat = feature["geometry"]["coordinates"]
        props = feature.get("properties") or {}
        parts = (
            props.get("name"),
            props.get("city") or props.get("town") or props.get("village"),
            props.get("state"),
            props.get("country"),
        )
        display_name = ", ".join(dict.fromkeys(str(part) for part in parts if part))
        country_code = (props.get("countrycode") or "").lower()
        if not country_code and "United States" in display_name:
            country_code = "us"
        return {
            "lat": float(lat),
            "lon": float(lon),
            "country_code": country_code or None,
            "display_name": display_name,
        }


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

    cache_key = "location:v2:" + hashlib.sha256(text.lower().encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = PhotonClient().search(text)
    if result is None or result.get("country_code") != "us":
        raise LocationNotInUS(f"'{text}' could not be resolved to a USA location.")
    if not is_in_continental_us(result["lat"], result["lon"]):
        raise LocationNotInUS(
            f"'{text}' is in the USA but outside the supported continental area."
        )
    resolved = (result["lat"], result["lon"], result["display_name"])
    cache.set(cache_key, resolved, settings.GEOCODE_CACHE_SECONDS)
    return resolved
