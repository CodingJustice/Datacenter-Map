# Datacenter Map

Interactive U.S. map of public data-center facilities, nuclear sites, nearby
power plants, and named water-source reference points.

Market-hub placeholders are no longer included. The default data-center layer
comes from public facility records.

## Files

- `us_ai_datacenters_nuclear_map.html` defines the page structure, CSP,
  controls, and script loading order.
- `styles.css` controls the INCOMPAS-themed map, marker, popup, and responsive
  layout styling.
- `app.js` powers the Leaflet map, safe DOM rendering, filters, popups, lazy
  power-plant layer, heatmap, and proximity lines.
- `ADA_ACCESSIBILITY_PRD.md`, `ADA_CERTIFICATION_PATH.md`, and
  `ACCESSIBILITY_CONFORMANCE_REPORT.md` document the ADA/WCAG requirements,
  certification pathway, draft conformance evidence, and remaining sign-offs.
- `accessibility.html` is the public accessibility-statement page. Replace its
  placeholder feedback contact before production launch.
- `script.py` refreshes public source data and writes CSV exports, split
  JSON/GeoJSON map data, and a private SQLite test database.
- `server.py` serves only public map assets with security headers, restrictive
  CORS, and rate limiting.
- `data/map-summary.json` contains generated counts, source metadata, and
  source/change records.
- `data/*.geojson` contains the generated Leaflet point layers.
- `private/map-data.sqlite` is an ignored local SQLite test export for
  production-readiness experiments. It is not served by `server.py`.
- `water_sources.csv` contains the named water-reference points used for
  proximity calculations.

## Current Data

- 422 public data-center facility records from Compute Atlas.
- 15,975 operating and planned power-plant records from EIA-860M plus Compute
  Atlas power-generation project records.
- 63 nuclear operating, planned, restart, or SMR-related records.
- 41 named water-source reference points for proximity context.

Sources:

- Compute Atlas API: https://www.compute-atlas.com/api
- EIA-860M: https://www.eia.gov/electricity/data/eia860m/index.php
- USGS National Hydrography Dataset: https://www.usgs.gov/national-hydrography/national-hydrography-dataset
- Natural Earth: https://www.naturalearthdata.com/

## Run The Python Data Backend

Refresh source data, rebuild CSVs, write split JSON/GeoJSON files, and create a
private SQLite test database:

```powershell
python script.py
```

To request fresh Compute Atlas JSON and EIA data where network access is
available:

```powershell
python script.py --refresh-compute-atlas --refresh-eia
```

Useful options:

```powershell
python script.py --compute-atlas-json compute-atlas-data-centers.json --water-sources water_sources.csv --output-dir data --output-db private/map-data.sqlite
```

Skip the SQLite test export when you only need static map files:

```powershell
python script.py --skip-sqlite
```

## INCOMPAS Webpage Integration

The current app is prepared as a static Leaflet experience for integration into
the INCOMPAS webpage. Keep the browser-facing contract simple:

- Host `us_ai_datacenters_nuclear_map.html`, `styles.css`, `app.js`, and
  `data/*.json` / `data/*.geojson` from the same INCOMPAS origin whenever
  possible.
- Do not publish raw CSVs, cached Excel workbooks, Compute Atlas source JSON,
  Python scripts, private folders, certificates, logs, or SQLite files.
- Coordinate the production CSP with the parent INCOMPAS site. If this map is
  embedded as an iframe, replace `frame-ancestors 'none'` with the exact
  INCOMPAS parent origin in both the HTML meta policy and server/proxy headers.
- Keep external allowlists narrow: Leaflet CDN scripts/styles, approved map tile
  image hosts, same-origin data connections, and no forms/workers/objects.
- The SQLite export is for testing a future backend data store. It should be
  opened read-only by any API prototype and converted to a managed database or
  immutable artifact pipeline before final production.

## Serve The Map

Run the hardened local server and open the URL it prints:

```powershell
python server.py --host 127.0.0.1 --port 8000
```

For HTTPS in production, place this app behind a TLS-terminating proxy or run
the local server with a certificate and key:

```powershell
python server.py --host 127.0.0.1 --port 8443 --certfile cert.pem --keyfile key.pem
```

## Python API Research

For a larger production API, FastAPI is the recommended Python service layer.
The official FastAPI docs describe it as a high-performance framework based on
Python type hints, with OpenAPI generation, CORS middleware, API key security
helpers, async support, and deployment guidance for HTTPS behind a TLS
termination proxy:

- https://fastapi.tiangolo.com/
- https://fastapi.tiangolo.com/tutorial/cors/
- https://fastapi.tiangolo.com/reference/security/
- https://fastapi.tiangolo.com/deployment/concepts/
- https://fastapi.tiangolo.com/deployment/https/

This static project remains dependency-light for now. At larger scale, move the
`script.py` normalization logic behind FastAPI endpoints with Pydantic response
models, Redis-backed rate limiting, least-privilege database access, parameterized
queries, and prebuilt GeoJSON/vector-tile artifacts for large layers. The new
SQLite export gives that path something realistic to test before choosing the
final production data store.

## Security And Performance

- No Java loader is used. The browser fetches JSON/GeoJSON, not executable data.
- The large power layer is lazy-fetched only when the Power toggle is enabled
  and renders only power markers in the padded visible viewport, capped to avoid
  long main-thread stalls on slower devices.
- Data-center marker refreshes use a render-key cache so repeated zoom/move
  events do not clear and rebuild layers when filter state and visible layer
  mode have not changed.
- Proximity lines render only for data centers near the current viewport.
- Upstream collection uses allowlisted HTTPS hosts, TLS verification, response
  size limits, JSON-shape validation, and workbook zip-bomb checks.
- CSV exports protect text fields from spreadsheet formula injection.
- A private SQLite test database is generated with parameterized inserts,
  metadata/change-record tables, and indexes for common map queries.
- The static server allows only `GET`, `HEAD`, and `OPTIONS`; upload/write
  methods are rejected.
- The static server serves only the HTML, CSS, JS, and generated `data/`
  artifacts. Raw CSVs, Excel caches, scripts, keys, and local private files are
  not public routes.
- The browser renders data through DOM APIs and `textContent`; source links are
  limited to `http` and `https` with `noopener noreferrer`.
- CSP restricts scripts, styles, map tile images, fonts, frames, forms, objects,
  workers, and browser connections.
- CORS is same-origin by default. Cross-origin access must be explicitly
  allowlisted with `MAP_ALLOWED_ORIGINS`.
- Rate limiting defaults to 300 requests per minute per client address and can
  be adjusted with `--rate-limit` or `MAP_RATE_LIMIT_PER_MINUTE`.
- Admin tools and upload endpoints are not exposed. Data refresh is a local CLI
  operation.
- SQLite is a local test export only and is not served. If a production database
  is added, use least-privilege service accounts and parameterized queries only.
- Secure headers include CSP, `nosniff`, `DENY` framing, referrer policy,
  permissions policy, COOP, CORP, and HSTS when served over HTTPS.
- Dependency versions are pinned in the HTML. As of this update, the pinned
  Leaflet, MarkerCluster, and Leaflet.heat versions match the latest stable npm
  package releases.

## Data Notes

The map uses public, source-cited records. It is not a proprietary commercial
facility registry, and exact locations depend on what each public source
discloses. Water proximity uses named surface-water reference points and any
reported water/cooling notes in the data-center records; it is not a legal
water-rights or utility-service determination.
