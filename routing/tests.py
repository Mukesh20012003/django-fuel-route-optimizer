"""
Comprehensive tests for the Fuel Route Optimizer.

Phase 14 – Unit tests (model, optimizer) and API integration tests (DRF).
All external HTTP calls are mocked for deterministic, fast execution.
"""
import math
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from routing.models import FuelStation
from routing.services.optimizer import (
    haversine_distance_miles,
    build_route_profile,
    find_stations_near_route,
    plan_fuel_stops_greedy,
    compute_total_fuel_usage_and_cost,
    RoutePoint,
    CandidateStation,
    FuelStopPlan,
)


# =====================================================================
# 1. FuelStation Model Tests
# =====================================================================
class FuelStationModelTest(TestCase):
    """Unit tests for the FuelStation model."""

    def setUp(self):
        self.station = FuelStation.objects.create(
            opis_id=60442,
            name="Circle K #4706716",
            address="SR-71 & SR-47",
            city="Yorkville",
            state="IL",
            rack_id=290,
            latitude=41.6411,
            longitude=-88.4426,
            price=Decimal("3.799"),
        )

    def test_str_representation(self):
        """__str__ should return 'Name - City - State'."""
        self.assertEqual(str(self.station), "Circle K #4706716 - Yorkville - IL")

    def test_str_representation_no_city(self):
        """__str__ without city should omit city segment."""
        self.station.city = ""
        self.station.save()
        self.assertEqual(str(self.station), "Circle K #4706716 - IL")

    def test_str_representation_no_state(self):
        """__str__ without state should omit state."""
        self.station.state = ""
        self.station.save()
        self.assertEqual(str(self.station), "Circle K #4706716 - Yorkville")

    def test_default_ordering(self):
        """Meta ordering should be by state, city, name."""
        FuelStation.objects.create(
            name="AAA Station", address="1st St", city="Austin",
            state="TX", price=Decimal("3.500"),
        )
        FuelStation.objects.create(
            name="BBB Station", address="2nd St", city="Dallas",
            state="TX", price=Decimal("3.400"),
        )
        stations = list(FuelStation.objects.values_list("name", flat=True))
        # IL < TX alphabetically, so Circle K first
        self.assertEqual(stations[0], "Circle K #4706716")

    def test_price_field_precision(self):
        """Price should store 3 decimal places properly."""
        self.station.price = Decimal("4.125")
        self.station.save()
        self.station.refresh_from_db()
        self.assertEqual(self.station.price, Decimal("4.125"))

    def test_nullable_opis_id_and_rack_id(self):
        """opis_id and rack_id accept null."""
        station = FuelStation.objects.create(
            name="Generic", address="Main St", city="Nowhere",
            state="KS", price=Decimal("3.000"),
            opis_id=None, rack_id=None,
            latitude=None, longitude=None,
        )
        self.assertIsNone(station.opis_id)
        self.assertIsNone(station.rack_id)
        self.assertIsNone(station.latitude)
        self.assertIsNone(station.longitude)

    def test_auto_timestamps(self):
        """created_at and updated_at should be auto-set."""
        self.assertIsNotNone(self.station.created_at)
        self.assertIsNotNone(self.station.updated_at)


# =====================================================================
# 2. Optimizer Unit Tests
# =====================================================================
class HaversineDistanceTest(TestCase):
    """Unit tests for haversine distance helper."""

    def test_same_point(self):
        """Distance from a point to itself is 0."""
        d = haversine_distance_miles(40.0, -74.0, 40.0, -74.0)
        self.assertAlmostEqual(d, 0.0, places=3)

    def test_known_distance_nyc_to_la(self):
        """NYC to LA should be roughly 2,451 miles."""
        d = haversine_distance_miles(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(d, 2451, delta=50)

    def test_short_distance(self):
        """Two points ~70 miles apart."""
        d = haversine_distance_miles(32.7767, -96.7970, 33.4484, -96.7970)
        self.assertGreater(d, 40)
        self.assertLess(d, 60)


class BuildRouteProfileTest(TestCase):
    """Tests for build_route_profile."""

    def test_empty_coordinates(self):
        """Empty coordinate list returns empty profile."""
        result = build_route_profile([])
        self.assertEqual(result, [])

    def test_single_point(self):
        """Single point yields profile with 0 cumulative distance."""
        result = build_route_profile([[-96.7970, 32.7767]])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].cum_dist_miles, 0.0)

    def test_cumulative_distance_increases(self):
        """Cumulative distance should be monotonically increasing."""
        coords = [
            [-96.7970, 32.7767],  # Dallas
            [-95.3698, 29.7604],  # Houston
            [-90.0715, 29.9511],  # New Orleans
        ]
        profile = build_route_profile(coords)
        self.assertEqual(len(profile), 3)
        self.assertAlmostEqual(profile[0].cum_dist_miles, 0.0)
        for i in range(1, len(profile)):
            self.assertGreater(profile[i].cum_dist_miles, profile[i - 1].cum_dist_miles)


