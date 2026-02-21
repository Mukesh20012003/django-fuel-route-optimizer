import logging
import time
import uuid
import hashlib
from typing import Tuple, Dict, Any
import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from routing.serializers import RouteRequestSerializer, RoutePlanSerializer
from routing.services.routing_client import get_route  # Keep existing
from routing.services.optimizer import (
    build_route_profile,
    find_stations_near_route,
    plan_fuel_stops_greedy,
    compute_total_fuel_usage_and_cost,
)
from routing.exceptions import (
    GeocodingError, RoutingAPIError, OptimizationError, RouteUnfeasibleError,
    RoutingAPIConfigError 
)
logger = logging.getLogger(__name__)

class FuelRouteOptimizerView(APIView):
    """
    POST /api/route/optimize/ - Fuel-optimized route with stops.
    """
    
    def post(self, request, *args, **kwargs):
        # Request ID for tracing
        request_id = str(uuid.uuid4())[:8]
        logger.info(f"[RID:{request_id}] Route optimization started")
        start_time = time.time()
        
        try:
            return self._handle_optimize(request, request_id, start_time)
        except GeocodingError as exc:
            logger.warning(f"[RID:{request_id}] Geocoding failed: {exc}", extra={'request_id': request_id})
            return Response({"detail": str(exc)}, status=exc.status_code)
        except RoutingAPIError as exc:
            logger.error(f"[RID:{request_id}] Routing API failed: {exc}", extra={'request_id': request_id})
            return Response({"detail": str(exc)}, status=exc.status_code)
        except OptimizationError as exc:
            logger.warning(f"[RID:{request_id}] Optimization failed: {exc}", extra={'request_id': request_id})
            return Response({"detail": str(exc)}, status=exc.status_code)
        except Exception as exc:
            logger.exception(f"[RID:{request_id}] Unexpected error: {exc}", extra={'request_id': request_id})
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _handle_optimize(self, request, request_id: str, start_time: float) -> Response:
        """Core logic with structured logging."""
        # 1. Input validation
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"[RID:{request_id}] Invalid input: {serializer.errors}")
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        logger.info(f"[RID:{request_id}] Valid input: {data['start']['query'] or data['start']} → {data['end']['query' or data['end']]}")

        # 2. Geocode (with timing)
        geo_start = time.time()
        try:
            start_lon, start_lat = self._resolve_location(data["start"], request_id)
            end_lon, end_lat = self._resolve_location(data["end"], request_id)
        except ValueError as exc:
            raise GeocodingError(str(exc)) from exc
        logger.info(f"[RID:{request_id}] Geocoding done in {time.time() - geo_start:.2f}s")

        # 3. Route (with timing)
        route_start = time.time()
        try:
            route_data = get_route(start_lon, start_lat, end_lon, end_lat)
        except RoutingAPIConfigError as exc:
            raise RoutingAPIError("Routing API not configured") from exc
        except Exception as exc:
            raise RoutingAPIError("Routing provider failed") from exc
        logger.info(f"[RID:{request_id}] Route fetched in {time.time() - route_start:.2f}s: {route_data.get('distance_miles', 0):.0f}mi")

        coordinates = route_data.get("coordinates") or []
        distance_miles = float(route_data.get("distance_miles", 0.0))
        
        if not coordinates or distance_miles <= 0:
            raise RoutingAPIError("Invalid route returned")

        # 4. Route profile
        route_profile_start = time.time()
        route_points = build_route_profile(coordinates)
        logger.debug(f"[RID:{request_id}] Route profile built in {time.time() - route_profile_start:.2f}s: {len(route_points)} points")

        # 5. Feasibility check FIRST (before expensive station query)
        vehicle_max_range = float(getattr(settings, "VEHICLE_MAX_RANGE_MILES", 500.0))
        # if distance_miles > vehicle_max_range * 2:  # Absolute sanity check
        #     raise RouteUnfeasibleError(f"Route {distance_miles:.0f}mi exceeds safe limit ({vehicle_max_range * 2:.0f}mi)")

        # 6. Stations (with timing)
        stations_start = time.time()
        candidate_stations = find_stations_near_route(
            route_points=route_points,
            max_distance_from_route_miles=50.0,
            bbox_padding_degrees=1.0,
        )
        logger.info(f"[RID:{request_id}] Found {len(candidate_stations)} stations in {time.time() - stations_start:.2f}s")

        # 7. Early feasibility validation
        initial_fuel_gallons = data.get("initial_fuel_gallons", 0)
        if not candidate_stations:
            if distance_miles > vehicle_max_range:
                logger.warning(f"[RID:{request_id}] No stations + route too long: {distance_miles:.0f}mi > {vehicle_max_range:.0f}mi")
                raise RouteUnfeasibleError()
            fuel_plans = []  # Direct drive OK
        else:
            opt_start = time.time()
            try:
                fuel_plans = plan_fuel_stops_greedy(
                    route_points=route_points,
                    candidate_stations=candidate_stations,
                    vehicle_max_range_miles=500,
                    vehicle_mpg=float(getattr(settings, "VEHICLE_MPG", 25.0)),
                    initial_fuel_gallons=initial_fuel_gallons,
                )
                logger.info(f"[RID:{request_id}] Optimization complete: {len(fuel_plans)} stops in {time.time() - opt_start:.2f}s")
            except ValueError as exc:
                logger.warning(f"[RID:{request_id}] Planner failed: {exc}")
                raise OptimizationError(str(exc))

        # 8. Totals
        totals = compute_total_fuel_usage_and_cost(fuel_plans)
        
        # 9. Response
        route_payload = {
            "route": {
                "distance_miles": distance_miles,
                "coordinates": coordinates,
                "geometry_polyline": "",
            },
            "fuel_stops": [
                {
                    "station_id": fp.station.id,
                    "name": fp.station.name,
                    "address": fp.station.address,
                    "city": fp.station.city,
                    "state": fp.station.state,
                    "latitude": fp.station.latitude,
                    "longitude": fp.station.longitude,
                    "price_per_gallon": fp.price_per_gallon,
                    "distance_from_start_miles": fp.route_distance_miles,
                    "gallons_to_buy": fp.gallons_to_buy,
                    "cost_at_stop": fp.cost_at_stop,
                }
                for fp in fuel_plans
            ],
            "total_fuel_gallons": totals["total_gallons"],
            "total_fuel_cost": totals["total_cost"],
            "vehicle_max_range_miles": vehicle_max_range,
            "vehicle_mpg": float(getattr(settings, "VEHICLE_MPG", 25.0)),
        }
        
        response_serializer = RoutePlanSerializer(data=route_payload)
        response_serializer.is_valid(raise_exception=True)
        
        total_time = time.time() - start_time
        logger.info(f"[RID:{request_id}] SUCCESS in {total_time:.2f}s: {distance_miles:.0f}mi, ${totals['total_cost']:.2f}, {len(fuel_plans)} stops")
        
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def _resolve_location(self, loc: Dict[str, Any], request_id: str) -> Tuple[float, float]:
        """Resolve location to (lon, lat) with caching for geocoded queries."""
        lat = loc.get("lat")
        lng = loc.get("lng")
        query = (loc.get("query") or "").strip()

        if lat is not None and lng is not None:
            logger.debug(f"[RID:{request_id}] Using direct coords: ({lng}, {lat})")
            return float(lng), float(lat)

        if not query:
            raise GeocodingError("Location requires coordinates or query")

        # ── Cache lookup ──────────────────────────────────────────────
        cache_key = f"geocode:{hashlib.md5(query.lower().encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached:
            logger.info(f"[RID:{request_id}] Geocode cache HIT: '{query}' → {cached}")
            return cached

        api_key = getattr(settings, "ROUTING_API_KEY", None)
        if not api_key:
            raise GeocodingError("Routing API key missing")

        geocode_start = time.time()
        geocode_url = "https://api.openrouteservice.org/geocode/search"
        params = {"api_key": api_key, "text": query, "size": 1}

        try:
            resp = requests.get(geocode_url, params=params, timeout=10)
            logger.debug(f"[RID:{request_id}] Geocode {query} in {time.time() - geocode_start:.2f}s: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            logger.error(f"[RID:{request_id}] Geocode network error: {exc}")
            raise GeocodingError("Geocoding service unavailable") from exc

        if resp.status_code != 200:
            raise GeocodingError(f"Geocoding failed: HTTP {resp.status_code}")

        data = resp.json()
        features = data.get("features", [])
        if not features:
            raise GeocodingError(f"No results for '{query}'")

        coords = features[0]["geometry"]["coordinates"]
        lon, lat = float(coords[0]), float(coords[1])
        logger.debug(f"[RID:{request_id}] Geocoded '{query}' → ({lon}, {lat})")

        # ── Cache store (1 hour TTL) ──────────────────────────────────
        cache.set(cache_key, (lon, lat), 3600)

        return lon, lat

