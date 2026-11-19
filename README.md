# Fuel Route Planner (Backend Django assessment)

A small Django REST API that takes two USA locations and returns the driving route,
cost-effective fuel stops for a 500-mile-range vehicle (10 mpg), and the total fuel cost.

Verified versions (PyPI, checked 2026-11-19): **Django 6.1.3**, **djangorestframework 3.18.3**.
Django 6.1 requires Python >= 3.12; this project was developed with Python 3.13.

---

## 1. Setup

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
cp .env.example .env            # then edit SECRET_KEY / PHOTON_USER_AGENT
.venv/Scripts/python.exe manage.py migrate
```

(If you do not use `uv`: `python -m venv .venv`, activate it, then `pip install -r requirements.txt`.)

## 2. Load the data

```bash
.venv/Scripts/python.exe manage.py import_fuel_prices
.venv/Scripts/python.exe manage.py geocode_fuel_stations --limit 100
```

`import_fuel_prices` is idempotent: run it as often as you like (no duplicates are created).
Free-text locations in API requests are resolved by **Open-Meteo** (keyless, city-level) and
cached in memory; `"lat,lon"` inputs skip geocoding entirely.
`geocode_fuel_stations` is the **only** code path that geocodes stations. It uses Photon,
is rate limited to ~1 request/second (`GEOCODE_DELAY_SECONDS`), caches every result
(including "not found") in `data/geocode_cache.json`, and uses `bulk_update`. The full import
has ~6,626 US stations, so a complete sequential geocode takes roughly 2-2.5 hours. Use
`--limit` for a demo and `--force` to retry already-geocoded rows; interrupted runs can be
resumed by re-running without `--force`. The API never geocodes a station during a request.

For the NY -> LA verification below, stations in the cities along the route were geocoded
with a small one-off shell snippet and `STATION_SEARCH_RADIUS_MILES=40` (see limitation #1).

## 3. Run

```bash
.venv/Scripts/python.exe manage.py runserver
```

Tests:

```bash
.venv/Scripts/python.exe manage.py test fuel -v 2
```

---

## 4. API

### `POST /api/route/`

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "40.7128,-74.0060", "finish": "34.0522,-118.2437"}'
```

