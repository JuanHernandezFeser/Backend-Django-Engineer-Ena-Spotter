"""Pure fuel-stop planner. No Django, no database, no network calls."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from fuel.exceptions import RouteNotFeasibleError

EPS = Decimal("0.000001")
CENT = Decimal("0.01")


def dec(value: float | int | str) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class FuelStationOption:
    id: int
    name: str
    address: str
    city: str
    state: str
    lat: float
    lon: float
    price: Decimal
    position_miles: float


@dataclass(frozen=True)
class FuelStop:
    station: FuelStationOption
    gallons: Decimal
    cost: Decimal


@dataclass(frozen=True)
class FuelPlan:
    stops: list[FuelStop]
    total_cost: Decimal


class NoStationInRangeError(RouteNotFeasibleError):
    """Raised when the destination cannot be reached with the available stations."""


def plan_fuel_stops(
    stations: list[FuelStationOption],
    total_distance_miles: float,
    range_miles: float = 500.0,
    mpg: float = 10.0,
    origin_station: FuelStationOption | None = None,
) -> FuelPlan:
    """Choose stops and gallons bought, starting with an initial purchase at origin.

    The trip is anchored at the origin station (position_miles on the route) with
    no fuel, and the first purchase is decided with the same rule as later stops:
    buy only what is needed to reach the destination or a cheaper reachable
    station, otherwise fill the tank. If the effective distance is zero, no fuel
    is bought.
    """
    distance = dec(total_distance_miles)
    tank_range = dec(range_miles)
    miles_per_gallon = dec(mpg)
    capacity_gallons = tank_range / miles_per_gallon

    if distance <= EPS:
        return FuelPlan(stops=[], total_cost=Decimal("0.00"))

    if origin_station is None:
        raise NoStationInRangeError(
            "No fuel station available at the trip origin.",
            details={"reason": "no_origin_station"},
        )

    position = dec(origin_station.position_miles)
    if distance - position <= EPS:
        return FuelPlan(stops=[], total_cost=Decimal("0.00"))

    fuel_miles = Decimal(0)
    current_station: FuelStationOption | None = origin_station
    current_price: Decimal | None = origin_station.price
    stops: list[FuelStop] = []

    upcoming = sorted(
        (
            s
            for s in stations
            if dec(s.position_miles) > position + EPS and s.id != origin_station.id
        ),
        key=lambda s: s.position_miles,
    )

    while distance - position > fuel_miles + EPS:
        remaining = distance - position

        if remaining <= tank_range + EPS:
            gallons = max(
                Decimal(0), remaining / miles_per_gallon - fuel_miles / miles_per_gallon
            )
            gallons = round_gallons(gallons)
            if current_station is not None and gallons > 0:
                cost = (gallons * current_price).quantize(CENT, rounding=ROUND_HALF_UP)
                stops.append(FuelStop(current_station, gallons, cost))
            break

        reachable = [
            s
            for s in upcoming
            if position < dec(s.position_miles) <= position + tank_range + EPS
        ]
        if not reachable:
            raise NoStationInRangeError(
                f"No fuel station within {range_miles:g} miles at mile "
                f"{float(position):.1f} of {total_distance_miles:.1f}.",
                details={"reason": "no_reachable_station"},
            )

        if current_price is None:
            cheaper = reachable
        else:
            cheaper = [s for s in reachable if s.price < current_price - EPS]
        fill_up = not cheaper

        if cheaper:
            target = min(cheaper, key=lambda s: (s.price, s.position_miles))
        else:
            target = max(reachable, key=lambda s: s.position_miles)

        travel = dec(target.position_miles) - position
        if fill_up:
            gallons = capacity_gallons - fuel_miles / miles_per_gallon
        else:
            gallons = max(
                Decimal(0), travel / miles_per_gallon - fuel_miles / miles_per_gallon
            )

        gallons = round_gallons(gallons)
        fuel_miles = fuel_miles + gallons * miles_per_gallon - travel
        if current_station is not None and gallons > 0:
            cost = (gallons * current_price).quantize(CENT, rounding=ROUND_HALF_UP)
            stops.append(FuelStop(current_station, gallons, cost))

        position = dec(target.position_miles)
        current_station = target
        current_price = target.price

    return FuelPlan(
        stops=stops,
        total_cost=sum((stop.cost for stop in stops), Decimal(0)).quantize(CENT),
    )


def round_gallons(gallons: Decimal) -> Decimal:
    return gallons.quantize(CENT, rounding=ROUND_HALF_UP)
