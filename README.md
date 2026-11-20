# Fuel Route Planner

Django REST API that takes two USA locations and returns the driving route, cost-effective fuel stops for a 500-mile-range vehicle (10 mpg), and the total fuel cost.

- Fuel cost is the sum of purchases at 10 mpg (500-mile tank). The trip starts with an initial purchase at an origin station near the start, charged using the same rule as later stops (buy only enough to reach the destination or a cheaper reachable station, otherwise fill the tank). If the route distance is zero, `total_fuel_cost` is `"0.00"`.
- Request-time geocoding/routing use external providers (Open-Meteo by default for text, OSRM for routing). Station coordinates are prepared offline; the API never geocodes stations during a request.

Verified versions (PyPI, checked 2026-11-19): **Django 6.1.3**, **djangorestframework 3.18.3**, **requests 2.34.2**, **django-environ 0.14.0**. Django 6.1 requires Python >= 3.12.

## Requirements and installation

- Python >= 3.12 (developed with 3.13)
- Windows (project paths/commands below use Windows conventions)

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env   # edit SECRET_KEY and other values as needed
.venv\Scripts\python.exe manage.py migrate
```

Without `uv`: create/activate a venv and run `pip install -r requirements.txt`, then use `.venv\Scripts\python.exe` for `manage.py` commands.

## Environment variables

All values are read in `config/settings.py` and mirrored in `.env.example`.

| Name | Default | Description |
|---|---|---|
| `SECRET_KEY` | `local-development-only-not-a-secret` | Django secret key. |
| `DEBUG` | `False` | Django debug mode. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated allowed hosts. |
| `DB_NAME` | `<BASE_DIR>/db.sqlite3` | SQLite database path. |
| `ROUTING_URL` | `https://router.project-osrm.org/route/v1/driving` | OSRM-compatible routing base URL. |
| `ROUTING_TIMEOUT_SECONDS` | `8.0` | Routing request timeout (seconds). |
| `ROUTE_CACHE_SECONDS` | `86400` | Cache TTL for route results. |
| `ROUTE_SIMPLIFY_TOLERANCE` | `0.0002` | Douglas-Peucker tolerance for route geometry simplification. |
| `TEXT_GEOCODER` | `open-meteo` | Request-time text geocoder (must be `open-meteo`). |
| `OPEN_METEO_URL` | `https://geocoding-api.open-meteo.com/v1/search` | Open-Meteo geocoding endpoint. |
| `OPEN_METEO_TIMEOUT_SECONDS` | `10.0` | Open-Meteo request timeout (seconds). |
| `OPEN_METEO_MAX_CONSECUTIVE_FAILURES` | `20` | Abort threshold for `--all-cities` geocoding. |
| `PHOTON_URL` | `https://photon.komoot.io/api` | Photon endpoint (offline station geocoding). |
| `PHOTON_USER_AGENT` | `fuel-route-planner/1.0 (local development)` | User-Agent for geocoding requests. |
| `GEOCODE_CACHE_SECONDS` | `604800` | Cache TTL for resolved text locations (memory). |
| `GEOCODE_CACHE_FILE` | `<BASE_DIR>/data/geocode_cache.json` | Disk cache for Photon station geocoding. |
| `GEOCODE_DELAY_SECONDS` | `1.1` | Delay between Photon requests (rate limiting). |
| `PHOTON_CONNECT_TIMEOUT_SECONDS` | `5.0` | Photon connect timeout (seconds). |
| `PHOTON_TIMEOUT_SECONDS` | `20.0` | Photon read timeout (seconds). |
| `PHOTON_RETRIES` | `2` | Photon retry attempts. |
| `PHOTON_MAX_CONSECUTIVE_FAILURES` | `10` | Abort threshold for bulk Photon geocoding. |
| `STATION_SEARCH_RADIUS_MILES` | `5.0` | Max distance from route/start to consider a station. |
| `TANK_RANGE_MILES` | `500.0` | Vehicle range on a full tank (miles). |
| `MPG` | `10.0` | Vehicle fuel efficiency (miles per gallon). |
| `CSV_FILE` | `<BASE_DIR>/fuel-prices-for-be-assessment.csv` | Fuel price CSV path for import. |

## Data flow

1. **Import (offline):** `import_fuel_prices` loads US stations/prices from the CSV into `FuelStation` (idempotent, keeps lowest price per station).
2. **Geocoding (offline):** `geocode_fuel_stations` assigns `lat/lon` to stations (Photon bulk or Open-Meteo per city). Re-runs resume pending rows; the API never geocodes stations.
3. **API (request-time):** resolve `start`/`finish` (text via configured geocoder or `"lat,lon"`), fetch one driving route from OSRM, find stations near the route, plan stops/purchases, return route GeoJSON, stops, and `total_fuel_cost`.

