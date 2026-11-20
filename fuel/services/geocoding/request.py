"""Request-time location resolution (coordinates and Open-Meteo text geocoding)."""

import hashlib

from django.conf import settings
from django.core.cache import cache

from fuel.exceptions import (
    InvalidLocationError,
    LocationNotFoundError,
    ServerMisconfiguredError,
)

from .common import is_in_continental_us
from .providers import OpenMeteoClient


def parse_coordinates(value: str) -> tuple[float, float] | None:
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def get_text_geocoder() -> OpenMeteoClient:
    if settings.TEXT_GEOCODER != "open-meteo":
        raise ServerMisconfiguredError(
            detail=(
                f"Unsupported TEXT_GEOCODER '{settings.TEXT_GEOCODER}' for request-time resolution. "
                "Use 'open-meteo'."
            )
        )
    return OpenMeteoClient()


def resolve_location(value: str) -> tuple[float, float, str]:
    """Return (lat, lon, label) for 'lat,lon' or free text.

    Raises InvalidLocationError for out-of-area coordinates/locations and
    LocationNotFoundError when text cannot be resolved to a USA location.
    """
    text = value.strip()
    coords = parse_coordinates(text)
    if coords is not None:
        lat, lon = coords
        if not is_in_continental_us(lat, lon):
            raise InvalidLocationError(
                "Coordinates outside supported continental USA area.",
                details={
                    "input": text,
                    "reason": "outside_continental_us",
                    "supported_area": "continental USA (lat 24.4-49.4, lon -125.0 to -66.9)",
                },
            )
        return lat, lon, text

    cache_key = (
        f"location:{settings.TEXT_GEOCODER}:v1:"
        + hashlib.sha256(text.lower().encode()).hexdigest()
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = get_text_geocoder().search(text)
    if result is None or result.get("country_code") != "us":
        raise LocationNotFoundError(
            "Location could not be resolved to a USA location.",
            details={"input": text},
        )
    if not is_in_continental_us(result["lat"], result["lon"]):
        raise InvalidLocationError(
            "Resolved location is outside the supported continental USA area.",
            details={"input": text, "reason": "outside_continental_us"},
        )
    resolved = (result["lat"], result["lon"], result["display_name"])
    cache.set(cache_key, resolved, settings.GEOCODE_CACHE_SECONDS)
    return resolved
