## Context

This is a new project with no existing code. Users upload address files (200–200,000 rows) with city, postcode, country and sometimes a street. The data is regularly dirty: misspelled cities, corrupted country codes (for example "GE" meaning Germany), postcodes damaged by spreadsheets. Several users work in the system; admins oversee everyone's jobs. Users need coordinates, a trustworthy per-row confidence number, a fast review workflow, and CSV export.

Hard constraints from stakeholders:
- Backend FastAPI, frontend React. Multi-user with username/password.
- OpenAI is the AI provider (key in `.env`).
- Coordinates must be exportable and storable, which rules out Google Geocoding.

## Goals / Non-Goals

**Goals:**
- Process a 200k-row file end-to-end in under ~30 minutes for typical data (dominated by unique-address count, not row count).
- Make confidence explainable and reproducible, so reviewers can bulk-confirm High rows safely and focus on the rest.
- Keep most rows offline: no third-party call for clean postcode/city rows.
- Keep OpenAI and provider costs bounded by deduplication, tiering and caching.

**Non-Goals:**
- Rooftop accuracy for rows without a street (impossible from city/postcode input).
- Reverse geocoding, routing, or map-based address search.
- SSO/OAuth, self-registration, email password reset (admin resets only).
- Excel (.xlsx) export; CSV only in this change.
- Teams/workspaces beyond the owner + admin visibility model.
- A self-hosted Nominatim/Pelias instance.

## Decisions

### D1. Architecture: API + worker + Postgres + Redis

```
 React (Vite, TS) ──REST/SSE──▶ FastAPI (api) ──enqueue──▶ Redis ──▶ arq worker(s)
                                     │                                    │
                                     └──────────── PostgreSQL ◀───────────┘
                         users · sessions · jobs · rows · address_keys · candidates
                         audit_log · geocode_cache · geonames_postal · geonames_place
                         geonames_altname · scoring_config
```
- **arq** rather than Celery: async-native like FastAPI, small, Redis-only, and supports retries and cron (file retention cleanup). Celery was rejected as heavier, with poorer asyncio support. RQ is sync-only.
- One codebase, `backend/app`, with two entrypoints (`uvicorn app.main:app`, `arq app.worker.WorkerSettings`) so that domain code (normalization, scoring) is shared and tested once.
- Docker Compose services: `api`, `worker`, `web` (Vite dev / nginx prod), `postgres` (16), `redis` (7).

### D2. Data model centered on unique address keys

```
jobs 1─* rows *─1 address_keys 1─* candidates
            │
            └─ review fields (status, lat/lon override, version, reviewed_by/at)
```
- `rows`: job_id, row_index, `raw` (JSONB, all original columns, ordered), mapped raw fields, `address_key_id`, review status, manual override lat/lon, `version` (optimistic lock).
- `address_keys` (per job): normalized key hash, normalized fields, correction log (JSONB), resolution status, flags, selected candidate, score, band, precision, reasons (JSONB), scoring_config_version.
- Effective result for a row = row override (manual/edited) ?? address_key result. A single-row edit therefore never changes other rows sharing the key: the edit is stored on the row.
- Indexes on `(job_id, band)`, `(job_id, status)`, `(job_id, score)`, `(job_id, row_index)`, plus a trigram index for search, so that server-side filter and paging stay fast at 200k rows.
- **Alternative rejected:** storing results directly on each row. That would make deduplication a copy step and bloat the table.

### D3. Ingestion with pandas, everything as text
- `pandas.read_csv(dtype=str, keep_default_na=False)`; `charset-normalizer` for encoding; `csv.Sniffer` plus a frequency heuristic for the delimiter; `openpyxl` (xlsx, `read_only=True`) and `xlrd` (xls).
- Excel numeric cells are converted to strings, and `"80331.0"` is fixed later by postcode repair. Reading as text avoids silent type coercion.
- Rows are bulk-inserted with `COPY` (asyncpg `copy_records_to_table`), since 200k ORM inserts would be too slow.

### D4. Normalization tiers

```
 raw ─▶ T0 cleanup ─▶ T1 deterministic ──resolved──────────────▶ geocode
                          │  (alias table, postcode rules,
                          │   rapidfuzz vs GeoNames, cross-checks)
                          └─unresolved─▶ T2 OpenAI repair ─▶ re-validate T1 ─▶ geocode
                                                                  │
                                    geocode ─ambiguous─▶ T3 OpenAI pick candidate id
```
- **Country alias table:** generated from `pycountry` (alpha-2/3, numeric, names) + GeoNames `countryInfo` + alternate names in local languages + a hand-curated variants file (`UK`, `GER`, `D`, `Holland`, …). It's stored as a Python dict loaded at startup.
- **Postcode rules:** a per-country YAML (`regex`, `length`, `pad_zero`, `format` template) for the countries GeoNames covers. Repairs are applied in order: strip prefix → strip `.0` → uppercase/space canonicalization → zero-pad → validate.
- **Cross-field inference:** for the postcode, find all countries whose format matches and where it exists in `geonames_postal`. For the city, find countries where an exact or alias match exists. Then intersect. The given country is overridden only when it is unsupported by both postcode and city and exactly one alternative is supported by both. This keeps it conservative.
- **City fuzzy matching:** rapidfuzz `WRatio` against an in-memory per-country name index (name + alternate names, accent-folded) built lazily and cached in the worker process. Candidates are limited by postcode when available (city names linked to that postcode in `geonames_postal`).
- **Why deterministic first:** it is fast, free, private and reproducible, and handles roughly 70–90% of rows. The LLM only handles the long tail.