class FindStationsNearRouteTest(TestCase):
    """Tests for bounding-box + station distance filtering."""

    def setUp(self):
        # Station ON the route (Dallas area)
        self.station_near = FuelStation.objects.create(
            name="Near Station", address="Highway 1", city="Dallas",
            state="TX", price=Decimal("3.500"),
            latitude=32.78, longitude=-96.80,
        )
        # Station FAR from route
        self.station_far = FuelStation.objects.create(
            name="Far Station", address="Remote Rd", city="Anchorage",
            state="AK", price=Decimal("4.000"),
            latitude=61.2181, longitude=-149.9003,
        )
        # Station without coords
        self.station_no_coords = FuelStation.objects.create(
            name="No Coords", address="Unknown", city="Unknown",
            state="XX", price=Decimal("3.000"),
            latitude=None, longitude=None,
        )

    def test_near_station_found(self):
        """Station within bbox and distance threshold should be included."""
        route_points = [
            RoutePoint(lon=-96.80, lat=32.78, cum_dist_miles=0.0),
            RoutePoint(lon=-96.70, lat=32.85, cum_dist_miles=10.0),
        ]
        candidates = find_stations_near_route(route_points, max_distance_from_route_miles=50.0)
        names = [c.station.name for c in candidates]
        self.assertIn("Near Station", names)
        self.assertNotIn("Far Station", names)
        self.assertNotIn("No Coords", names)

    def test_empty_route(self):
        """Empty route returns no candidates."""
        self.assertEqual(find_stations_near_route([]), [])


class GreedyPlannerTest(TestCase):
    """Tests for the greedy fuel-stop planner."""

    def _make_route(self, total_miles, num_points=10):
        """Create a synthetic linear route of given length."""
        step = total_miles / max(num_points - 1, 1)
        return [
            RoutePoint(lon=-96.8 + i * 0.01, lat=32.78, cum_dist_miles=i * step)
            for i in range(num_points)
        ]

    def _make_candidate(self, station, route_mile):
        return CandidateStation(
            station=station,
            route_distance_miles=route_mile,
            distance_off_route_miles=1.0,
        )

    def setUp(self):
        self.cheap_station = FuelStation.objects.create(
            name="Cheap", address="1st", city="A", state="TX",
            price=Decimal("3.000"), latitude=32.78, longitude=-96.70,
        )
        self.expensive_station = FuelStation.objects.create(
            name="Expensive", address="2nd", city="B", state="TX",
            price=Decimal("5.000"), latitude=32.78, longitude=-96.60,
        )

    def test_short_route_no_stops_needed(self):
        """A 100-mile route with full tank (500 mi range) needs no stops."""
        route = self._make_route(100)
        plans = plan_fuel_stops_greedy(
            route, [], vehicle_max_range_miles=500, vehicle_mpg=10,
        )
        self.assertEqual(plans, [])

    def test_chooses_cheapest_station(self):
        """When two stations are reachable, the cheaper one is chosen."""
        route = self._make_route(600)
        candidates = [
            self._make_candidate(self.expensive_station, 200),
            self._make_candidate(self.cheap_station, 250),
        ]
        plans = plan_fuel_stops_greedy(
            route, candidates,
            vehicle_max_range_miles=500, vehicle_mpg=10,
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].station.name, "Cheap")
        self.assertAlmostEqual(plans[0].price_per_gallon, 3.0)

    def test_stop_before_range_limit(self):
        """Stops must be placed before the vehicle runs out of fuel."""
        route = self._make_route(1200, num_points=20)
        candidates = [
            self._make_candidate(self.cheap_station, 300),
            self._make_candidate(self.expensive_station, 700),
        ]
        plans = plan_fuel_stops_greedy(
            route, candidates,
            vehicle_max_range_miles=500, vehicle_mpg=10,
        )
        # Should pick at least 2 stops for a 1200-mile route with 500-mile range
        self.assertGreaterEqual(len(plans), 2)
        # First stop must be before mile 500
        self.assertLessEqual(plans[0].route_distance_miles, 500)

    def test_raises_when_no_reachable_station(self):
        """Should raise ValueError when no station is reachable."""
        route = self._make_route(600)
        # No candidates at all
        with self.assertRaises(ValueError):
            plan_fuel_stops_greedy(
                route, [],
                vehicle_max_range_miles=500, vehicle_mpg=10,
            )

    def test_gallons_and_cost_positive(self):
        """Gallons bought and cost should be positive at each stop."""
        route = self._make_route(600)
        candidates = [self._make_candidate(self.cheap_station, 250)]
        plans = plan_fuel_stops_greedy(
            route, candidates,
            vehicle_max_range_miles=500, vehicle_mpg=10,
        )
        for plan in plans:
            self.assertGreater(plan.gallons_to_buy, 0)
            self.assertGreater(plan.cost_at_stop, 0)


