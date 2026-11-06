## 1. Project Setup

- [x] 1.1 Create repo layout `backend/` (app package, tests) and `frontend/` (Vite React TS), root `.gitignore`, `README.md`
- [x] 1.2 Backend dependencies (pyproject): fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, arq, redis, pandas, openpyxl, xlrd, charset-normalizer, rapidfuzz, pycountry, openai, httpx, argon2-cffi, pydantic-settings; dev: pytest, pytest-asyncio, respx, ruff, mypy
- [x] 1.3 `app/config.py` with pydantic-settings reading `.env`; add `.env.example` listing OPENAI_API_KEY, OPENAI_MODEL, GEOCODER_PROVIDER, GEOCODER_API_KEY, GEOCODER_RPS, DATABASE_URL, REDIS_URL, SESSION_SECRET, limits and thresholds
- [x] 1.4 `docker-compose.yml` with api, worker, web, postgres 16, redis 7, volumes and healthchecks; Dockerfiles for backend and frontend
- [x] 1.5 Alembic setup with async engine; FastAPI app factory with `/health` endpoint
- [x] 1.6 arq `WorkerSettings` entrypoint and a smoke job; CI-style `make test` / `make lint` targets
- [x] 1.7 Frontend scaffolding: Vite + TS, Mantine, TanStack Query, TanStack Table/Virtual, react-leaflet + markercluster, router, API client with CSRF header and 401 redirect

## 2. Auth and Users

- [x] 2.1 Migrations: `users` (username unique ci, password_hash, role, active, must_change_password), `sessions` (token_hash, user_id, expires_at, last_seen)
- [x] 2.2 Argon2id password hashing service and password policy validator (min 12 chars)
- [x] 2.3 Login/logout/me endpoints with HttpOnly session cookie, idle expiry, CSRF double-submit token
- [x] 2.4 Redis-based login rate limiting per username and IP (10 per 15 min)
- [x] 2.5 Auth dependencies `current_user`, `require_admin`, and must_change_password gate
- [x] 2.6 Admin user endpoints: create, list, change role, reset password, deactivate/reactivate, with last-admin protection
- [x] 2.7 Self-service change-password endpoint
- [x] 2.8 CLI `create-admin` bootstrap command (refuses when admin exists)
- [x] 2.9 Tests for all auth-and-users scenarios
- [x] 2.10 Frontend: login page, forced password change page, admin users page, auth context and route guards

## 3. Gazetteer (GeoNames)

- [x] 3.1 Migrations: `geonames_postal`, `geonames_place`, `geonames_altname`, `gazetteer_meta`, with indexes (country+postcode, country+ascii name, trigram on names)
- [x] 3.2 CLI `import-geonames`: download allCountries postal zip, cities500, alternateNamesV2, countryInfo; load into staging tables with COPY; atomic swap; record import date
- [x] 3.3 Gazetteer query service: postcode lookup (with centroid of multiple matches), city lookup by exact/alias name per country, postcode→city names, distance helper (haversine)
- [x] 3.4 Per-country in-memory city-name index (accent-folded names + alternates) with lazy build and LRU
- [x] 3.5 Tests using a small fixture gazetteer (DE, FR, GB, NL, US, GE samples)

## 4. File Ingestion

- [x] 4.1 Migrations: `jobs` (owner, name, status, stage, cursor, options, counts, scoring_config_version, file path, error message), `rows` (job_id, row_index, raw JSONB, mapped raw fields, address_key_id, review fields, version)
- [x] 4.2 Upload endpoint with size limit and extension/content checks; store file on a volume
- [x] 4.3 Parsers: encoding detection, delimiter sniffing, header detection, CSV/TXT/TSV via pandas dtype=str, XLSX/XLS with sheet listing; all as text
- [x] 4.4 Preview endpoint (first 20 rows) with overrides for encoding, delimiter, sheet, header flag
- [x] 4.5 Column-mapping heuristics with multilingual header dictionary (street, house_number, city, postcode, country, id, full_address)
- [x] 4.6 OpenAI column-mapping fallback (headers + ≤5 sample rows, structured output) when heuristics leave required fields unmapped and AI is enabled
- [x] 4.7 Mapping validation (city or postcode required; country or job default required) and start-job endpoint with options (name, ai_repair, default_country)
- [x] 4.8 Ingest stage: stream-parse the file, skip empty rows, enforce row limit, bulk-insert rows with COPY
- [x] 4.9 Tests for all file-ingestion scenarios (Windows-1252 semicolon CSV, headerless, multi-sheet xlsx, leading zeros, row limit)
- [x] 4.10 Frontend: upload wizard (drop zone → preview with overrides → mapping editor with AI-suggested badges → job options → start)

