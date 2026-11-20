"""One-call routing via the free public OSRM demo server."""

import hashlib
import math
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from fuel.exceptions import (
    ExternalServiceBadResponseError,
    ExternalServiceError,
    ExternalServiceRateLimitedError,
    ExternalServiceTimeoutError,
    ExternalServiceUnavailableError,
    RouteNotFeasibleError,
)

METERS_PER_MILE = 1609.344


class RoutingError(ExternalServiceError):
    """External routing service error (compatibility base)."""


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

        osrm_code = payload.get("code")
        if osrm_code in {"NoRoute", "NoSegment"}:
            raise RouteNotFeasibleError(
                detail=f"Routing infeasible: {osrm_code}",
                details={"reason": "no_route"},
            )
        if osrm_code not in (None, "Ok"):
            raise ExternalServiceBadResponseError(
                detail=f"Routing service returned error code: {osrm_code}"
            )

        routes = payload.get("routes") or []
        if not routes:
            raise ExternalServiceBadResponseError(
                detail="Routing service returned no routes."
            )
        route = routes[0]
        coordinates = simplify(
            [tuple(point) for point in route["geometry"]["coordinates"]],
            settings.ROUTE_SIMPLIFY_TOLERANCE,
        )
        if len(coordinates) < 2:
            raise ExternalServiceBadResponseError(
                detail="Routing service returned an empty geometry."
            )
        data = {
            "coordinates": coordinates,
            "distance_miles": route["distance"] / METERS_PER_MILE,
        }
    except requests.Timeout as exc:
        raise ExternalServiceTimeoutError(str(exc)) from exc
    except requests.ConnectionError as exc:
        raise ExternalServiceUnavailableError(str(exc)) from exc
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        osrm_code = None
        if exc.response is not None:
            try:
                osrm_code = exc.response.json().get("code")
            except (TypeError, ValueError):
                osrm_code = None
        if status_code == 429:
            retry_after = (
                exc.response.headers.get("Retry-After")
                if exc.response is not None
                else None
            )
            raise ExternalServiceRateLimitedError(str(exc), retry_after=retry_after) from exc
        if osrm_code in {"NoRoute", "NoSegment"}:
            raise RouteNotFeasibleError(
                detail=f"Routing infeasible: {osrm_code}",
                details={"reason": "no_route"},
            )
        if status_code is not None and 500 <= status_code < 600:
            raise ExternalServiceUnavailableError(str(exc)) from exc
        if status_code is not None and 400 <= status_code < 500:
            raise ExternalServiceBadResponseError(str(exc)) from exc
        raise ExternalServiceBadResponseError(str(exc)) from exc
    except requests.RequestException as exc:
        raise ExternalServiceUnavailableError(str(exc)) from exc
    except RouteNotFeasibleError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ExternalServiceBadResponseError(str(exc)) from exc

    cache.set(key, data, settings.ROUTE_CACHE_SECONDS)
    return RouteResult(**data)
