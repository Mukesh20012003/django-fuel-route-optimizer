import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation


class Command(BaseCommand):
    """
    Import fuel station data from a CSV file into the FuelStation model.

    This version assumes the CSV has NO HEADER row and columns are in this order:[file:1]
        0: Opis_Truckstop_Id (int)
        1: Name (str)
        2: Address (str)
        3: City (str)
        4: State (2-letter code)
        5: Rack_Id (int)
        6: Retail_Price (decimal)

    Example row:[file:1]
        60442,Circle K #4706716,SR-71 & SR-47,Yorkville,IL,290,3.799

    Usage:
        python manage.py import_fuel_prices \
            --csv-path data/fuel-prices-for-be-assessment.csv --truncate
    """

    help = "Import fuel station price data from a headerless CSV file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            type=str,
            required=True,
            help="Path to the fuel prices CSV file.",
        )

        parser.add_argument(
            "--truncate",
            action="store_true",
            help="If provided, deletes existing FuelStation records before import.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        truncate = options["truncate"]

        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV file does not exist: {path}")

        self.stdout.write(self.style.NOTICE(f"Using CSV file: {path}"))

        if truncate:
            self.stdout.write("Deleting existing FuelStation records...")
            deleted_count, _ = FuelStation.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(f"Deleted {deleted_count} existing FuelStation records.")
            )

        created_count = 0
        updated_count = 0
        skipped_count = 0

        # Wrap the import in an atomic transaction so it's all‑or‑nothing
        with transaction.atomic():
            with path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)

                for row_index, row in enumerate(reader, start=1):
                    # Expect exactly 7 columns per row based on sample CSV.[file:1]
                    if len(row) < 7:
                        skipped_count += 1
                        self.stderr.write(
                            self.style.ERROR(
                                f"Row {row_index}: expected at least 7 columns, got {len(row)}; row skipped."
                            )
                        )
                        continue

                    try:
                        opis_raw = row[0].strip()
                        opis_id = int(opis_raw) if opis_raw else None

                        name = row[1].strip()
                        address = row[2].strip()
                        city = row[3].strip()
                        state = row[4].strip()
                        rack_raw = row[5].strip()
                        rack_id = int(rack_raw) if rack_raw else None

                        price_raw = row[6].strip()
                        if not price_raw:
                            raise ValueError("Empty price")
                        price = Decimal(price_raw)

                    except (ValueError, InvalidOperation, IndexError) as exc:
                        skipped_count += 1
                        self.stderr.write(
                            self.style.ERROR(
                                f"Row {row_index}: invalid data ({exc}); row skipped. Raw row: {row}"
                            )
                        )
                        continue

                    if not name or not address or not city or not state:
                        skipped_count += 1
                        self.stderr.write(
                            self.style.WARNING(
                                f"Row {row_index}: missing required text fields; row skipped."
                            )
                        )
                        continue

                    # Upsert by natural key (name, address, city, state)
                    obj, created = FuelStation.objects.update_or_create(
                        name=name,
                        address=address,
                        city=city,
                        state=state,
                        defaults={
                            "opis_id": opis_id,
                            "rack_id": rack_id,
                            "price": price,
                        },
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Created: {created_count}, Updated: {updated_count}, "
                f"Skipped: {skipped_count}."
            )
        )
