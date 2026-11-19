from django.db import models


class FuelStation(models.Model):
    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2)
    price_per_gallon = models.DecimalField(max_digits=10, decimal_places=8)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["opis_id"]
        indexes = [models.Index(fields=["lat", "lon"], name="fuelstation_lat_lon_idx")]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"
