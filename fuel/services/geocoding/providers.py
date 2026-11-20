"""Shared geocoding providers (Open-Meteo) for request-time and offline command use."""

import hashlib

import requests
from django.conf import settings
from django.core.cache import cache

from fuel.exceptions import (
    ExternalServiceBadResponseError,
    ExternalServiceRateLimitedError,
    ExternalServiceTimeoutError,
    ExternalServiceUnavailableError,
)

from .common import CONTINENTAL_US_BBOX, is_in_continental_us

_MISSING = object()

US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


class OpenMeteoClient:
    """Keyless city-level geocoder used for request-time free-text input."""

    def search(self, query: str) -> dict | None:
        text = query.strip()
        key = "geocode:open-meteo:v2:" + hashlib.sha256(
            text.lower().encode()
        ).hexdigest()
        cached = cache.get(key, _MISSING)
        if cached is not _MISSING:
            return cached

        parts = [part.strip() for part in text.split(",")]
        city = parts[0]
        state = parts[1].upper() if len(parts) > 1 and parts[1] else None
        names = [text]
        if state and city and city.lower() != text.lower():
            names.append(city)
        result = None

        try:
            for name in names:
                response = requests.get(
                    settings.OPEN_METEO_URL,
                    params={
                        "name": name,
                        "count": 20,
                        "language": "en",
                        "format": "json",
                    },
                    headers={
                        "User-Agent": settings.PHOTON_USER_AGENT,
                        "Accept": "application/json",
                    },
                    timeout=settings.OPEN_METEO_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                result = self._parse(response.json(), state)
                if result:
                    break
        except requests.Timeout as exc:
            raise ExternalServiceTimeoutError(str(exc)) from exc
        except requests.ConnectionError as exc:
            raise ExternalServiceUnavailableError(str(exc)) from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                retry_after = exc.response.headers.get("Retry-After") if exc.response is not None else None
                raise ExternalServiceRateLimitedError(str(exc), retry_after=retry_after) from exc
            if status_code is not None and 500 <= status_code < 600:
                raise ExternalServiceUnavailableError(str(exc)) from exc
            raise ExternalServiceBadResponseError(str(exc)) from exc
        except requests.RequestException as exc:
            raise ExternalServiceUnavailableError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise ExternalServiceBadResponseError(str(exc)) from exc

        cache.set(key, result, settings.GEOCODE_CACHE_SECONDS)
        return result

    @staticmethod
    def _parse(payload: dict, state: str | None = None) -> dict | None:
        expected_state = US_STATE_NAMES.get(state, state) if state else None
        for item in payload.get("results") or []:
            if (item.get("country_code") or "").lower() != "us":
                continue
            admin1 = str(item.get("admin1") or "").strip()
            if expected_state and admin1.lower() not in (
                expected_state.lower(), state.lower()
            ):
                continue
            try:
                lat, lon = float(item["latitude"]), float(item["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not is_in_continental_us(lat, lon):
                continue
            parts = (item.get("name"), admin1, item.get("country"))
            return {
                "lat": lat,
                "lon": lon,
                "country_code": "us",
                "display_name": ", ".join(
                    dict.fromkeys(str(part) for part in parts if part)
                ),
            }
        return None
