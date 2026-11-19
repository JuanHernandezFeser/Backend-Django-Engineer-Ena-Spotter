"""Pure fuel-stop planner. No Django, no database, no network calls."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

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


class NoStationInRangeError(Exception):
    """Raised when the destination cannot be reached with the available stations."""


def plan_fuel_stops(
    stations: list[FuelStationOption],
    total_distance_miles: float,
    range_miles: float = 500.0,
    mpg: float = 10.0,
) -> FuelPlan:
    """Choose stops and gallons bought, starting with a full tank.

    Starting fuel is assumed to be already paid for, so it costs $0 here.
    At each stop we can buy up to a full tank (range_miles / mpg gallons).
    If a cheaper station is within a full tank's reach we buy only enough to
    get there; otherwise we fill up at the furthest reachable station.
    """
    distance = dec(total_distance_miles)
    tank_range = dec(range_miles)
    miles_per_gallon = dec(mpg)
    capacity_gallons = tank_range / miles_per_gallon

    upcoming = sorted(
        (s for s in stations if dec(s.position_miles) > EPS),
        key=lambda s: s.position_miles,
    )

    position = Decimal(0)
    fuel_miles = tank_range
    current_station: FuelStationOption | None = None
    current_price: Decimal | None = None
    stops: list[FuelStop] = []

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
                f"{float(position):.1f} of {total_distance_miles:.1f}."
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
