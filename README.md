# Geocoding Workbench

Multi-user web application that turns messy address files (CSV, Excel, text; 200–200,000 rows) into
latitude/longitude with an explainable **confidence score (0–100)** per row, lets a team review and
confirm results one by one or in bulk, and exports confirmed results as CSV.

- **Backend:** FastAPI, SQLAlchemy/Alembic, PostgreSQL, Redis + arq workers (`backend/`)
- **Frontend:** React + TypeScript (Vite), Mantine, TanStack Query/Table/Virtual, Leaflet (`frontend/`)
- **Spec & design:** `openspec/changes/geocoding-workbench/` (proposal, design, specs, tasks)

## How it works

```
upload ─▶ column mapping ─▶ dedupe ─▶ T1 rule-based repair ─▶ T2 AI repair* ─▶ geocode ─▶ score ─▶ review ─▶ CSV
                                       (country aliases,        (OpenAI, only     (GeoNames offline;
                                        postcode formats,        unresolved rows,  OpenCage for
                                        fuzzy city match,        re-validated)     street rows)
                                        cross-field checks)
```

- **Coordinates never come from the AI.** OpenAI only repairs inputs, suggests column mappings and
  picks between geocoder candidates. Every AI suggestion is re-checked against the gazetteer and
  discarded if it does not validate.
- **Confidence is deterministic.** Start at 100, subtract a penalty per measurable signal (country
  inferred −15, postcode repaired −5, fuzzy city −5…−20, postcode/city far apart −15/−35, AI repair −10, …).
  The precision level caps the maximum (city-level ≤ 70, street given but only postcode found ≤ 85).
  Bands: High ≥ 90, Medium ≥ 70, Low ≥ 40, Failed < 40. Each row lists its reasons.
  Weights are versioned and editable by admins (*Scoring* page).
- **Precision is separate from confidence:** rooftop / street / postcode / city / region / country / manual.

## Quick start (Docker)

```bash
cp .env.example .env            # add OPENAI_API_KEY and GEOCODER_API_KEY (both optional)
docker compose up -d --build    # postgres, redis, api, worker, web
docker compose exec api python -m app.cli import-geonames --download     # ~400 MB, 10–20 min, once
docker compose exec api python -m app.cli create-admin admin            # prompts for a password (≥ 12 chars)
```

Open <http://localhost:8080>, sign in, and add users under **Users**.

### `.env`

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Enables AI repair. Empty = rules only; nothing is sent to OpenAI. |
| `OPENAI_BASE_URL`, `OPENAI_CHAT_DEPLOYMENT`, `OPENAI_API_VERSION` | Azure-style gateway (e.g. the corporate API-Management endpoint): requests go to `{BASE_URL}/deployments/{DEPLOYMENT}/chat/completions?api-version=…`. Leave empty for api.openai.com (then `OPENAI_MODEL` is used). |
| `OPENAI_AUTH_STYLE` | `api-key` (header `api-key`), `subscription-key` (`Ocp-Apim-Subscription-Key`) or `bearer`. The corporate gateway accepts `api-key` (verified). |
| `OPENAI_TIMEOUT_S`, `OPENAI_CONCURRENCY`, `OPENAI_RPM` | Per-request timeout, parallel requests per worker, shared rate limit. |
| `OPENAI_BATCH_ENABLED` | Batch API for very large jobs. Default on for api.openai.com, **off for gateways** (the corporate gateway returns 404 for `/files` and `/batches`); jobs then use concurrent requests. |
| `GEOCODER_PROVIDER` / `GEOCODER_API_KEY` | `opencage` (default). Empty key = offline only (street rows located at postcode/city level). |
| `GEOCODER_RPS` | Requests/second allowed by your OpenCage plan (shared by all workers). |
| `GOOGLE_MAPS_API_KEY` | Enables address lookup and road distance on the **Distance** page only (see below). Empty = Distance page works with coordinates (straight-line distance) only. |
| `GOOGLE_ROUTES_ENABLED`, `GOOGLE_RPS`, `GOOGLE_CACHE_TTL_HOURS`, `GOOGLE_LANGUAGE` | Road distance on/off, global Google request rate, Redis cache TTL (clamped to 720 h = 30 days), language of resolved addresses. |
| `DISTANCE_PER_USER_PER_MIN`, `DISTANCE_MAP` | Per-user calculation limit (HTTP 429 above it). `DISTANCE_MAP=leaflet` draws Google-resolved points and routes on the OSM map; default `off` until you have checked Google's terms for your region. |
| `COOKIE_SECURE` | `true` behind HTTPS (production). `false` only for plain-http testing. |
| `MAX_UPLOAD_MB`, `MAX_ROWS`, `AI_BATCH_THRESHOLD`, `FILE_RETENTION_DAYS` | Limits and thresholds. |

API keys are read only by the backend; nothing in `.env` reaches the browser.

### Gazetteer (GeoNames)

`import-geonames` loads `allCountries.zip` (postal codes) and `cities500.zip` (places with
alternate names) from <https://download.geonames.org/export/> and swaps them in atomically, so it can
be re-run to refresh data while the app is running. **If the server cannot reach geonames.org** (for
example behind a corporate proxy), download the two files elsewhere and run:

```bash
docker compose cp ./geonames/. api:/data/geonames/
docker compose exec api python -m app.cli import-geonames --source-dir /data/geonames
```

Add `--full` (or place `GB_full.csv.zip`, `NL_full.csv.zip`, `CA_full.csv.zip` next to the others)
for full UK/NL/CA postcodes; otherwise those countries resolve at outward-code/4-digit/FSA level.
For development without GeoNames there is a tiny demo gazetteer: `python -m app.cli load-demo-gazetteer`.

