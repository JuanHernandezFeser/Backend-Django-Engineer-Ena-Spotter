from decimal import Decimal

from django.test import SimpleTestCase

from fuel.services.fuel import (
    FuelStationOption,
    NoStationInRangeError,
    plan_fuel_stops,
)


def station(opis_id: int, position_miles: float, price: str) -> FuelStationOption:
    return FuelStationOption(
        id=opis_id,
        name=f"Station {opis_id}",
        address="I-80, EXIT 1",
        city="Testville",
        state="NE",
        lat=41.0,
        lon=-100.0,
        price=Decimal(price),
        position_miles=position_miles,
    )


class FuelStopTests(SimpleTestCase):
    def test_trip_shorter_than_range_needs_no_fuel(self):
        plan = plan_fuel_stops([station(1, 100, "3.00")], 300)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, Decimal("0.00"))

    def test_exactly_500_miles_needs_no_fuel(self):
        plan = plan_fuel_stops([station(1, 250, "3.00")], 500)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, Decimal("0.00"))

    def test_no_stations_in_range(self):
        with self.assertRaises(NoStationInRangeError):
            plan_fuel_stops([station(1, 900, "3.00")], 1200)

    def test_no_stations_at_all(self):
        with self.assertRaises(NoStationInRangeError):
            plan_fuel_stops([], 1200)

    def test_cheaper_station_ahead_buys_only_enough_to_reach_it(self):
        plan = plan_fuel_stops(
            [station(1, 300, "4.00"), station(2, 800, "3.00")], 1200
        )
        self.assertEqual([(s.station.id, s.gallons) for s in plan.stops], [(1, 30.00), (2, 40.00)])
        self.assertEqual([s.cost for s in plan.stops], [Decimal("120.00"), Decimal("120.00")])
        self.assertEqual(plan.total_cost, Decimal("240.00"))

    def test_cheaper_station_behind_is_ignored(self):
        plan = plan_fuel_stops(
            [
                station(1, 100, "2.00"),
                station(2, 550, "5.00"),
                station(3, 1000, "4.00"),
            ],
            1200,
        )
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([s.gallons for s in plan.stops], [10.00, 40.00, 20.00])
        self.assertEqual(plan.total_cost, Decimal("300.00"))

    def test_fill_up_when_no_cheaper_station_is_reachable(self):
        plan = plan_fuel_stops(
            [station(1, 400, "4.00"), station(2, 900, "5.00")], 1000
        )
        self.assertEqual([(s.station.id, s.gallons) for s in plan.stops], [(1, 40.00), (2, 10.00)])
        self.assertEqual(plan.total_cost, Decimal("210.00"))

    def test_station_exactly_at_range_limit(self):
        plan = plan_fuel_stops(
            [station(1, 500, "4.00"), station(2, 1000, "4.00")], 1200
        )
        self.assertEqual([s.gallons for s in plan.stops], [50.00, 20.00])
        self.assertEqual(plan.total_cost, Decimal("280.00"))

    def test_stations_behind_origin_are_ignored(self):
        plan = plan_fuel_stops([station(1, -50, "1.00"), station(2, 400, "3.00")], 900)
        self.assertEqual([s.station.id for s in plan.stops], [2])