class ComputeTotalsTest(TestCase):
    """Tests for compute_total_fuel_usage_and_cost."""

    def test_empty_plans(self):
        """No stops should return zero totals."""
        result = compute_total_fuel_usage_and_cost([])
        self.assertEqual(result["total_gallons"], 0.0)
        self.assertEqual(result["total_cost"], 0.0)

    def test_correct_summation(self):
        """Should sum gallons and costs correctly."""
        station = FuelStation(name="S", address="A", city="C", state="TX", price=Decimal("3.000"))
        plans = [
            FuelStopPlan(
                station=station, route_distance_miles=100,
                distance_off_route_miles=1, price_per_gallon=3.0,
                gallons_to_buy=20.0, cost_at_stop=60.0,
            ),
            FuelStopPlan(
                station=station, route_distance_miles=300,
                distance_off_route_miles=2, price_per_gallon=3.5,
                gallons_to_buy=15.0, cost_at_stop=52.5,
            ),
        ]
        result = compute_total_fuel_usage_and_cost(plans)
        self.assertAlmostEqual(result["total_gallons"], 35.0)
        self.assertAlmostEqual(result["total_cost"], 112.5)


# =====================================================================
# 3. API Integration Tests
# =====================================================================

# Reusable mock data
MOCK_GEOCODE_RESPONSE = {
    "features": [
        {
            "geometry": {"coordinates": [-96.7970, 32.7767]},
            "properties": {"label": "Dallas, TX"},
        }
    ]
}

MOCK_GEOCODE_RESPONSE_END = {
    "features": [
        {
            "geometry": {"coordinates": [-84.3880, 33.7490]},
            "properties": {"label": "Atlanta, GA"},
        }
    ]
}

MOCK_ROUTE_RESPONSE = {
    "routes": [
        {
            "summary": {"distance": 1_287_000},  # ~800 miles in meters
            "geometry": "some_polyline_string",
        }
    ]
}

# Synthetic decoded polyline: straight line Dallas → Atlanta (~800 mi)
MOCK_DECODED_COORDS = [
    [-96.80, 32.78],
    [-94.00, 32.90],
    [-91.00, 33.10],
    [-88.00, 33.30],
    [-84.39, 33.75],
]