Attribution: gazetteer data © GeoNames, CC BY 4.0; OpenCage results © OpenStreetMap contributors (ODbL).
The UI shows both. Map tiles default to `tile.openstreetmap.org`; for production traffic set
`VITE_TILE_URL` (build arg of the `web` image) to a tile service you are licensed to use.

## What data leaves the server

| Destination | When | What exactly |
|---|---|---|
| OpenAI | AI repair enabled for the job **and** a row could not be resolved by the rules; ambiguous candidates; column-mapping help when headers are unrecognizable | Street, house number, city, postcode, country of that row + an opaque id. Candidate labels for disambiguation. Headers + ≤ 5 sample rows for mapping help. **No other columns.** |
| OpenCage | Rows with a street; rows the offline gazetteer cannot place | The structured address (street, house number, postcode, city, country). Results are cached and shared, so an address is never paid for twice. |
| Google (Geocoding API, Routes API) | Distance page only: an origin/destination entered as an address; road distance between the two resolved points | The address fields typed for that side; the two coordinate pairs for the route. **Never used for jobs.** |

Turn AI repair off per job in the upload wizard to keep everything except street lookups on the server.

## Distance calculator

The **Distance** page (header nav) computes the distance between an origin and a destination. Each one
is either a postal address (any mix of street, house number, postcode, city, state/region, country,
or one free-text line) or decimal coordinates (paste `"lat, lon"` into either field). It shows:

- the **straight-line** (great-circle) distance, always;
- the **road distance and driving time** (Google Routes API, `DRIVE`), when a Google key is set;
- what each side resolved to (address, coordinates, precision, partial-match warning), plus other
  matches you can switch to; km/mi toggle; copy-to-clipboard summary.

Coordinates-only calculations are shareable via the URL (`/distance?o=52.52,13.405&d=48.137,11.575`);
addresses are never put in the URL.

**Google retention rules.** Google's terms do not allow storing or exporting geocodes, so Google is
*not* a job geocoder (`GEOCODER_PROVIDER=google` is rejected at startup). Distance results are never
written to PostgreSQL, never enter the shared geocode cache, and never appear in exports. They are
cached in Redis only, for `GOOGLE_CACHE_TTL_HOURS` (max 30 days).

**Google Cloud setup.**
1. Create a project, enable billing, and enable **Geocoding API** and **Routes API**.
2. Create an API key. Restrict it to those two APIs (*API restrictions*) and to the server's egress IP
   (*Application restrictions → IP addresses*). The key is used only by the backend.
3. Set a **budget alert** and per-day **quota caps** for both APIs. Each calculation costs at most
   2 geocoding requests + 1 route request; cache hits and coordinate inputs cost nothing.
4. Put the key in `.env` as `GOOGLE_MAPS_API_KEY` and restart `api`.

## Review workflow

- **Review tab:** virtualized grid (server-side paging/sort/filter, fine at 200k rows), filters by band,
  status, correction type, precision, confidence range, errors, free-text search.
- Keyboard: `J`/`K` move, `C` confirm, `R` reject, `Enter` details.
- **Row details:** original vs. normalized values, confidence reasons, corrections (rule vs. AI),
  map with draggable pin, alternative candidates (click to use), edit input & re-run, full history.
- **Bulk:** confirm/reject/reset *all rows matching the filter* (or the checked rows). Above 100 rows a
  dialog shows the exact count; the server re-checks the count before applying.
- Statuses: `pending → confirmed | edited | rejected`. `edited` (alternative picked, pin moved, input
  re-run) counts as confirmed for export. Every action is in the audit log.
- **Export:** confirmed only (default), all rows with status, or the current filter. UTF-8 with BOM,
  original columns first, postcodes kept as text, spreadsheet-formula injection neutralized.

## Calibrating the confidence score

The default penalties are informed guesses. Label 200–500 real rows (`street,house_number,city,postcode,country,true_lat,true_lon`) and run:

```bash
docker compose exec api python -m app.cli calibrate /data/labeled.csv
```

It prints, per band, the share of rows within 1 / 5 / 25 km of the truth. Adjust weights on the
*Scoring* page (creates a new version) and re-score finished jobs from their *Progress* tab.

## Development

```bash
make dev-infra                      # postgres + redis in docker
cd backend && uv venv .venv && uv pip install -e ".[dev]" && cd ..
make migrate && make demo-gazetteer
COOKIE_SECURE=false make backend    # http://localhost:8000/api/docs
make worker                         # second terminal
cd frontend && npm install && cd .. && make frontend   # http://localhost:5173 (proxies /api)
make test                           # backend tests (needs postgres + redis) + frontend unit tests/typecheck/build
make lint
```

## Measured performance

Docker stack on one VM, worst case for deduplication (200,000 rows, all unique street addresses,
offline geocoding):

| Step | Result |
|---|---|
| Upload + parse + preview (8.6 MB CSV) | 1.5 s |
| Full processing (ingest → normalize → geocode → score) | ~5 min |
| Grid page (200 rows), incl. deep cursor pages and filters | 17–117 ms |
| Bulk-count preview / bulk-confirm all 200k rows | 6 ms / 11.5 s |
| Map payload, all 200k points | 1.5 s |
| CSV export: first byte / complete (33 MB) | 26 ms / 8 s |

Real files usually contain many duplicate addresses, which are processed once. With an external
geocoder, street-level throughput is bounded by `GEOCODER_RPS`.
