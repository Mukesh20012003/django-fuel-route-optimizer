# 🎥 Loom Video Talking Points (≤ 5 minutes)

## Opening (30 sec)
- "Hi, this is the Django Fuel Route Optimiser – a REST API that finds the cheapest fuel stops along a driving route across the US."
- "I'll walk through the code, show it working via Postman, and highlight the performance/caching layer."

---

## 1. Code Tour (2 min)

### Models (`routing/models.py`)
- **FuelStation** – loaded from CSV, stores name, address, city, state, lat/lon, price
- DB indexes on `state`, `city`, `rack_id`, `latitude`, `longitude` + composite indexes
- "These indexes accelerate the bounding-box station queries."

### Services (`routing/services/`)
- **`routing_client.py`** – wraps OpenRouteService for geocoding + routing
  - Both functions cache results via Django's cache framework (1hr geocoding, 30min routes)
  - "This ensures we hit the external API at most once per unique input."
- **`optimizer.py`** – core algorithm
  - `build_route_profile()` – converts polyline coordinates to cumulative distances
  - `find_stations_near_route()` – bbox SQL filter → haversine refinement with sampled vertices
  - `plan_fuel_stops_greedy()` – greedy planner picks cheapest reachable station before range limit, fills tank to full
  - "10 MPG, 500-mile range – so the tank is 50 gallons."

### Views (`routing/views.py`)
- Single endpoint: `POST /api/route/optimize/`
- Shows the pipeline: validate → geocode → route → profile → stations → optimise → respond
- Performance timing logged at each step (geocoding, routing, optimisation)
- Geocode results also cached at the view level (separate from the routing_client cache)

### Settings (`fuel_route_api/settings.py`)
- All secrets from `.env` via `python-dotenv`
- Redis cache when `REDIS_URL` is set, otherwise in-memory LocMemCache
- PostgreSQL or SQLite via `USE_POSTGRES` flag

---

## 2. Postman Demo (1.5 min)

### Request 1 – City Strings
```json
POST http://localhost:8000/api/route/optimize/
{
  "start": { "query": "Dallas, TX" },
  "end":   { "query": "Atlanta, GA" }
}
```
- Show response: ~780 miles, fuel stops with station names/prices, total cost
- Point out: `fuel_stops` list is ordered by route distance, costs look realistic for a 10 MPG truck

### Request 2 – Raw Coordinates
```json
{
  "start": { "lat": 32.7767, "lng": -96.7970 },
  "end":   { "lat": 33.7490, "lng": -84.3880 }
}
```
- "Coordinates skip geocoding – faster response"
- Highlight response time in Postman (compare with first call vs cached second call)

### Performance Highlight
- "First call: ~2-3 seconds (geocoding + routing API)"
- "Second call (same inputs): ~200ms (cache hit)"
- Show the console logs with timing breakdowns

---

## 3. Testing (30 sec)
- "30 unit + integration tests, all passing"
- Run `python manage.py test routing -v 2` on screen
- "All external APIs are mocked – tests run in under 1 second with no network calls"

---

## Closing (30 sec)
- "The repo includes a README with full setup instructions, a .env.example template, and a Postman collection."
- "Thanks for watching – repo link and this video link are included in the submission."
