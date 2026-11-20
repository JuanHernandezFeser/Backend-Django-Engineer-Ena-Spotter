"""Shared geocoding constants, helpers, and compatibility exceptions."""

CONTINENTAL_US_BBOX = (24.4, 49.4, -125.0, -66.9)


def is_in_continental_us(lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = CONTINENTAL_US_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
