## Why

Users want to know how far apart two places are without building a file and running a batch job. Today the workbench only geocodes uploaded files. There is no way to type in an origin and a destination, as a postal address or as raw coordinates, and get a distance. Google's Geocoding and Routes APIs resolve free-form postal addresses worldwide and return driving distances. That makes them a good fit for a one-off, on-screen lookup, even though their terms rule them out for the batch pipeline, which stores and exports results.

## What Changes

- New **Distance** page in the frontend (nav entry next to "Jobs"). It has two location inputs, Origin and Destination. Each one can be:
  - a **postal address**: any mix of street, house number, postcode, city, state/region and country (name or ISO code), or a single free-text line, or
  - **coordinates**: decimal latitude/longitude, typed into two fields or pasted as `"48.137, 11.575"`.
- New authenticated backend endpoint `POST /api/distance`. It resolves each side to coordinates, using **Google Geocoding API** for addresses and passing coordinates through after validation. It then returns:
  - the **straight-line (great-circle) distance**, always,
  - the **road distance and driving duration** from the **Google Routes API**, when enabled,
  - the resolved address, coordinates, precision and alternatives for each side.
- When an address is ambiguous, the user can pick one of the alternative matches and recompute.
- The result shows km/mi, a map with both points (and the route line when available), and a copy-to-clipboard summary.
- New server-side config: `GOOGLE_MAPS_API_KEY` and related tuning. The key never reaches the browser.
- Google results are **not persisted** to PostgreSQL, **not** written to the shared geocode cache, and **never** appear in job results or CSV exports. A short-lived Redis cache (≤ 30 days, as Google's terms require) avoids repeat billing.
- Per-user rate limiting on the distance endpoint to keep API cost under control.
- The project rule "Google Geocoding is excluded" is narrowed: Google stays **excluded from the batch pipeline** and is **allowed only for the interactive distance calculator**.

## Capabilities

### New Capabilities
- `distance-calculator`: interactive origin/destination input (address or coordinates), location resolution via Google Geocoding, straight-line and road distance, ambiguity handling, result display, and the related data-retention and rate-limit rules.

### Modified Capabilities
- `geocoding`: the "Provider abstraction" requirement currently says Google MUST NOT be implemented. It changes to: Google MUST NOT be used as a batch geocoding provider, but MAY be used by the interactive distance calculator under the retention rules in `distance-calculator`. *(Base spec currently lives in the unarchived `geocoding-workbench` change. Archive that change first.)*

## Impact

- **Backend**: new `app/distance/` package (Google geocoding client, Google Routes client, input parsing, service), new router `app/api/distance.py` registered in `app/main.py`, new settings in `app/config.py`, new capability flags in `app/api/system.py`. Reuses `haversine_km` (`app/gazetteer/base.py`), country normalization (`app/normalize/country.py`) and the Redis rate limiter (`app/jobs/ratelimit.py`).
- **Frontend**: new `pages/DistancePage.tsx`, location-input and result components, API types/client functions, route in `App.tsx`, nav button and capability badge in `AppLayout.tsx`. The map reuses the existing Leaflet setup, pending the map-display compliance check in design.md.
- **Config/docs**: `.env.example`, `README.md`, `openspec/config.yaml` domain rules.
- **External dependencies**: Google Maps Platform project with Geocoding API and Routes API enabled and billing on. No new Python or npm packages are expected (httpx is already used).
- **Cost**: each calculation costs at most 2 geocoding requests and 1 route request. Cache hits and coordinate inputs cost nothing.
- No database migration. No change to existing job, review or export behaviour.