### D5. OpenAI usage

- SDK `openai` with **Structured Outputs** (`response_format` JSON schema, strict). The schema contains only address fields, `changed_fields` and `reason`; there are **no coordinate fields in the schema**, so the model can't return coordinates.
- Model is configurable (`OPENAI_MODEL`, default a current small, cost-efficient GPT model). Temperature is 0.
- Prompt: system instructions with the repair rules, and up to 25 addresses per request (each with an opaque id) to keep costs low. The response maps ids to corrections.
- **Batch API** when unresolved count > `AI_BATCH_THRESHOLD` (2000): upload JSONL, poll status from an arq cron, resume the pipeline when done. Below the threshold, concurrent requests run behind a Redis token-bucket limiter.
- **Corporate gateway:** the deployment uses an Azure-style API-Management gateway (`{base}/deployments/{deployment}/chat/completions?api-version=…`, header `api-key`). Probed: strict `json_schema` works through it (even with `api-version=2024-02-01`), ~7 s per 25-address request; `/files` and `/batches` return 404, so the Batch API is disabled for gateways and large jobs use concurrent requests. If an endpoint rejects `json_schema`, the client falls back to JSON mode once and keeps validating locally.
- Everything the AI returns is **re-validated by T1**; a failed validation discards the correction. This protects against hallucinations.
- Column-mapping suggestion uses the same client with headers + ≤5 sample rows, and runs only if heuristics fail.
- Privacy: the payload builder takes only the mapped address fields, and a unit test asserts no other column can reach it. `ai_repair=false` short-circuits the client at the job level.
- **Alternative rejected:** asking the LLM for coordinates directly. These cannot be verified, the model fabricates them for obscure places, and the result would make the confidence score meaningless.

### D6. Geocoding

- **Offline:** `geonames_postal` (country, postcode, compact `pc_key`, place name, admin, lat, lon) and `geonames_place` (`cities500`), plus one accent-folded name index `gazetteer_names` that holds primary names, the alternate names already included in `cities500` (Latin script), base names without qualifiers ("Frankfurt" for "Frankfurt (Oder)") and postal place names. The separate `alternateNamesV2` dump (~600 MB) turned out to be unnecessary. Imported by `python -m app.cli import-geonames` (or `--source-dir` with pre-downloaded files when geonames.org is unreachable) into staging tables, then swapped in one transaction.
- A postcode can map to several place rows (for example a DE postcode spanning localities). The postcode centroid is the mean of rows matching the postcode (and the city when it matches).
- **Provider:** a `GeocoderProvider` protocol (`async geocode(query: StructuredAddress, country: str) -> list[Candidate]`). `OpenCageProvider` uses `countrycode=`, `no_annotations=1` and `limit=5`, and maps `components._type` + `confidence` to the precision level. `NullProvider` is used when no key is configured.
- **Cache:** `geocode_cache(provider, query_hash, response JSONB, created_at)`, checked before every provider call and shared across jobs.
- Concurrency is limited by a Redis token bucket at the provider's rate limit (configurable `GEOCODER_RPS`). Quota errors (OpenCage 402) pause the job.
- **Alternative rejected:** public Nominatim. Its 1 rps limit and bulk-use policy make 200k-row files infeasible.

### D7. Confidence scoring as a pure function

- `score(signals: Signals, config: ScoringConfig) -> ScoreResult(score, band, reasons)` is a pure function with no I/O, so it is easy to test exhaustively with table-driven tests.
- The precision cap is the starting maximum and penalties are subtracted from it (a "subtract-then-clamp" order let the city cap swallow e.g. the ambiguity penalty, ranking an ambiguous city equal to an unambiguous one).
- `scoring_config` table rows are versioned JSON; one is marked active. Each job stores `scoring_config_version`. Re-scoring is an explicit admin action.
- The score is separate from the precision level in both the UI and the export.
- A calibration CLI (`python -m app.cli calibrate labeled.csv`) runs the pipeline on labeled data and prints per-band distance-error stats.

### D8. Job pipeline and progress

