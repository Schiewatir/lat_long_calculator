## 1. Configuration and project rules

- [x] 1.1 Add settings to `app/config.py`: `google_maps_api_key` (empty → None), `google_routes_enabled` (default True), `google_rps` (default 10), `google_cache_ttl_hours` (default 24, validator clamps to 720 with a warning), `google_language` (default "en"), `distance_per_user_per_min` (default 30), `distance_map` (`leaflet|off`, default `off`); add properties `distance_address_available` and `road_distance_available`
- [x] 1.2 Add a settings validator that rejects `GEOCODER_PROVIDER=google` with a clear error; add a test
- [x] 1.3 Add the new variables, with comments (key restrictions, 30-day cap), to `.env.example`
- [x] 1.4 Update `openspec/config.yaml` domain rule: Google excluded from job geocoding, allowed only for the interactive distance calculator without persistence
- [x] 1.5 Update the comment in `app/geocode/providers.py` to match the narrowed rule

## 2. Backend: input models and parsing

- [x] 2.1 Create the `app/distance/` package with pydantic models: `AddressInput`, `CoordsInput` (lat/lon range validation, optional `label`), discriminated union `LocationInput`, `DistanceRequest`, `ResolvedLocation`, `DistanceResponse`
- [x] 2.2 Validate that an address input has at least one non-empty field; normalize country via `resolve_country` and return 422 for unknown countries
- [x] 2.3 Unit tests: range errors name the field, empty address rejected, "Deutschland"/"DEU"/"de" → "DE", unknown country rejected

## 3. Backend: Google Geocoding client

- [x] 3.1 Implement `GoogleGeocoder` (httpx) building `address` + `components` from `AddressInput`, `language` from settings
- [x] 3.2 Map Google statuses to results / 422 / `ProviderError(transient|quota)` as in design D4; never log the key (strip `key=` from logged URLs)
- [x] 3.3 Map `location_type`/result types to precision; return top match plus up to 4 alternatives with `place_id` and `partial_match`
- [x] 3.4 Unit tests with recorded/fake JSON responses: rooftop, postcode-only, locality, ZERO_RESULTS, REQUEST_DENIED, 503-then-OK retry

## 4. Backend: Google Routes client

- [x] 4.1 Implement `GoogleRoutes.compute(origin, dest)` calling `directions/v2:computeRoutes` with field mask `routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline`, DRIVE, TRAFFIC_UNAWARE
- [x] 4.2 Parse distance (m → km/mi), duration ("1234s" → int seconds), polyline; empty routes → `no_route`
- [x] 4.3 Unit tests: route OK, empty routes, timeout after retries → `provider_error`

## 5. Backend: caching, rate limits, service

- [x] 5.1 Add non-blocking `try_acquire()` (returns retry-after seconds or None) to `RateLimiter` in `app/jobs/ratelimit.py`; unit test
- [x] 5.2 Implement a Redis cache for geocode (`gdist:geo:<hash>`) and route (`gdist:route:<o>|<d>`) results with the clamped TTL
- [x] 5.3 Implement `DistanceService.calculate()`: resolve both sides concurrently, haversine via `haversine_km`, same-point short-circuit, optional route, assemble the response with attribution
- [x] 5.4 Use with_retries semantics capped at 3 attempts for interactive calls; use the shared global limiter `RateLimiter(redis, "google", google_rps)`
- [x] 5.5 Service tests with fakes: mixed address+coords (only one Google call), coords+coords (zero geocode calls), cache hit avoids second call, route failure still returns straight-line, Berlin–Munich ≈ 504 km

## 6. Backend: API endpoint and capabilities

- [x] 6.1 Create `app/api/distance.py` with `POST /distance` (auth required, per-user `try_acquire` → 429 + `Retry-After`, quota errors → 503 generic message, side-specific 422s that name origin/destination)
- [x] 6.2 Register the router in `app/main.py`
- [x] 6.3 Extend `/capabilities` with `distance_address_available`, `road_distance_available`, `distance_map`; add Google attribution when available
- [x] 6.4 API tests: 401 unauthenticated, 422 validation, 429 over limit, 503 on REQUEST_DENIED, address mode returns 503/disabled when no key while coords mode works
- [x] 6.5 Retention test: after a distance calculation, assert no new rows in any table, including `geocode_cache`

## 7. Frontend: API layer

- [x] 7.1 Add `DistanceRequest`/`DistanceResponse`/`ResolvedLocation` types and the new capability fields to `src/api/types.ts`
- [x] 7.2 Add `calculateDistance()` to `src/api/client.ts`, and a `useCalculateDistance` mutation hook in `src/lib/hooks.ts`

## 8. Frontend: Distance page

- [x] 8.1 Create the `LocationInput` component: Address/Coordinates segmented control, structured address fields, country Select (`lib/countries.ts`), free-text line, lat/lon NumberInputs with range validation
- [x] 8.2 Paste parser: `"lat, lon"`, `"lat; lon"`, `"lat lon"` in either coordinate field fills both; unit-test the parser
- [x] 8.3 Create `DistancePage.tsx`: two `LocationInput`s, swap button, Calculate button, loading/error states (side-specific error messages)
- [x] 8.4 Disable address mode with the "Address lookup not configured" note when `distance_address_available` is false
- [x] 8.5 Create `DistanceResult`: per-side label, 6-dp coords, source/precision badges (reuse `Badges.tsx`), partial-match warning, straight-line and road distance + formatted duration, `road_distance_unavailable` explanation
- [x] 8.6 Alternatives menu per side: selecting one re-submits that side as coords with label and recomputes
- [x] 8.7 km/mi toggle persisted in localStorage; "Copy" puts a plain-text summary on the clipboard
- [x] 8.8 Create `DistanceMap` (Leaflet): two markers, dashed straight line, decoded route polyline (local decoder helper); honour `distance_map` capability (hide for Google-resolved points when `off`)
- [x] 8.9 Show Google attribution under results when Google data is displayed
- [x] 8.10 Add the `/distance` route in `App.tsx` (lazy-loaded) and a "Distance" nav button in `AppLayout.tsx`
- [x] 8.11 Sync coords-only inputs to the URL query string for shareable links (never put address fields in the URL)

## 9. Docs, ops and verification

- [x] 9.1 README: Distance calculator section, Google Cloud setup (enable Geocoding API + Routes API, restrict key by API and server IP, budget alert, quota caps), retention rules
- [ ] 9.2 Resolve design open question 1 (map display terms for this account's region) and set the `DISTANCE_MAP` default accordingly
- [x] 9.3 Run backend tests and frontend lint/typecheck/build; all green
- [ ] 9.4 Manual end-to-end check with a real key: address→address, address→coords, coords→coords, ambiguous city with alternative pick, transatlantic (no route), no-key mode
- [ ] 9.5 Archive `geocoding-workbench` before archiving this change (the geocoding delta depends on it)
