from django.db import models


class FuelStation(models.Model):
    """
    Represents a fuel station with pricing and location information.

    Data is loaded from the provided CSV file, which includes columns like:
    OPIS Truckstop ID, Name, Address, City, State, Rack ID, and Retail Price.[file:1]
    """

    # Optional: OPIS truckstop or station identifier from the CSV
    opis_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Optional external OPIS Truckstop ID reference from CSV.",
    )

    name = models.CharField(
        max_length=255,
        help_text="Fuel station name (brand + location identifier).",
    )

    address = models.CharField(
        max_length=255,
        help_text="Street address or highway exit description for this station.",
    )

    city = models.CharField(
        max_length=100,
        help_text="City name for this station.",
        db_index=True,  # common filter field
    )

    state = models.CharField(
        max_length=2,
        help_text="Two-letter US state code (e.g., TX, CA).",
        db_index=True,  # common filter field
    )

    # Rack ID from CSV – often represents a pricing/terminal region.[file:1]
    rack_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rack/terminal region identifier from CSV.",
        db_index=True,
    )

    # Latitude/longitude will be used to find stations near a route
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Latitude in decimal degrees.",
        db_index=True,
    )

    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Longitude in decimal degrees.",
        db_index=True,
    )

    price = models.DecimalField(
        max_digits=5,
        decimal_places=3,
        help_text="Price per gallon in USD (e.g., 3.599).",
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this station record was first created.",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this station record was last updated.",
    )

    class Meta:
        verbose_name = "Fuel station"
        verbose_name_plural = "Fuel stations"
        # Use modern Meta.indexes instead of deprecated index_together in Django 5+[web:46][web:50][web:58]
        indexes = [
            # Composite index for queries by state + rack_id
            models.Index(fields=["state", "rack_id"], name="station_state_rack_idx"),
            # Composite index for geo‑like queries combining lat/lon
            models.Index(fields=["latitude", "longitude"], name="station_lat_lon_idx"),
        ]
        ordering = ["state", "city", "name"]

    def __str__(self) -> str:
        parts = [self.name]
        if self.city:
            parts.append(self.city)
        if self.state:
            parts.append(self.state)
        return " - ".join(parts)
