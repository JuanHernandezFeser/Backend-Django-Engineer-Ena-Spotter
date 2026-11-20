"""Glue the steps together: resolve -> route -> nearby stations -> fuel plan."""

from django.conf import settings

from .fuel import EPS, plan_fuel_stops
from .geocoding import resolve_location
from .routing import get_route
from .stations import nearby_stations, select_origin_station


def plan_route(start: str, finish: str) -> dict:
    start_lat, start_lon, _ = resolve_location(start)
    end_lat, end_lon, _ = resolve_location(finish)

    route = get_route(start_lat, start_lon, end_lat, end_lon)
    route_feature = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lon, lat in route.coordinates],
        },
        "properties": {},
    }
    if route.distance_miles <= float(EPS):
        return {
            "start": start,
            "finish": finish,
            "route": route_feature,
            "distance_miles": round(route.distance_miles, 2),
            "stops": [],
            "total_fuel_cost": "0.00",
        }

    stations = nearby_stations(route.coordinates)
    origin = select_origin_station(stations, start_lat, start_lon)
    plan = plan_fuel_stops(
        stations,
        route.distance_miles,
        range_miles=settings.TANK_RANGE_MILES,
        mpg=settings.MPG,
        origin_station=origin,
    )

    return {
        "start": start,
        "finish": finish,
        "route": route_feature,
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