def _mock_geocode_side_effect(url, params=None, timeout=None):
    """Return different geocoding results for start vs end."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    query = (params or {}).get("text", "")
    if "Atlanta" in query:
        mock_resp.json.return_value = MOCK_GEOCODE_RESPONSE_END
    else:
        mock_resp.json.return_value = MOCK_GEOCODE_RESPONSE
    return mock_resp


def _mock_route_post(url, params=None, json=None, timeout=None):
    """Return mock route response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_ROUTE_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@override_settings(
    ROUTING_API_KEY="test-api-key-12345",
    VEHICLE_MAX_RANGE_MILES=500,
    VEHICLE_MPG=10,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-cache"}},
)
class RouteOptimizeAPITest(APITestCase):
    """API integration tests for POST /api/route/optimize/."""

    URL = "/api/route/optimize/"

    def setUp(self):
        # Create stations along the mock route
        FuelStation.objects.create(
            name="Station A", address="I-20 Exit 50", city="Shreveport",
            state="LA", price=Decimal("3.200"),
            latitude=32.85, longitude=-93.75,
        )
        FuelStation.objects.create(
            name="Station B", address="I-20 Exit 120", city="Jackson",
            state="MS", price=Decimal("3.100"),
            latitude=33.10, longitude=-90.18,
        )
        FuelStation.objects.create(
            name="Station C", address="I-20 Exit 180", city="Birmingham",
            state="AL", price=Decimal("3.400"),
            latitude=33.30, longitude=-86.80,
        )

    @patch("routing.services.routing_client.polyline.decode")
    @patch("routing.services.routing_client.requests.post", side_effect=_mock_route_post)
    @patch("routing.views.requests.get", side_effect=_mock_geocode_side_effect)
    def test_happy_path_with_city_strings(self, mock_geo, mock_route, mock_decode):
        """Valid city string input → 200 with route + stops."""
        mock_decode.return_value = [(lat, lon) for lon, lat in MOCK_DECODED_COORDS]

        payload = {
            "start": {"query": "Dallas, TX"},
            "end": {"query": "Atlanta, GA"},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        data = resp.json()
        self.assertIn("route", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("total_fuel_gallons", data)
        self.assertIn("total_fuel_cost", data)
        self.assertIn("distance_miles", data["route"])
        self.assertIn("coordinates", data["route"])

        # Route should be ~800 miles
        self.assertGreater(data["route"]["distance_miles"], 500)

    @patch("routing.services.routing_client.polyline.decode")
    @patch("routing.services.routing_client.requests.post", side_effect=_mock_route_post)
    @patch("routing.views.requests.get", side_effect=_mock_geocode_side_effect)
    def test_happy_path_with_coordinates(self, mock_geo, mock_route, mock_decode):
        """Direct coordinates → 200 with valid response structure."""
        mock_decode.return_value = [(lat, lon) for lon, lat in MOCK_DECODED_COORDS]

        payload = {
            "start": {"lat": 32.7767, "lng": -96.7970},
            "end": {"lat": 33.7490, "lng": -84.3880},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        data = resp.json()
        self.assertIsInstance(data["fuel_stops"], list)
        self.assertGreaterEqual(data["total_fuel_cost"], 0)

    def test_invalid_input_missing_start(self):
        """Missing 'start' field → 400."""
        payload = {"end": {"query": "Atlanta, GA"}}
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_input_empty_body(self):
        """Empty body → 400."""
        resp = self.client.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_input_bad_coordinates(self):
        """Out-of-range coordinates → 400."""
        payload = {
            "start": {"lat": 999.0, "lng": -96.797},
            "end": {"query": "Atlanta, GA"},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("routing.views.requests.get")
    def test_geocoding_failure(self, mock_get):
        """Geocoding service error → 400 (GeocodingError)."""
        import requests as req_lib
        mock_get.side_effect = req_lib.RequestException("Connection refused")
        payload = {
            "start": {"query": "NonexistentPlace"},
            "end": {"query": "Atlanta, GA"},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("routing.services.routing_client.cache")
    @patch("routing.services.routing_client.requests.post")
    @patch("routing.views.requests.get", side_effect=_mock_geocode_side_effect)
    def test_routing_api_failure(self, mock_geo, mock_route_post, mock_cache):
        """Routing API failure → 502."""
        from requests.exceptions import ConnectionError as ReqConnectionError
        mock_route_post.side_effect = ReqConnectionError("ORS down")
        mock_cache.get.return_value = None  # ensure no cache hit
        payload = {
            "start": {"query": "Dallas, TX"},
            "end": {"query": "Atlanta, GA"},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("routing.services.routing_client.polyline.decode")
    @patch("routing.services.routing_client.requests.post", side_effect=_mock_route_post)
    @patch("routing.views.requests.get", side_effect=_mock_geocode_side_effect)
    def test_no_stations_short_route(self, mock_geo, mock_route, mock_decode):
        """Short route with no nearby stations → direct drive, 0 cost."""
        # Make route only 100 miles
        short_route_resp = {
            "routes": [{"summary": {"distance": 160_000}, "geometry": "short"}]
        }
        mock_route.side_effect = lambda *a, **kw: MagicMock(
            status_code=200,
            json=MagicMock(return_value=short_route_resp),
            raise_for_status=MagicMock(),
        )
        mock_decode.return_value = [(32.78, -96.80), (32.90, -96.50)]

        # Delete all stations so none are nearby
        FuelStation.objects.all().delete()

        payload = {
            "start": {"lat": 32.78, "lng": -96.80},
            "end": {"lat": 32.90, "lng": -96.50},
        }
        resp = self.client.post(self.URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["fuel_stops"], [])
        self.assertEqual(data["total_fuel_cost"], 0)
