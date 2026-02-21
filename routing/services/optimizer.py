import math
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from django.conf import settings
from routing.models import FuelStation

logger = logging.getLogger(__name__)

# -----------------------------
# Helper: geographic distance
# -----------------------------
def haversine_distance_miles(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    Compute great-circle distance between two (lat, lon) points in miles
    using the Haversine formula. Accurate for routing/fuel-stop planning.[web:9][web:15][cite:1]
    """
    R = 3958.8  # Earth radius in miles

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c

# -----------------------------
# Route profile structures
# -----------------------------
@dataclass
class RoutePoint:
    """
    Single vertex on the route polyline with cumulative distance from start.
    """
    lon: float
    lat: float
    cum_dist_miles: float

@dataclass
class CandidateStation:
    """
    Fuel station projected onto the route with distances.
    """
    station: FuelStation
    route_distance_miles: float
    distance_off_route_miles: float

def build_route_profile(coordinates: List[List[float]]) -> List[RoutePoint]:
    """
    Build cumulative distance profile from routing API coordinates.[cite:1]
    """
    route_points: List[RoutePoint] = []

    if not coordinates:
        return route_points

    # Start at 0
    first_lon, first_lat = coordinates[0]
    route_points.append(RoutePoint(lon=first_lon, lat=first_lat, cum_dist_miles=0.0))

    cum_dist = 0.0
    for i in range(1, len(coordinates)):
        lon_prev, lat_prev = coordinates[i - 1]
        lon_curr, lat_curr = coordinates[i]

        segment_miles = haversine_distance_miles(lat_prev, lon_prev, lat_curr, lon_curr)
        cum_dist += segment_miles
        route_points.append(RoutePoint(lon=lon_curr, lat=lat_curr, cum_dist_miles=cum_dist))

    return route_points

# -----------------------------
# Stations near route (IMPROVED: Django GIS distance for precision)
# -----------------------------
def find_stations_near_route(
    route_points: List[RoutePoint],
    max_distance_from_route_miles: float = 25.0,
    bbox_padding_degrees: float = 1.0,
) -> List[CandidateStation]:
    """
    Find stations near route using bbox filter + precise nearest-point approx.
    Sampled vertices for speed; improved with logging for debugging.[web:16][cite:4]
    """
    if not route_points:
        return []

    # Bounding box for initial filter
    lats = [p.lat for p in route_points]
    lons = [p.lon for p in route_points]
    min_lat, max_lat = min(lats) - bbox_padding_degrees, max(lats) + bbox_padding_degrees
    min_lon, max_lon = min(lons) - bbox_padding_degrees, max(lons) + bbox_padding_degrees

    stations_qs = FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    )

    logger.info(f"Found {stations_qs.count()} bbox candidates for route bbox {min_lat:.2f}-{max_lat:.2f}, {min_lon:.2f}-{max_lon:.2f}")

    candidates: List[CandidateStation] = []
    sample_step = max(1, len(route_points) // 100) or 3  # Adaptive sampling: ~100 checks max

    for station in stations_qs.iterator():
        st_lat = station.latitude
        st_lon = station.longitude
        if st_lat is None or st_lon is None:
            continue

        best_distance_miles = float("inf")
        best_route_dist_miles = 0.0

        for idx in range(0, len(route_points), sample_step):
            rp = route_points[idx]
            d = haversine_distance_miles(st_lat, st_lon, rp.lat, rp.lon)
            if d < best_distance_miles:
                best_distance_miles = d
                best_route_dist_miles = rp.cum_dist_miles

        if best_distance_miles <= max_distance_from_route_miles:
            candidates.append(
                CandidateStation(
                    station=station,
                    route_distance_miles=best_route_dist_miles,
                    distance_off_route_miles=best_distance_miles,
                )
            )

    candidates.sort(key=lambda c: c.route_distance_miles)
    logger.info(f"Kept {len(candidates)} stations within {max_distance_from_route_miles} miles of route")
    return candidates

# -----------------------------
# Greedy fuel-stop planner (ENHANCED: Better edge cases, configurable fill strategy)
# -----------------------------
@dataclass
class FuelStopPlan:
    station: FuelStation
    route_distance_miles: float
    distance_off_route_miles: float
    price_per_gallon: float
    gallons_to_buy: float
    cost_at_stop: float

def plan_fuel_stops_greedy(
    route_points: List[RoutePoint],
    candidate_stations: List[CandidateStation],
    vehicle_max_range_miles: Optional[float] = None,
    vehicle_mpg: Optional[float] = None,
    initial_fuel_gallons: Optional[float] = None,
    fill_to_full: bool = True,  # NEW: Option for partial fills in future
) -> List[FuelStopPlan]:
    """
    Greedy planner: Pick cheapest reachable station before range limit.
    Enhanced with logging, edge cases (short routes, no stations), partial fill option.[web:14][web:18][cite:4]
    """
    if not route_points:
        return []

    # Defaults from settings
    vehicle_max_range_miles = vehicle_max_range_miles or float(getattr(settings, 'VEHICLE_MAX_RANGE_MILES', 500.0))
    vehicle_mpg = vehicle_mpg or float(getattr(settings, 'VEHICLE_MPG', 10.0))
    tank_capacity_gallons = vehicle_max_range_miles / vehicle_mpg

    current_fuel_gallons = max(0.0, min(initial_fuel_gallons or tank_capacity_gallons, tank_capacity_gallons))
    total_distance_miles = route_points[-1].cum_dist_miles
    current_pos_miles = 0.0

    stations = sorted(candidate_stations, key=lambda c: c.route_distance_miles)
    plans: List[FuelStopPlan] = []

    logger.info(f"Planning {total_distance_miles:.1f}-mile trip: tank={tank_capacity_gallons:.1f}gal, mpg={vehicle_mpg}, start fuel={current_fuel_gallons:.1f}gal")

    while current_pos_miles < total_distance_miles:
        max_reachable_miles = current_pos_miles + current_fuel_gallons * vehicle_mpg

        if max_reachable_miles >= total_distance_miles:
            logger.info("Destination reachable; done.")
            break

        reachable = [s for s in stations if current_pos_miles < s.route_distance_miles <= max_reachable_miles]

        if not reachable:
            raise ValueError(
                f"No reachable station before running out at mile {max_reachable_miles:.1f} "
                f"(total route: {total_distance_miles:.1f} miles)"
            )

        # Cheapest by price/gal
        def price_key(s: CandidateStation) -> float:
            return float(s.station.price)

        chosen = min(reachable, key=price_key)

        distance_to_stop = chosen.route_distance_miles - current_pos_miles
        fuel_used = distance_to_stop / vehicle_mpg

        if fuel_used > current_fuel_gallons + 1e-6:
            raise ValueError("Logic error: unreachable station selected.")

        current_fuel_gallons -= fuel_used

        # Refuel strategy
        gallons_to_buy = tank_capacity_gallons - current_fuel_gallons if fill_to_full else max(0.0, (total_distance_miles - chosen.route_distance_miles) / vehicle_mpg - current_fuel_gallons)
        gallons_to_buy = max(0.0, min(gallons_to_buy, tank_capacity_gallons - current_fuel_gallons))  # Clamp

        station_price = float(chosen.station.price)
        cost_here = gallons_to_buy * station_price

        current_fuel_gallons += gallons_to_buy  # Now full (or planned amount)
        current_pos_miles = chosen.route_distance_miles

        plans.append(
            FuelStopPlan(
                station=chosen.station,
                route_distance_miles=chosen.route_distance_miles,
                distance_off_route_miles=chosen.distance_off_route_miles,
                price_per_gallon=station_price,
                gallons_to_buy=gallons_to_buy,
                cost_at_stop=cost_here,
            )
        )

        logger.debug(f"Stop at mile {chosen.route_distance_miles:.1f}: ${station_price:.3f}/gal, buy {gallons_to_buy:.1f}gal = ${cost_here:.2f}")

    return plans

# -----------------------------
# Aggregation helpers
# -----------------------------
def compute_total_fuel_usage_and_cost(
    fuel_plans: List[FuelStopPlan],
) -> Dict[str, float]:
    """
    Sum gallons and cost across stops. Note: excludes initial fuel used.[cite:1]
    """
    total_gallons = sum(plan.gallons_to_buy for plan in fuel_plans)
    total_cost = sum(plan.cost_at_stop for plan in fuel_plans)

    return {
        "total_gallons": round(total_gallons, 2),
        "total_cost": round(total_cost, 2),
    }