## Management commands

Run with `.venv\Scripts\python.exe manage.py <command> ...`

### `import_fuel_prices`

Imports US fuel stations from the assessment CSV (idempotent).

- `--file PATH` (default: `CSV_FILE`): CSV file to import.

```powershell
.venv\Scripts\python.exe manage.py import_fuel_prices
```

### `geocode_fuel_stations`

Geocodes stations into `lat/lon`. Not used by the API.

- `--limit N` (default: `0`): max stations to process (Photon mode) or max cities (`--all-cities`).
- `--force`: re-geocode rows that already have coordinates.
- `--city CITY,ST` (repeatable): geocode all stations in a city with Open-Meteo (no Photon delay).
- `--all-cities`: geocode every pending distinct city/state with Open-Meteo (resumable; aborts after `OPEN_METEO_MAX_CONSECUTIVE_FAILURES` consecutive failures).

```powershell
# Photon bulk (rate-limited, resumable, cached in GEOCODE_CACHE_FILE)
.venv\Scripts\python.exe manage.py geocode_fuel_stations --limit 100

# Specific cities (Open-Meteo)
.venv\Scripts\python.exe manage.py geocode_fuel_stations --city Phoenix,AZ --city Tucson,AZ

# All pending cities
.venv\Scripts\python.exe manage.py geocode_fuel_stations --all-cities
```

## Running and testing

```powershell
.venv\Scripts\python.exe manage.py runserver
.venv\Scripts\python.exe manage.py test fuel -v 2
```

## API

### `POST /api/route/`

Request body (JSON): `start` and `finish` as `"lat,lon"` or free text (city/state, e.g. `"City, ST"`).

**Example (success, real execution with mocked routing and sample stations):**

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d "{\"start\": \"33.4484,-112.0740\", \"finish\": \"31.8000,-110.0000\"}"
```

```json
{
  "start": "33.4484,-112.0740",
  "finish": "31.8000,-110.0000",
  "route": {
    "type": "Feature",
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [-112.074, 33.4484],
        [-111.5, 33.0],
        [-110.9747, 32.2226],
        [-110.5, 32.0],
        [-110.0, 31.8]
      ]
    },
    "properties": {}
  },
  "distance_miles": 186.41,
  "stops": [
    {
      "name": "Origin Stop",
      "address": "I-10 Exit 1",
      "city": "Phoenix",
      "state": "AZ",
      "lat": 33.4484,
      "lon": -112.074,
      "price_per_gallon": "3.19900000",
      "miles_from_start": 0.0,
      "gallons_purchased": 18.64,
      "cost": "59.63"
    }
  ],
  "total_fuel_cost": "59.63"
}
```

**Example (zero distance, real execution):**

```json
{
  "start": "33.4484,-112.0740",
  "finish": "33.4484,-112.0740",
  "route": {
    "type": "Feature",
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [-112.074, 33.4484],
        [-112.074, 33.4484]
      ]
    },
    "properties": {}
  },
  "distance_miles": 0.0,
  "stops": [],
  "total_fuel_cost": "0.00"
}
```

#### Error format

All API errors use a uniform JSON body with a stable `code` and a fixed client `message` (original exception details are logged server-side only):

```json
{
  "error": {
    "code": "<stable_code>",
    "message": "<fixed message>",
    "details": {}
  }
}
```

#### Error codes

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed input or missing/invalid parameters. |
| 400 | `invalid_location` | Coordinates or resolved location outside the supported continental USA area (reason in `details`). |
| 404 | `location_not_found` | Free-text location could not be resolved to a USA location. |
| 422 | `route_not_feasible` | Request is valid but cannot be fulfilled (no drivable route, or no reachable/origin fuel station). |
| 502 | `external_service_bad_response` | External service returned an invalid or unexpected response. |
| 503 | `external_rate_limited` | External service rate limit exceeded (may include `Retry-After` response header). |
| 503 | `external_service_unavailable` | External service unavailable or upstream server error. |
| 504 | `external_service_timeout` | External service timed out. |
| 500 | `server_misconfigured` | Server configuration error. |
| 500 | `internal_error` | Unexpected internal error. |

**Error examples (real execution):**

400 `invalid_location`:
```json
{
  "error": {
    "code": "invalid_location",
    "message": "Invalid location.",
    "details": {
      "input": "51.5,-0.1",
      "reason": "outside_continental_us",
      "supported_area": "continental USA (lat 24.4-49.4, lon -125.0 to -66.9)"
    }
  }
}
```

400 `bad_request` (missing field):
```json
{
  "error": {
    "code": "bad_request",
    "message": "Bad request.",
    "details": {
      "finish": ["This field is required."]
    }
  }
}
```

404 `location_not_found`:
```json
{
  "error": {
    "code": "location_not_found",
    "message": "Location not found.",
    "details": { "input": "Atlantis" }
  }
}
```

422 `route_not_feasible`:
```json
{
  "error": {
    "code": "route_not_feasible",
    "message": "Route is not feasible.",
    "details": { "reason": "no_origin_station" }
  }
}
```

502 `external_service_bad_response`:
```json
{
  "error": {
    "code": "external_service_bad_response",
    "message": "External service returned an invalid response.",
    "details": {}
  }
}
```

503 `external_rate_limited` (with `Retry-After: 120` header when provided by the provider):
```json
{
  "error": {
    "code": "external_rate_limited",
    "message": "External service rate limit exceeded.",
    "details": {}
  }
}
```

503 `external_service_unavailable`:
```json
{
  "error": {
    "code": "external_service_unavailable",
    "message": "External service is unavailable.",
    "details": {}
  }
}
```

504 `external_service_timeout`:
```json
{
  "error": {
    "code": "external_service_timeout",
    "message": "External service timed out.",
    "details": {}
  }
}
```

500 `internal_error` (no trace or internal detail in body, even with `DEBUG=True`):
```json
{
  "error": {
    "code": "internal_error",
    "message": "Internal server error.",
    "details": {}
  }
}
```

### `GET /api/route/map/`

HTML map view (Leaflet). Query parameters: `start`, `finish` (same values as the API). Returns the same HTTP status and error `code` as the API on failure, rendered as a safe HTML message (no traces/internal details).

Example: `http://127.0.0.1:8000/api/route/map/?start=33.4484,-112.0740&finish=31.8000,-110.0000`

