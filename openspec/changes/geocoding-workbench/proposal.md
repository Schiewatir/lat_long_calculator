## Why

Teams regularly receive address lists (CSV, Excel, plain text) of 200 to 200,000 rows that need latitude/longitude, but the data is dirty: misspelled city names, corrupted or non-standard country codes, postcodes with stripped leading zeros, and only sometimes a street. Today there is no tool that repairs these inputs, geocodes them at scale, tells the user *how much to trust* each coordinate, and lets a team review and confirm results before exporting them.

## What Changes

- New multi-user web application ("Geocoding Workbench"): FastAPI backend + React (TypeScript, Vite) frontend, username/password login with `user` and `admin` roles.
- File upload for `.csv`, `.xlsx`, `.xls`, `.txt`/`.tsv` (encoding and delimiter auto-detected) with an auto-suggested, user-confirmable column-mapping step (street optional; city, postcode, country).
- Tiered address repair pipeline: deterministic normalization first (country alias table, per-country postcode format repair, fuzzy city matching against a gazetteer, cross-field consistency checks), OpenAI-based repair only for rows the deterministic tier cannot resolve, and OpenAI disambiguation only when several geocoder candidates remain. OpenAI never produces coordinates.
- Hybrid geocoding: offline GeoNames postcode/city data for postcode- and city-level lookups; a storage-permitting external geocoding API (OpenCage by default, swappable) for street-level rows and offline misses; a shared, persistent geocode cache.
- Deterministic, explainable 0–100 confidence score per row with bands (High / Medium / Low / Failed), a separate precision level (rooftop → country) and a human-readable list of reasons and corrections.
- Background processing jobs with deduplication, chunking, resumability and live progress, sized for 200k-row files.
- Review workspace: server-paged virtualized grid, filters, row detail panel with map and alternative candidates, single-row confirm/reject/edit/pick-candidate/drag-pin, and bulk "confirm all matching filter"; audit trail of every review action.
- CSV export (UTF-8 with BOM, postcodes kept as text) of confirmed rows only or all rows with status.
- Privacy controls: per-job "AI repair" toggle; only address fields of unresolved rows plus an opaque row id are ever sent to OpenAI; API keys kept server-side in `.env`.

## Capabilities

### New Capabilities
- `auth-and-users`: Username/password authentication, sessions, `user`/`admin` roles, user management by admins, job ownership and visibility rules.
- `file-ingestion`: Upload of CSV/Excel/TXT files, format/encoding/delimiter detection, size and row limits, column-mapping suggestion and confirmation.
- `address-normalization`: Tiered repair of country, postcode, city and street fields (deterministic tier, OpenAI repair tier, OpenAI disambiguation tier), recording every correction applied.
- `geocoding`: Offline GeoNames lookups, external provider lookups behind a provider abstraction, candidate generation, precision levels, and the shared geocode cache.
- `confidence-scoring`: Deterministic 0–100 score from measurable signals, configurable weights and caps, bands, and per-row reasons.
- `processing-jobs`: Background job lifecycle, deduplication, chunked/resumable processing, rate limiting, progress streaming, cancellation and retry.
- `results-review`: Paged/filterable results grid, row detail with map and candidates, single and bulk review actions, row status model, and audit trail.
- `csv-export`: CSV export format, column set, scope options (confirmed only / all), and encoding guarantees.

### Modified Capabilities
<!-- None: this is a new project with no existing specs. -->

## Impact

- **New codebase**: `backend/` (FastAPI app, arq worker, Alembic migrations, GeoNames import command) and `frontend/` (React app), plus `docker-compose.yml` (api, worker, web, postgres, redis).
- **External services**: OpenAI API (input repair, column-mapping suggestion, disambiguation; Batch API for large jobs), OpenCage Geocoding API (street-level and fallback lookups). Both keys supplied via `.env` (`OPENAI_API_KEY`, `GEOCODER_API_KEY`).
- **Data**: GeoNames `allCountries` postal-code and cities dumps (CC-BY 4.0, attribution required in the UI) imported into PostgreSQL; several GB of disk for the gazetteer tables and indexes.
- **Costs**: Per-call OpenAI and OpenCage costs, bounded by deduplication, tiering and caching.
- **Privacy**: Address fragments of unresolved rows leave the server to OpenAI unless AI repair is disabled for the job; street-level rows go to the geocoding provider.
