"""Load the OPIS CSV into FuelStation. Safe to run repeatedly."""

import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from fuel.models import FuelStation

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
UPDATABLE = ["name", "address", "city", "state", "price_per_gallon"]


class Command(BaseCommand):
    help = "Import US fuel stations from the assessment CSV (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=settings.CSV_FILE)

    def handle(self, *args, **options):
        path = Path(options["file"])
        cheapest: dict[int, FuelStation] = {}

        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                state = row["State"].strip().upper()
                if state not in US_STATES:
                    continue
                opis_id = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"].strip())
                current = cheapest.get(opis_id)
                if current is not None and current.price_per_gallon <= price:
                    continue
                cheapest[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=state,
                    price_per_gallon=price,
                )

        existing = {station.opis_id: station for station in FuelStation.objects.all()}
        to_create, to_update = [], []
        for opis_id, candidate in cheapest.items():
            current = existing.get(opis_id)
            if current is None:
                to_create.append(candidate)
                continue
            changed = False
            for field in UPDATABLE:
                if getattr(current, field) != getattr(candidate, field):
                    setattr(current, field, getattr(candidate, field))
                    changed = True
            if changed:
                to_update.append(current)

        with transaction.atomic():
            FuelStation.objects.bulk_create(to_create, batch_size=1000)
            FuelStation.objects.bulk_update(to_update, UPDATABLE, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"CSV rows read; {len(cheapest)} US stations kept "
                f"({len(to_create)} created, {len(to_update)} updated)."
            )
        )