## 5. Address Normalization (Deterministic Tier)

- [x] 5.1 T0 text cleanup (trim, collapse whitespace, NFC, control chars) keeping raw values
- [x] 5.2 Country alias table generator (pycountry + countryInfo + curated variants file) and resolver with methods exact/alias/fuzzy/job_default
- [x] 5.3 Per-country postcode rules YAML and repair pipeline (prefix strip, `.0` strip, canonical spacing/case, zero-pad, ZIP+4 reduce, validate)
- [x] 5.4 Field split/swap detection (postcode inside city field, swapped city/postcode, combined "80331 München")
- [x] 5.5 City matcher: exact/alias, abbreviation normalization, rapidfuzz fuzzy with threshold, postcode-constrained candidates, ambiguity detection
- [x] 5.6 Cross-field country validation/inference (postcode format + existence + city existence intersection, conservative override)
- [x] 5.7 Address-key builder (normalized key hash) and resolution status classification (resolved/unresolved/ambiguous)
- [x] 5.8 Structured correction log (field, from, to, tier, reason) plus human-readable sentences
- [x] 5.9 Table-driven tests for every address-normalization scenario (GE→DE override, Tbilisi kept, Munich alias, Muenchn fuzzy, Frankfurt ambiguity, zero-padding, `.0`, GB/NL formats)

## 6. AI Repair and Disambiguation (OpenAI)

- [x] 6.1 OpenAI client wrapper: model from config, temperature 0, structured-output JSON schema without coordinate fields, timeouts, retries
- [x] 6.2 Minimal payload builder (mapped address fields + opaque id only) with a test proving unmapped columns never appear
- [x] 6.3 Synchronous batched repair (≤25 addresses per request) behind a Redis token-bucket limiter
- [x] 6.4 Batch API path: JSONL build, upload, submit, arq cron poller, result ingestion, resume pipeline
- [x] 6.5 Re-validation of AI output through the deterministic tier; discard on failure with reason; set `ai_repaired` flag
- [x] 6.6 T3 disambiguation: send original fields + candidate ids/labels, accept only a valid candidate id or "none"; set `ai_disambiguated`
- [x] 6.7 Job-level `ai_repair=false` short-circuit with a test asserting zero OpenAI calls
- [x] 6.8 Tests with mocked OpenAI responses (valid repair, hallucinated postcode rejected, disambiguation pick)

## 7. Geocoding

- [x] 7.1 Migrations: `candidates` (address_key_id, rank, lat, lon, source, precision, formatted, components, provider_confidence, flags), `geocode_cache`
- [x] 7.2 Offline geocoder: postcode centroid, city centroid, candidate generation with precision levels
- [x] 7.3 `GeocoderProvider` protocol, `NullProvider`, and `OpenCageProvider` (countrycode restriction, limit 5, precision mapping from `_type`/confidence)
- [x] 7.4 Shared cache lookup/store with TTL around provider calls
- [x] 7.5 Routing logic (no street → offline first, street → provider, offline miss → provider; no key → offline only with UI notice)
- [x] 7.6 Candidate validation against input postcode/city (`postcode_mismatch` > 25 km) and ranking
- [x] 7.7 Rate limiting, exponential backoff for 429/5xx/timeouts, `geocode_error` marking, quota-exhausted → job paused
- [x] 7.8 Tests with respx-mocked OpenCage (rooftop match, cache hit across jobs, transient 503, quota 402, mismatch flag)

## 8. Confidence Scoring

