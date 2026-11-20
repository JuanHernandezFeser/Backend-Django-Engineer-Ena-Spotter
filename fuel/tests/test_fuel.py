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
    def test_zero_distance_needs_no_fuel(self):
        origin = station(1, 0, "3.00")
        plan = plan_fuel_stops([origin], 0, origin_station=origin)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, Decimal("0.00"))

    def test_same_origin_destination_zero_cost(self):
        origin = station(1, 0, "3.00")
        plan = plan_fuel_stops([origin], 0.0, origin_station=origin)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, Decimal("0.00"))

    def test_trip_shorter_than_range_buys_only_needed_at_origin(self):
        origin = station(1, 0, "3.00")
        plan = plan_fuel_stops(
            [origin, station(2, 100, "2.00")], 300, origin_station=origin
        )
        self.assertEqual([(s.station.id, float(s.gallons)) for s in plan.stops], [(1, 30.0)])
        self.assertEqual(plan.total_cost, Decimal("90.00"))

    def test_exactly_range_buys_full_tank_at_origin(self):
        origin = station(1, 0, "3.00")
        plan = plan_fuel_stops([origin, station(2, 250, "2.00")], 500, origin_station=origin)
        self.assertEqual([(s.station.id, float(s.gallons)) for s in plan.stops], [(1, 50.0)])
        self.assertEqual(plan.total_cost, Decimal("150.00"))

    def test_no_origin_station_raises_for_trip(self):
        with self.assertRaises(NoStationInRangeError):
            plan_fuel_stops([station(1, 100, "3.00")], 300)

    def test_no_stations_in_range_from_origin(self):
        origin = station(1, 0, "3.00")
        with self.assertRaises(NoStationInRangeError):
            plan_fuel_stops([origin, station(2, 900, "3.00")], 1200, origin_station=origin)

    def test_no_stations_at_all_with_distance(self):
        with self.assertRaises(NoStationInRangeError):
            plan_fuel_stops([], 1200)

    def test_origin_buys_only_enough_to_reach_cheaper_station(self):
        origin = station(1, 0, "5.00")
        s2 = station(2, 300, "3.00")
        s3 = station(3, 800, "4.00")
        plan = plan_fuel_stops([origin, s2, s3], 1200, origin_station=origin)
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [30.0, 50.0, 40.0])
        self.assertEqual(
            [s.cost for s in plan.stops],
            [Decimal("150.00"), Decimal("150.00"), Decimal("160.00")],
        )
        self.assertEqual(plan.total_cost, Decimal("460.00"))

    def test_fill_up_at_origin_when_no_cheaper_reachable(self):
        origin = station(1, 0, "4.00")
        s2 = station(2, 300, "4.50")
        s3 = station(3, 800, "3.00")
        plan = plan_fuel_stops([origin, s2, s3], 1200, origin_station=origin)
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [50.0, 30.0, 40.0])
        self.assertEqual(
            [s.cost for s in plan.stops],
            [Decimal("200.00"), Decimal("135.00"), Decimal("120.00")],
        )
        self.assertEqual(plan.total_cost, Decimal("455.00"))

    def test_fill_up_when_no_cheaper_station_is_reachable_mid_route(self):
        origin = station(1, 0, "4.00")
        s2 = station(2, 400, "4.00")
        s3 = station(3, 900, "5.00")
        plan = plan_fuel_stops([origin, s2, s3], 1000, origin_station=origin)
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [50.0, 40.0, 10.0])
        self.assertEqual(plan.total_cost, Decimal("410.00"))

    def test_station_exactly_at_range_limit(self):
        origin = station(1, 0, "4.00")
        s2 = station(2, 500, "4.00")
        s3 = station(3, 1000, "4.00")
        plan = plan_fuel_stops([origin, s2, s3], 1200, origin_station=origin)
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [50.0, 50.0, 20.0])
        self.assertEqual(plan.total_cost, Decimal("480.00"))

    def test_stations_behind_origin_are_ignored(self):
        origin = station(2, 0, "3.00")
        plan = plan_fuel_stops(
            [station(1, -50, "1.00"), origin, station(3, 400, "3.00")],
            900,
            origin_station=origin,
        )
        self.assertEqual([s.station.id for s in plan.stops], [2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [50.0, 40.0])

    def test_multi_stop_route_from_origin(self):
        origin = station(1, 0, "3.00")
        s2 = station(2, 450, "5.00")
        s3 = station(3, 900, "4.00")
        plan = plan_fuel_stops([origin, s2, s3], 1200, origin_station=origin)
        self.assertEqual([s.station.id for s in plan.stops], [1, 2, 3])
        self.assertEqual([float(s.gallons) for s in plan.stops], [50.0, 40.0, 30.0])
        self.assertEqual(
            [s.cost for s in plan.stops],
            [Decimal("150.00"), Decimal("200.00"), Decimal("120.00")],
        )
        self.assertEqual(plan.total_cost, Decimal("470.00"))
