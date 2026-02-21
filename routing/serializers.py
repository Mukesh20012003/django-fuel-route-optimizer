from decimal import Decimal

from rest_framework import serializers


class LocationInputSerializer(serializers.Serializer):
    """
    Represents a location provided by the client.

    Either:
      - a free-form text query (city name, address, etc.), or
      - explicit latitude/longitude coordinates.

    Validation rules:
      - At least one of (query) OR (lat & lng) must be provided.
      - If lat is provided, lng is required (and vice versa).
    """

    query = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Free-form location text, e.g., 'Dallas, TX' or '1600 Pennsylvania Ave NW, Washington, DC'.",
    )
    lat = serializers.FloatField(
        required=False,
        help_text="Latitude in decimal degrees, e.g., 32.7767.",
    )
    lng = serializers.FloatField(
        required=False,
        help_text="Longitude in decimal degrees, e.g., -96.7970.",
    )

    def validate(self, attrs):
        query = attrs.get("query", "").strip()
        lat = attrs.get("lat")
        lng = attrs.get("lng")

        has_query = bool(query)
        has_coords = lat is not None or lng is not None

        if not has_query and not has_coords:
            raise serializers.ValidationError(
                "Provide either 'query' or both 'lat' and 'lng' for a location."
            )

        if has_coords and (lat is None or lng is None):
            raise serializers.ValidationError(
                "Both 'lat' and 'lng' must be provided together for coordinates."
            )

        # Optionally, validate coordinate ranges
        if lat is not None and not (-90.0 <= lat <= 90.0):
            raise serializers.ValidationError("Latitude must be between -90 and 90 degrees.")

        if lng is not None and not (-180.0 <= lng <= 180.0):
            raise serializers.ValidationError("Longitude must be between -180 and 180 degrees.")

        # Normalise query string back into attrs
        attrs["query"] = query
        return attrs


class RouteRequestSerializer(serializers.Serializer):
    """
    Main input serializer for the fuel route optimizer endpoint.

    Example input:
        {
          "start": { "query": "Dallas, TX" },
          "end":   { "query": "Atlanta, GA" }
        }

        or

        {
          "start": { "lat": 32.7767, "lng": -96.7970 },
          "end":   { "lat": 33.7490, "lng": -84.3880 }
        }
    """

    start = LocationInputSerializer(
        help_text="Start location (either query string or coordinates)."
    )
    end = LocationInputSerializer(
        help_text="End location (either query string or coordinates)."
    )

    # Optional override for initial fuel state etc., reserved for future extension
    initial_fuel_gallons = serializers.FloatField(
        required=False,
        min_value=0.0,
        default=None,
        help_text="Optional current fuel in gallons at trip start. If omitted, assume full tank.",
    )


class FuelStopOutputSerializer(serializers.Serializer):
    """
    Represents a single fuel stop along the optimized route.

    This is a pure output serializer (read-only from the client's perspective).
    """

    station_id = serializers.IntegerField(
        help_text="Internal ID of the FuelStation model instance."
    )
    name = serializers.CharField(
        help_text="Station name."
    )
    address = serializers.CharField(
        help_text="Station address or exit description."
    )
    city = serializers.CharField(
        help_text="Station city."
    )
    state = serializers.CharField(
        help_text="Two-letter state code."
    )

    latitude = serializers.FloatField(
        help_text="Station latitude in decimal degrees."
    )
    longitude = serializers.FloatField(
        help_text="Station longitude in decimal degrees."
    )

    price_per_gallon = serializers.DecimalField(
        max_digits=5,
        decimal_places=3,
        help_text="Price per gallon at this station in USD.",
    )

    distance_from_start_miles = serializers.FloatField(
        help_text="Cumulative distance from trip start to this stop, in miles."
    )

    gallons_to_buy = serializers.FloatField(
        help_text="How many gallons to purchase at this stop."
    )
    cost_at_stop = serializers.FloatField(
        help_text="Total fuel cost (USD) at this stop."
    )


class RouteGeometrySerializer(serializers.Serializer):
    """
    Simplified representation of the route geometry returned to the client.

    For this assignment, we will return:
      - total distance
      - polyline or list of coordinate points

    To minimise payload size, you will probably use an encoded polyline string.
    """

    distance_miles = serializers.FloatField(
        help_text="Total route distance in miles."
    )

    # You can choose one representation; here we support both but you might use one.
    geometry_polyline = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Encoded polyline string of the route geometry, if available.",
    )

    coordinates = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(),
            min_length=2,
            max_length=2,
        ),
        required=False,
        help_text="Route as list of [lon, lat] coordinate pairs, if returned.",
    )


class RoutePlanSerializer(serializers.Serializer):
    """
    Full response serializer for the fuel route optimizer.

    Combines:
      - route geometry + distance
      - fuel stops and per-stop costs
      - aggregated fuel cost information
    """

    route = RouteGeometrySerializer(
        help_text="Route geometry and distance information."
    )

    fuel_stops = FuelStopOutputSerializer(
        many=True,
        help_text="Ordered list of fuel stops along the route."
    )

    total_fuel_gallons = serializers.FloatField(
        help_text="Total gallons of fuel consumed for this trip."
    )
    total_fuel_cost = serializers.FloatField(
        help_text="Total fuel cost in USD for the entire trip."
    )

    vehicle_max_range_miles = serializers.FloatField(
        help_text="Maximum vehicle range per full tank in miles (e.g., 500)."
    )
    vehicle_mpg = serializers.FloatField(
        help_text="Vehicle efficiency in miles per gallon (e.g., 10)."
    )
