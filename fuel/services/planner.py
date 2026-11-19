"""Glue the steps together: resolve -> route -> nearby stations -> fuel plan."""

from django.conf import settings

from .fuel import plan_fuel_stops
from .geocoding import resolve_location
from .routing import get_route
from .stations import nearby_stations


def plan_route(start: str, finish: str) -> dict:
    start_lat, start_lon, _ = resolve_location(start)
    end_lat, end_lon, _ = resolve_location(finish)

    route = get_route(start_lat, start_lon, end_lat, end_lon)
    stations = nearby_stations(route.coordinates)
    plan = plan_fuel_stops(
        stations,
        route.distance_miles,
        range_miles=settings.TANK_RANGE_MILES,
        mpg=settings.MPG,
    )

    return {
        "start": start,
        "finish": finish,
        "route": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for lon, lat in route.coordinates],
            },
            "properties": {},
        },
        "distance_miles": round(route.distance_miles, 2),
        "stops": [
            {
                "name": stop.station.name,
                "address": stop.station.address,
                "city": stop.station.city,
                "state": stop.station.state,
                "lat": stop.station.lat,
                "lon": stop.station.lon,
                "price_per_gallon": str(stop.station.price),
                "miles_from_start": round(stop.station.position_miles, 2),
                "gallons_purchased": float(stop.gallons),
                "cost": str(stop.cost),
            }
            for stop in plan.stops
        ],
        "total_fuel_cost": str(plan.total_cost),
    }