- Stages: `ingesting → normalizing → ai_repair → geocoding → scoring → ready`. Each stage processes `address_keys` in chunks of 500, with stage and chunk cursor stored on the job and committed per chunk. On restart, a worker reads the cursor and continues.
- Rows are scored as their key finishes, so partial review works.
- Progress is published to Redis pub/sub `job:{id}`. The API's SSE endpoint subscribes and forwards, and falls back to polling job counts if Redis messages were missed.
- Cancellation sets a flag that workers check between chunks.

### D9. Auth

- Argon2id via `argon2-cffi`. Server-side sessions: random 256-bit token, stored hashed in the `sessions` table with expiry, sent as an HttpOnly/Secure/SameSite=Lax cookie. CSRF protection uses a double-submit token header on state-changing requests.
- **Server-side sessions rather than JWT:** instant logout and deactivation, no token-refresh complexity, and a single API host means no need for statelessness.
- Login rate limiting via a Redis counter per username and per IP.
- `must_change_password` flag after an admin reset.

### D10. Review API and bulk actions

- `GET /jobs/{id}/rows?filter=…&sort=…&cursor=…` uses keyset pagination on `(sort_col, row_index)` so deep pages stay fast.
- The filter is a typed Pydantic model shared by the list, map, bulk and export endpoints. Bulk actions send the filter (not ids) and run one `UPDATE … WHERE` within a transaction, with one audit entry containing the filter JSON and affected count.
- Two-step confirmation: `POST /bulk/preview` returns the count, then `POST /bulk` with the same filter and an expected count. If the count changed, the server responds 409.
- Map endpoint: returns `[row_id, lat, lon, band]` compactly for the filter, capped at 200k points. The client uses `react-leaflet` + `leaflet.markercluster` (canvas renderer).

### D11. Frontend

- Vite + React + TypeScript; TanStack Query for server state; TanStack Table + TanStack Virtual for the grid with infinite, cursor-based loading; react-leaflet; a component library (Mantine) for forms and dialogs to move quickly with accessible defaults.
- Pages: Login, Jobs list, Upload & Mapping wizard, Job (tabs: Progress / Grid / Map / Export), Admin Users.
- Keyboard: `C` confirm, `R` reject, `J/K` next/previous row, `Enter` open detail.
- The API base URL comes from config, and no secrets exist in the frontend build.

### D12. Export

- A `StreamingResponse` generator uses a server-side cursor (`yield_per(2000)`) and writes the BOM, then rows through `csv.writer` with `QUOTE_MINIMAL` and `lineterminator="\r\n"`. Formula-injection prefixing and `geo_` header-collision handling happen in one row-formatter function.

## Risks / Trade-offs

- **GeoNames postcode coverage gaps** (some countries are missing or have only partial postcodes, e.g. IE Eircode, parts of Africa) → those rows fall back to city or provider lookup. The UI reports the gazetteer coverage per country in the job summary.
- **Postcode centroids can be far from the actual place** in rural areas with large postcode zones → the precision level shows `postcode`, and users needing better precision must supply a street.
- **Confidence penalties are initial guesses** → keep them in versioned config, ship the calibration CLI, and calibrate on 200–500 labeled rows before relying on bulk-confirm thresholds.
- **LLM silently "fixes" a correct but unusual name** → it only runs on unresolved rows, all output is re-validated, a −10 penalty and visible `ai_repaired` flag are applied, and there is a filter to review AI rows.
- **OpenAI Batch API latency (up to 24h)** → only above the threshold. The job shows "waiting for AI batch", deterministic rows can be reviewed meanwhile, and the threshold is configurable.
- **Provider costs on street-heavy files** → deduplication + shared cache. The job summary shows the provider calls made, and an optional per-job provider-call cap pauses the job when reached.
- **Data leaving the server** (OpenAI, OpenCage) → minimal payloads, per-job AI toggle, offline-first routing, and documentation of what is sent where.
- **In-memory city index per worker** (~hundreds of MB if every country is loaded) → build it lazily per country with an LRU of N countries.
- **Large SSE fan-out** → the number of users is small, so one Redis subscription per open job page is acceptable.

## Migration Plan

This is a new deployment, with no data migration.
1. `docker compose up` → Alembic `upgrade head` runs on api start.
2. `python -m app.cli import-geonames` (downloads ~400 MB, one-time, ~10–20 minutes).
3. `python -m app.cli create-admin`.
4. `.env`: `OPENAI_API_KEY`, `GEOCODER_PROVIDER=opencage`, `GEOCODER_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`.

Rollback: stop the containers. The database is self-contained.

## Open Questions

- Default OpenAI model choice and cost ceiling per job: to be decided after a cost test on a real dirty sample.
- OpenCage plan tier (rate limit and daily quota) determines `GEOCODER_RPS` and whether large street-heavy files need the per-job cap.
- Should rows that are `rejected` be excluded from `all` exports? The current spec includes them with status `rejected`.
- Hosting target for production (Azure VM vs container apps). Not required for this change but affects TLS and cookie settings.
- The deployment network blocks download.geonames.org (corporate proxy); the gazetteer must be imported from pre-downloaded files or the host allow-listed.
