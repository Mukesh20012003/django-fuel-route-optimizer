# 🚛 Django Fuel Route Optimizer

A Django REST API that finds the most **cost-efficient fuel stops** along a driving route across the United States. Given a start and end location, it calculates the optimal route, identifies fuel stations along the way, and uses a greedy algorithm to minimise total fuel cost while respecting vehicle range constraints.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Client (Postman / Frontend)                             │
│  POST /api/route/optimize/                               │
└──────────────────┬───────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────┐
│  Django REST Framework                                   │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ FuelRouteOptimizerView                              │ │
│  │  1. Validate input (serializers)                    │ │
│  │  2. Geocode city names → coordinates (cached)       │ │
│  │  3. Fetch route from OpenRouteService (cached)      │ │
│  │  4. Build route profile (cumulative distances)      │ │
│  │  5. Find stations near route (bbox + haversine)     │ │
│  │  6. Greedy fuel-stop optimisation                   │ │
│  │  7. Return optimised plan                           │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────┬───────────────────────────────────────┘
                   │
     ┌─────────────┼──────────────┐
     ▼             ▼              ▼
┌─────────┐  ┌──────────┐  ┌───────────┐
│ Postgres│  │  Redis   │  │OpenRoute  │
│  / SQLite│  │  Cache   │  │ Service   │
│ (DB)    │  │ (optional)│  │  (API)    │
└─────────┘  └──────────┘  └───────────┘
```

**Key design decisions:**
- **Greedy optimiser:** Picks the cheapest reachable station before the vehicle runs out of fuel. Fills to full tank at each stop.
- **Bounding-box + haversine filtering:** Narrows station search to a geographic rectangle around the route, then computes precise distances using the Haversine formula.
- **Caching:** Geocoding results (1hr TTL) and route responses (30min TTL) are cached to minimise external API calls.
- **Vehicle defaults:** 10 MPG, 500-mile tank range (configurable in settings).

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Django 5.2, Django REST Framework 3.16 |
| Database | PostgreSQL (SQLite fallback) |
| Cache | Redis via `django-redis` (LocMemCache fallback) |
| Routing API | OpenRouteService |
| Geocoding | OpenRouteService Geocode API |
| Testing | Django TestCase, DRF APITestCase, unittest.mock |

## Setup

### 1. Clone & Create Virtual Environment

```bash
git clone <repo-url>
cd django-fuel-route

python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values (see below)
```

### 4. Apply Migrations

```bash
python manage.py migrate
```

### 5. Load Fuel Station Data

```bash
python manage.py import_fuel_prices --csv-path data/fuel-prices-for-be-assessment.csv --truncate
```

### 6. Geocode Stations (Optional but Recommended)

```bash
python manage.py geocode_fuel_stations --workers 3 --delay 0.3
```

### 7. Run Development Server

```bash
python manage.py runserver
```

The API is now available at `http://localhost:8000/api/route/optimize/`.

## Environment Variables

Create a `.env` file in the project root (see `.env.example`):

```env
# Django
DJANGO_SECRET_KEY=change-me-to-a-long-random-string
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost

# OpenRouteService API
ROUTING_API_KEY=your-openrouteservice-api-key
ROUTING_API_BASE_URL=https://api.openrouteservice.org/v2/directions/driving-car

# PostgreSQL (set USE_POSTGRES=False for SQLite)
USE_POSTGRES=True
POSTGRES_DB=fuel_route_db
POSTGRES_USER=fuel_route_user
POSTGRES_PASSWORD=your-db-password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Redis (optional – leave blank for in-memory cache)
REDIS_URL=redis://127.0.0.1:6379/1

# CORS
CORS_ALLOW_ALL_ORIGINS=True
```

> **Production:** Set `DJANGO_DEBUG=False`, use a strong `DJANGO_SECRET_KEY`, restrict `DJANGO_ALLOWED_HOSTS`, and set `CORS_ALLOW_ALL_ORIGINS=False`.

## API Reference

### `POST /api/route/optimize/`

**Request – City Strings:**
```json
{
  "start": { "query": "Dallas, TX" },
  "end":   { "query": "Atlanta, GA" }
}
```

**Request – Coordinates:**
```json
{
  "start": { "lat": 32.7767, "lng": -96.7970 },
  "end":   { "lat": 33.7490, "lng": -84.3880 }
}
```

**Response (200 OK):**
```json
{
  "route": {
    "distance_miles": 781.3,
    "coordinates": [[-96.797, 32.777], ...],
    "geometry_polyline": ""
  },
  "fuel_stops": [
    {
      "station_id": 42,
      "name": "Circle K #4706716",
      "address": "SR-71 & SR-47",
      "city": "Shreveport",
      "state": "LA",
      "latitude": 32.513,
      "longitude": -93.747,
      "price_per_gallon": "3.199",
      "distance_from_start_miles": 188.5,
      "gallons_to_buy": 50.0,
      "cost_at_stop": 159.95
    }
  ],
  "total_fuel_gallons": 100.0,
  "total_fuel_cost": 314.90,
  "vehicle_max_range_miles": 500.0,
  "vehicle_mpg": 10.0
}
```

**Error Responses:**

| Status | Cause |
|--------|-------|
| 400 | Invalid input or geocoding failure |
| 422 | No feasible fuel plan (route exceeds range) |
| 502 | Routing API unavailable |
| 500 | Internal server error |

## Testing

Run the full test suite (uses SQLite, no external API calls needed):

```bash
# Windows
set USE_POSTGRES=False && python manage.py test routing -v 2

# PowerShell
$env:USE_POSTGRES="False"; python manage.py test routing -v 2
```

**Test coverage:**
- **Model tests** – `__str__`, ordering, field precision, nullable fields
- **Optimizer tests** – Haversine distance, route profile, station search, greedy planner (cheapest station, range limits, edge cases)
- **API tests** – Happy path (city strings + coordinates), invalid input, geocoding failure, routing API failure, short route (no stops)

## Postman Collection

Import `fuel_route_optimizer.postman_collection.json` into Postman for ready-to-use requests with example responses.

## Performance & Caching

- **Geocoding cache:** 1-hour TTL per city/address query (MD5-keyed)
- **Route cache:** 30-minute TTL per start/end coordinate pair
- **DB optimisations:** Indexes on `state`, `city`, `rack_id`, `latitude`, `longitude` + composite indexes on `(state, rack_id)` and `(latitude, longitude)`
- **Station search:** Bounding-box SQL filter → haversine distance refinement with sampled route vertices

## License

This project is for assessment purposes.
