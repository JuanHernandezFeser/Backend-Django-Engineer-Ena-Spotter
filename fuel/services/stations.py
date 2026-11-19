"""Find stations close to a route and compute their mile markers."""

import math

from django.conf import settings

from fuel.models import FuelStation

from .fuel import FuelStationOption

MILES_PER_DEGREE_LAT = 69.172
EARTH_RADIUS_MILES = 3958.8


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def nearby_stations(
    coordinates: list[tuple[float, float]], radius_miles: float | None = None
) -> list[FuelStationOption]:
    """Return stations within radius_miles of the route, with position in miles."""
    if len(coordinates) < 2:
        return []

    radius = (
        settings.STATION_SEARCH_RADIUS_MILES if radius_miles is None else radius_miles
    )
    lats = [lat for _lon, lat in coordinates]
    lons = [lon for lon, _lat in coordinates]
    mean_lat = sum(lats) / len(lats)
    lon_scale = MILES_PER_DEGREE_LAT * math.cos(math.radians(mean_lat))
    lat_delta = radius / MILES_PER_DEGREE_LAT
    lon_delta = radius / max(lon_scale, 1.0)

    candidates = FuelStation.objects.filter(
        lat__range=(min(lats) - lat_delta, max(lats) + lat_delta),
        lon__range=(min(lons) - lon_delta, max(lons) + lon_delta),
    ).only("id", "name", "address", "city", "state", "lat", "lon", "price_per_gallon")

    segments = []
    cumulative = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:]):
        length = _haversine_miles(lat1, lon1, lat2, lon2)
        if length <= 0:
            continue
        ax, ay = lon1 * lon_scale, lat1 * MILES_PER_DEGREE_LAT
        bx, by = lon2 * lon_scale, lat2 * MILES_PER_DEGREE_LAT
        segments.append(
            (
                ax, ay, bx, by, cumulative, length,
                min(ax, bx) - radius, max(ax, bx) + radius,
                min(ay, by) - radius, max(ay, by) + radius,
            )
        )
        cumulative += length

    result = []
    radius_squared = radius * radius
    for station in candidates:
        sx = station.lon * lon_scale
        sy = station.lat * MILES_PER_DEGREE_LAT
        best_distance_squared = float("inf")
        best_position = 0.0
        for ax, ay, bx, by, start_mile, length, minx, maxx, miny, maxy in segments:
            if sx < minx or sx > maxx or sy < miny or sy > maxy:
                continue
            dx, dy = bx - ax, by - ay
            fraction = ((sx - ax) * dx + (sy - ay) * dy) / (dx * dx + dy * dy)
            fraction = max(0.0, min(1.0, fraction))
            px, py = ax + fraction * dx, ay + fraction * dy
            distance_squared = (sx - px) ** 2 + (sy - py) ** 2
            if distance_squared < best_distance_squared:
                best_distance_squared = distance_squared
                best_position = start_mile + fraction * length
        if best_distance_squared <= radius_squared:
            result.append(
                FuelStationOption(
                    id=station.id,
                    name=station.name,
                    address=station.address,
                    city=station.city,
                    state=station.state,
                    lat=station.lat,
                    lon=station.lon,
                    price=station.price_per_gallon,
                    position_miles=best_position,
                )
            )

    return sorted(result, key=lambda station: station.position_miles)