- [x] 8.1 Migration and seed for `scoring_config` (versioned JSON: penalties, caps, distance thresholds, bands; one active)
- [x] 8.2 Pure `score(signals, config)` function producing score, band and ordered reasons; precision caps; manual placement rule
- [x] 8.3 Signals extraction from the normalization + geocoding results
- [x] 8.4 Admin re-score endpoint (keeps review statuses, records new config version)
- [x] 8.5 CLI `calibrate labeled.csv` printing per-band accuracy within 1/5/25 km
- [x] 8.6 Table-driven tests for every confidence-scoring scenario (100 clean, 74 GE→DE+fuzzy, city cap 70, none 0, bands, determinism)

## 9. Processing Jobs Pipeline

- [x] 9.1 Job state machine and stage orchestration in arq (ingesting → normalizing → ai_repair → geocoding → scoring → ready)
- [x] 9.2 Deduplication: build address_keys per job and link rows
- [x] 9.3 Chunked processing (500 keys) with per-chunk commit of stage/cursor and resume after worker restart
- [x] 9.4 Progress publishing to Redis pub/sub and SSE endpoint with polling fallback (stage counts, band counts, ETA)
- [x] 9.5 Cancel, resume (paused), and retry-errors endpoints
- [x] 9.6 Failure handling with user-readable messages; technical details to logs
- [x] 9.7 Jobs list/detail endpoints with band distribution and review progress; owner/admin visibility (404 for non-owners)
- [x] 9.8 Job deletion and arq cron for source-file retention (30 days)
- [x] 9.9 Integration test: 10k-row fixture with 800 unique keys → 800 lookups, all rows scored; crash/resume test
- [x] 9.10 Frontend: jobs list page and job progress tab with live SSE updates, cancel/resume/retry buttons

## 10. Results Review

- [x] 10.1 Shared typed filter model (band, score range, status, precision, correction types, errors, country, text search) and keyset-paginated rows endpoint with sorting
- [x] 10.2 Row detail endpoint (raw vs normalized, correction log, reasons, candidates, history)
- [x] 10.3 Single-row actions: confirm, reject, reset, pick candidate (re-score), manual pin, edit-and-rerun (synchronous pipeline for one row); optimistic concurrency via `version`
- [x] 10.4 Bulk preview (count) and bulk apply by filter or explicit ids with expected-count check (409 on mismatch)
- [x] 10.5 Migration and service for `audit_log`; record every single/bulk/export action with before/after values
- [x] 10.6 Map endpoint returning compact points for the filter
- [x] 10.7 Review progress counts endpoint (overall and per band)
- [x] 10.8 Tests for all results-review scenarios (bulk ≥90 exact count, confirm entire job skips rejected, conflicting edit, manual pin re-score, audit history)
- [x] 10.9 Frontend grid: virtualized infinite rows, column set, band colors, sort, filter bar with counts, text search
- [x] 10.10 Frontend detail panel: side-by-side values, reasons, correction log, Leaflet map with draggable pin and alternative pins, action buttons, history
- [x] 10.11 Frontend bulk actions with confirmation dialog (>100 rows) and keyboard shortcuts (C/R/J/K/Enter)
- [x] 10.12 Frontend map tab with clustered, band-colored markers opening row detail

## 11. CSV Export

- [x] 11.1 Row formatter: original columns in order + added columns, `geo_` collision prefix, 6-decimal coordinates, corrections string, formula-injection guard
- [x] 11.2 Streaming export endpoint with scopes confirmed/all/current_filter, UTF-8 BOM, CRLF, sanitized filename, audit entry
- [x] 11.3 Tests for all csv-export scenarios (950-row confirmed scope, header collision, BOM/umlauts, formula injection, 200k streaming start time)
- [x] 11.4 Frontend export dialog with scope selection and GeoNames/OpenCage attribution

## 12. Hardening and Documentation

- [x] 12.1 End-to-end test: upload a dirty sample file → process → bulk confirm High → fix a Low row → export, verifying output
- [x] 12.2 Performance test with a synthetic 200k-row file (processing time, grid paging latency, export start time)
- [x] 12.3 Security review: auth on all endpoints, CSRF, ownership checks, no secrets in frontend bundle, OpenAI payload minimization
- [x] 12.4 README: setup, `.env` variables, GeoNames import, admin bootstrap, what data is sent to OpenAI/OpenCage, calibration workflow