Free text also works (city/state names, e.g. `"City, ST"`; street addresses are not supported):

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "New York, NY", "finish": "Los Angeles, CA"}'
```

Response (abbreviated):

```json
{
  "start": "40.7128,-74.0060",
  "finish": "34.0522,-118.2437",
  "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.71], "..."]}, "properties": {}},
  "distance_miles": 2794.2,
  "stops": [
    {
      "name": "S&G #88", "address": "I-80, EXIT ...", "city": "Toledo", "state": "OH",
      "lat": 41.6, "lon": -83.5, "price_per_gallon": "3.00900000",
      "miles_from_start": 553.48, "gallons_purchased": 50.0, "cost": "150.45"
    }
  ],
  "total_fuel_cost": "738.61"
}
```

Errors: `400` for invalid/non-US/missing input, `422` when no station is reachable within
500 miles, `502` when an external service fails.

### `GET /api/route/map/`

Simple Leaflet map (CDN) showing the route and numbered fuel stops:

```
http://127.0.0.1:8000/api/route/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA
```

Every response also has an `X-Response-Time-Ms` header and a timing log line.

---

## 5. CSV findings and assumptions

The supplied `fuel-prices-for-be-assessment.csv` has **8,151 rows**, 7 columns
(`OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price`),
UTF-8 encoding, CRLF line endings, comma delimiter with quoted fields, and no missing values.
Prices are decimal strings with 3-8 decimal places ($2.687-$6.399).

- **No latitude/longitude columns.** Hence the separate one-time geocoding command.
- 620 rows are Canadian provinces (ON, AB, BC, ...); they are filtered out (7,531 US rows).
- 26 rows are exact duplicates and the same OPIS ID appears with several prices (different
  racks/grades). Import keeps the **cheapest** price per OPIS ID: **6,626 US stations**.
- `City` values have trailing spaces; they are trimmed on import.
- Locations are validated against a continental-US bounding box (lat 24.4-49.4,
  lon -125.0 to -66.9). `lat,lon` inputs are checked locally; free text is resolved with
  Open-Meteo, filtered to `country_code=US` and then checked against the same box.
  No reverse-geocoding call is made.
- **Initial tank assumption:** the vehicle starts with a full 50-gallon tank. Fuel already
  in that tank is treated as already paid for, so it contributes $0 to the reported total.

---

## 6. Design decisions

| Topic | Decision |
| --- | --- |
| Framework | Django 6.1.3 + DRF 3.18.3, SQLite (no PostGIS needed). |
| Routing | Free public OSRM demo server, configurable via `ROUTING_URL`. One request returns distance + full geometry. |
| Geocoding | Open-Meteo for free-text request input (keyless, city-level, Django-cache-backed); Photon only for the offline station command (file-cache-backed). `TEXT_GEOCODER=photon` switches the request-time provider back. |
| Caching | Route and resolved-location results in Django's local-memory cache; identical requests make **zero** external calls. |
| Geometry | Full OSRM geometry is simplified with Douglas-Peucker (~34k points -> ~4k) to keep responses small. The 5-mile station match uses a cheap equirectangular projection in pure Python (no Shapely/pyproj dependency). |
| DB access | One indexed bounding-box query for candidate stations; no per-station external calls, no N+1. |
| Money | `Decimal` throughout; gallons and stop costs rounded to cents. |

### External calls per request

| Input | Geocoding | Routing | Total |
| --- | ---: | ---: | ---: |
| `lat,lon` | 0 | 1 | **1** |
| Free text | 2 Open-Meteo (one per endpoint) | 1 OSRM | **3** |
| Cached repeat | 0 | 0 | **0** |

Leaflet tiles load in the browser, not from the backend, so they are not counted here.
One routing call is the ideal case; three total calls for free text is inside the
assessment's accepted 2-3 range.

### Fuel algorithm (`fuel/services/fuel.py`, pure/no I/O)

Starting with a full tank, at each stop:

1. If the destination is reachable with the fuel on board, buy only the gallons still needed
   (or nothing) and finish.
2. Otherwise, if a station with a lower price is reachable with a full tank, go to the
   cheapest such station and buy only enough fuel to get there.
3. Otherwise fill the tank at the furthest reachable station and continue.

Stations behind the vehicle are ignored. `NoStationInRangeError` (HTTP 422) is raised if a
gap larger than 500 miles cannot be covered. Tests cover short/exact-range trips, no
stations, cheaper stations ahead vs. behind, fill-up, and range-limit boundaries.

Measured on the real NY -> LA route (2,794 miles, 40-mile station radius in this demo):
first request ~1.9 s (dominated by OSRM), cached repeat ~18 ms, 7 stops, $738.61.
The free-text variant (`"New York, NY"` -> `"Los Angeles, CA"`) also returns 200 with
7 stops ($738.51) using 2 Open-Meteo calls plus 1 OSRM call.

---

## 7. Known limitations

1. Station addresses are highway exits, which Photon usually cannot resolve. The importer
   falls back to a **city centroid**, so station coordinates are approximate. The default
   match radius is 5 miles; with city-level geocodes raise `STATION_SEARCH_RADIUS_MILES`
   (20-40 works for the demo). If you need precise stops, geocode with a paid/accurate
   provider once and store the results.
2. The full station geocode is intentionally slow (~2-2.5 h at 1 req/sec) to stay inside
   the public Photon demo's fair-use limits; `Retry-After` is honored, the disk cache makes
   reruns/resumes cheap, and the command aborts after `PHOTON_MAX_CONSECUTIVE_FAILURES`
   (default 10) so an outage does not burn through all stations. For a much faster bulk
   pass, point `PHOTON_URL` at a private Photon instance and lower
   `GEOCODE_DELAY_SECONDS`. It has not been run to completion in this checkout.
3. The public OSRM demo server is rate-limited and has no SLA. For production, use your own
   OSRM instance or OpenRouteService with `ORS_API_KEY` support added to `services/routing.py`.
4. Only the continental USA is supported; Alaska, Hawaii and Canada are intentionally out of
   scope. Coordinates are not reverse-geocoded, so a point in the ocean inside the bbox is
   accepted.
5. No traffic, road grade, wind, tank reserve, or different fuel grades are modelled. The
   price used is the cheapest row per OPIS ID in the static CSV.
6. `LocMemCache` is per-process (fine for `runserver`, not shared across workers). Use Redis
   if you deploy with multiple workers. There is no authentication or API rate limiting.
7. A GitHub remote and the demo video are handled separately from this checkout.
8. Open-Meteo resolves city/town names only, not street addresses or highway exits. Use
   `"lat,lon"` for exact points; set `TEXT_GEOCODER=photon` (or a paid provider) if
   address-level free text is required.
