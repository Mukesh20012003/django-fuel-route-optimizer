import logging
import requests
import polyline
from typing import Dict, List, Tuple, Any
from django.conf import settings
from django.core.cache import cache
import hashlib

logger = logging.getLogger(__name__)

class RoutingAPIError(Exception):
    """Raised when the routing API call fails or returns an invalid response."""

class RoutingAPIConfigError(RoutingAPIError):
    """Raised when routing API configuration (e.g., API key) is missing or invalid."""

def geocode_location(query: str, timeout_seconds: int = 10) -> Tuple[float, float]:
    """
    Geocode query → (lon, lat) with Redis caching (1hr TTL).
    """
    cache_key = f"geocode:{hashlib.md5(query.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("🔴 Cache HIT: %s", query)
        return cached
    
    api_key = settings.ROUTING_API_KEY
    if not api_key:
        raise RoutingAPIConfigError("ROUTING_API_KEY missing in .env")
    
    url = "https://api.openrouteservice.org/geocode/search"
    params = {
        'api_key': api_key,
        'text': query,
        'size': 1,
        'boundary.country': 'US'
    }
    
    try:
        logger.info("🗺️ Geocoding: %s", query)
        resp = requests.get(url, params=params, timeout=timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        
        if not data.get('features'):
            raise RoutingAPIError(f"No location found for '{query}'")
        
        coords = data['features'][0]['geometry']['coordinates']
        lon, lat = float(coords[0]), float(coords[1])
        logger.info("✅ Geocoded '%s' → (%.4f, %.4f)", query, lon, lat)
        
        cache.set(cache_key, (lon, lat), 3600)  # 1hr TTL
        return lon, lat
        
    except requests.RequestException as e:
        logger.exception("❌ Geocoding failed: %s", query)
        raise RoutingAPIError(f"Geocoding failed for '{query}': {e}")

def get_route(start_lon: float, start_lat: float, end_lon: float, end_lat: float, timeout_seconds: int = 20) -> Dict[str, Any]:
    """
    Get full route with polyline decoding + Redis caching (30min TTL).
    """
    cache_key = f"route:{hashlib.md5(f'{start_lon:.4f},{start_lat:.4f},{end_lon:.4f},{end_lat:.4f}'.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("🔴 Cache HIT: %.1fmi route", cached['distance_miles'])
        return cached
    
    api_key = getattr(settings, "ROUTING_API_KEY", None)
    if not api_key:
        raise RoutingAPIConfigError("ROUTING_API_KEY missing")
    
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    params = {'api_key': api_key}
    
    body = {
        "coordinates": [[start_lon, start_lat], [end_lon, end_lat]],
        "instructions": False,
    }
    
    try:
        logger.info("🛣️ ORS route: (%.4f,%.4f)→(%.4f,%.4f)", start_lon, start_lat, end_lon, end_lat)
        resp = requests.post(url, params=params, json=body, timeout=timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("ORS keys: %s", list(data.keys()))
        
    except requests.RequestException as exc:
        logger.exception("❌ ORS network error")
        raise RoutingAPIError(f"ORS unavailable") from exc
    
    try:
        route = data['routes'][0]
        summary = route['summary']
        distance_miles = float(summary['distance']) / 1609.34
        
        # ✅ FULL POLYLINE DECODE
        geometry = route['geometry']
        decoded = polyline.decode(geometry)  # [(lat, lon), ...]
        coordinates = [[lon, lat] for lat, lon in decoded]  # [[lon, lat], ...]
        
        result = {
            "distance_miles": distance_miles,
            "coordinates": coordinates
        }
        
        logger.info("✅ Route: %.1f mi with %d coordinates", distance_miles, len(coordinates))
        cache.set(cache_key, result, 1800)  # 30min TTL
        return result
        
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("❌ ORS parse error: %s", data)
        raise RoutingAPIError("Invalid route response") from exc

# Usage in views.py:
"""
start_lon, start_lat = geocode_location(start_query)
end_lon, end_lat = geocode_location(end_query)
route = get_route(start_lon, start_lat, end_lon, end_lat)
"""