## Architecture

- **`fuel/views.py`**: thin HTTP layer (request parsing/validation delegation, service call, response). `RouteAPIView` (JSON) and `route_map` (HTML) use the same service; domain errors map to consistent statuses/codes.
- **`fuel/serializers.py`**: request validation (`start`, `finish`).
- **`fuel/services/planner.py`**: orchestration (`resolve_location` -> `get_route` -> `nearby_stations`/origin selection -> `plan_fuel_stops` -> response shape).
- **`fuel/services/geocoding.py`**: request-time location resolution and provider clients (`OpenMeteoClient` for requests, `PhotonClient` for offline ETL/cache). Input/area validation and external-failure classification.
- **`fuel/services/routing.py`**: OSRM routing, geometry simplification, caching, and external-failure/routability classification.
- **`fuel/services/stations.py`**: station lookup near the route and origin selection (data access/projection).
- **`fuel/services/fuel.py`**: pure fuel planning and cost calculation (no Django/DB/network).
- **`fuel/exceptions.py`**: domain exception hierarchy, stable error catalog, and DRF exception handler (uniform error translation/logging).
- **`fuel/models.py`**: `FuelStation` persistence.
- **`fuel/management/commands/`**: offline, resumable/idempotent import and geocoding commands.

## Decisions and limitations

- **Offline station geocoding:** the API never geocodes stations at request time. Station coordinates come from `geocode_fuel_stations` (Photon bulk or Open-Meteo by city/`--all-cities`, resumable and cached on disk for Photon).
- **Route proximity:** candidate stations are those within `STATION_SEARCH_RADIUS_MILES` of the route geometry (bounding-box prefilter plus segment projection). Each stop is assigned a mile marker along the route; the origin station is the nearest candidate to the start within the search radius.
- **Fuel/cost model:** 10 mpg, `TANK_RANGE_MILES` tank (500 mi). Initial purchase is made at the origin station and included in `total_fuel_cost` using the same buy rule as stops (sufficient for destination/cheaper reachable station, else fill). A zero-distance trip costs 0.
- **Unroutable input:** OSRM `NoRoute`/`NoSegment` (e.g. points not snappable to the road network) are treated as `422 route_not_feasible` (valid input but not fulfillable), not as an external service failure.
- **External rate limiting:** provider HTTP 429 is mapped to `503 external_rate_limited` and forwards `Retry-After` when supplied.
- **Provider failures:** timeouts -> 504, connection/upstream 5xx/unavailable -> 503, invalid/unexpected responses -> 502. Client messages are fixed per code; request/exception details are logged server-side.
- **Geocoding coverage/accuracy:** station geocoding may be approximate (e.g. city-centroid in Open-Meteo modes) and depends on imported/geocoded data and `STATION_SEARCH_RADIUS_MILES`; sparse coverage can yield `route_not_feasible`.
- **Routing/geocoding providers:** defaults use the public OSRM demo and Open-Meteo; availability, quotas, and terms are governed by those services.

No secrets are stored in the repository; configure sensitive values (e.g. `SECRET_KEY`) in `.env`.
