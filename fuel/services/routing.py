"""One-call routing via the free public OSRM demo server."""

import hashlib
import math
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    pass


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker: keep the route shape but drop near-collinear points."""
    if len(points) < 3 or tolerance <= 0:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        farthest, index = -1.0, None
        for i in range(start + 1, end):
            px, py = points[i]
            if denominator:
                fraction = ((px - ax) * dx + (py - ay) * dy) / denominator
                fraction = max(0.0, min(1.0, fraction))
                px_error = px - (ax + fraction * dx)
                py_error = py - (ay + fraction * dy)
            else:
                px_error, py_error = px - ax, py - ay
            distance = math.hypot(px_error, py_error)
            if distance > farthest:
                farthest, index = distance, i
        if farthest > tolerance and index is not None:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [point for point, kept in zip(points, keep) if kept]


@dataclass(frozen=True)
class RouteResult:
    coordinates: list[tuple[float, float]]
    distance_miles: float


def get_route(
    start_lat: float, start_lon: float, end_lat: float, end_lon: float
) -> RouteResult:
    raw_key = "route:v1:{:.5f},{:.5f}:{:.5f},{:.5f}".format(
        start_lat, start_lon, end_lat, end_lon
    )
    key = "route:v1:" + hashlib.sha256(raw_key.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return RouteResult(**cached)

    url = "{}/{:.6f},{:.6f};{:.6f},{:.6f}".format(
        settings.ROUTING_URL, start_lon, start_lat, end_lon, end_lat
    )
    try:
        response = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
                "alternatives": "false",
            },
            timeout=settings.ROUTING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        route = payload["routes"][0]
        coordinates = simplify(
            [tuple(point) for point in route["geometry"]["coordinates"]],
            settings.ROUTE_SIMPLIFY_TOLERANCE,
        )
        if len(coordinates) < 2:
            raise RoutingError("Routing service returned an empty geometry.")
        data = {
            "coordinates": coordinates,
            "distance_miles": route["distance"] / METERS_PER_MILE,
        }
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RoutingError(f"Routing service failed: {exc}") from exc

    cache.set(key, data, settings.ROUTE_CACHE_SECONDS)
    return RouteResult(**data)
