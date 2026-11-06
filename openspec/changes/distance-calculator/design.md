## Context

The Geocoding Workbench geocodes uploaded address files in batch. It uses offline GeoNames data and OpenCage, stores results, and exports them as CSV. Google was ruled out on purpose (`openspec/config.yaml`, geocoding spec "Provider abstraction") because Google Maps Platform terms limit caching of lat/lng to 30 days and forbid building a stored or exported dataset from Google content.

This change adds an **interactive, one-pair-at-a-time distance calculator** that uses Google Geocoding and Google Routes. This use is short-lived: the user looks at the result on screen and nothing goes into the database or exports. That keeps it within Google's terms while leaving the batch pipeline untouched.

Code this change will build on:
- `app/gazetteer/base.py::haversine_km`: great-circle distance, reused as-is.
- `app/normalize/country.py::resolve_country`: country name or ISO code → alpha-2.
- `app/jobs/ratelimit.py::RateLimiter`: Redis fixed-window limiter. It **waits** until a slot frees up, which suits the global Google rate limit. For the per-user limit, which must **reject** with 429, a non-blocking `try_acquire` variant is needed.
- `app/geocode/providers.py::with_retries` and the `ProviderError` pattern: same retry semantics, but at most 3 attempts here because a person is waiting on the result.
- `app/api/system.py` `/capabilities` and `AppLayout.tsx` badges: these already drive feature notices.
- Leaflet map setup (`lib/leaflet.ts`, OSM tiles).

## Goals / Non-Goals

**Goals:**
- Origin and destination given as a postal address (any combination of fields, or free text) **or** as lat/lon, with the mode chosen per side.
- Straight-line distance always. Road distance and duration from Google Routes when available.
- A clear view of what each input resolved to, with alternatives the user can pick.
- Stay within Google's terms: no persistence, Redis cache ≤ 30 days, key server-side only.
- Keep cost predictable: per-user and global rate limits, and caching.

**Non-Goals:**
- Using Google for batch jobs, review or export (stays forbidden, see geocoding delta).
- Distance matrices, many-to-many, or file-based distance batches (possible follow-up).
- Travel modes other than DRIVE, waypoints, tolls/traffic options, turn-by-turn directions.
- Browser-side Google Maps JS / Places Autocomplete. Autocomplete is a good follow-up but needs a browser key and Places billing.
- Storing a per-user history of calculations.

## Decisions

### D1. Backend proxy, not browser calls
All Google calls go through `POST /api/distance`. **Why:** keeps the key secret (project rule), gives one place for rate limits, caching and retention rules, and reuses session auth. **Alternative:** Google Maps JS SDK in the browser with a referrer-restricted key. Rejected for now: it exposes a key, bypasses our limits, and splits the logic.

### D2. One endpoint for resolve + distance
```
POST /api/distance
{
  "origin":      {"type": "address", "street": "...", "house_number": "...", "postcode": "...",
                  "city": "...", "region": "...", "country": "DE", "text": "..."}
              |  {"type": "coords", "lat": 48.137, "lon": 11.575},
  "destination": { ...same... },
  "road": true
}
→ 200 {
  "origin":      {"label", "lat", "lon", "source": "google|input", "precision",
                  "place_id", "partial_match", "alternatives": [{label, lat, lon, precision, place_id}]},
  "destination": { ... },
  "straight_line": {"km": 504.2, "mi": 313.3},
  "road": {"km": 584.7, "mi": 363.3, "duration_s": 19800, "polyline": "..."} | null,
  "road_distance_unavailable": null | "disabled" | "no_route" | "provider_error" | "same_point",
  "attribution": ["Geocoding and routing by Google"]
}
```
Picking an alternative re-posts with that side as `{"type":"coords", ...}` and carries the chosen label (`label` is an optional field on coords input). That side is then not geocoded again. **Alternative:** separate `/geocode` and `/route` endpoints. Rejected: twice the round trips, and the frontend would have to orchestrate them. A single call is simpler and the cache absorbs repeats.

### D3. Request flow
```
 request ─► validate (pydantic: ranges, non-empty address, country → ISO2)
         ─► per-user limit (try_acquire, 429 on reject)
         ─► resolve origin ┐ asyncio.gather
         ─► resolve dest.  ┘   address → Redis cache? → else global limiter → Google Geocoding (≤3 tries)
                               coords  → pass through
         ─► haversine_km
         ─► road? & key? & not same point → Redis cache? → Google Routes computeRoutes (≤3 tries)
         ─► response (nothing written to PostgreSQL)
```

### D4. Google Geocoding request mapping
- `components`: `country:XX` (always when given, hard filter), `postal_code:…`, `locality:…`, `administrative_area:…`.
- `address`: street + house number + free text, joined. If there is no street or free text, send components only (Google allows a components-only query when it includes postal_code/locality).
- `language` from the browser's `Accept-Language` is out of scope. Use a fixed `language=en` setting so labels are consistent (configurable).
- Status mapping: `OK` → results. `ZERO_RESULTS` → 422 for that side. `OVER_QUERY_LIMIT`/`OVER_DAILY_LIMIT`/`REQUEST_DENIED` → `ProviderError(quota=True)` → 503. `INVALID_REQUEST` → 422. `UNKNOWN_ERROR`, 5xx and timeouts → transient, retried.
- Precision mapping as in the spec. Kept in the same `rooftop…country` vocabulary as `PRECISION_RANK` so the UI can reuse `Badges.tsx`.

### D5. Google Routes API (not the legacy Distance Matrix)
`POST https://routes.googleapis.com/directions/v2:computeRoutes`, sent with `X-Goog-Api-Key` and `X-Goog-FieldMask: routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline` so we are billed only for the basic SKU fields. `travelMode: DRIVE`, `routingPreference: TRAFFIC_UNAWARE` (cheapest, deterministic). An empty `routes` array means `no_route`. **Alternative:** legacy Distance Matrix / Directions API. Rejected: Google has replaced them, and they are not enabled on new projects.

### D6. Caching in Redis only
- Key `gdist:geo:<sha256(normalized ProviderQuery-like payload)>` holds the top match plus alternatives. Key `gdist:route:<rounded 6dp origin>|<dest>` holds the route.
- TTL `GOOGLE_CACHE_TTL_HOURS`, default 24, **clamped to 720 (30 days)** by a settings validator, which logs a warning.
- Never use the PostgreSQL `geocode_cache` table. **Why:** that table is shared with jobs, has a 365-day TTL, and its contents can end up in exports.

### D7. Rate limits
- Global: `RateLimiter(redis, "google", GOOGLE_RPS)` (blocking), shared by geocode and routes calls. Default 10 rps.
- Per user: new `try_acquire()` on a fixed 60 s window, key `rl:distance:<user_id>`, limit `DISTANCE_PER_USER_PER_MIN` (default 30). Returns 429 + `Retry-After`.

### D8. Frontend structure
```
pages/DistancePage.tsx
 ├─ LocationInput (×2)   SegmentedControl [Address | Coordinates]
 │    Address: street, no., postcode, city, region, country (Select from lib/countries.ts), free text
 │    Coords : lat, lon (NumberInput) + paste-parser "lat, lon"
 │    [swap ⇅] button between the two inputs
 ├─ DistanceResult        straight-line | road + duration | km/mi toggle | Copy
 │    per side: label, coords, source/precision badges, partial-match warning, alternatives menu
 └─ DistanceMap           Leaflet: 2 markers, dashed great-circle line, route polyline when present
```
TanStack Query `useMutation`. Form state is kept in the URL query string (coords-only sides) so people can share links. Address sides are not put in the URL, to avoid leaking addresses in logs. Polyline decoding uses a small local helper (about 20 lines) rather than a new dependency.

### D9. Narrow the project-wide Google rule
Update `openspec/config.yaml` and the geocoding spec (delta) to: *"Google is excluded from job geocoding; allowed only for the interactive distance calculator with no persistence."* Add a settings validator that rejects `GEOCODER_PROVIDER=google`, so nobody can set it by mistake.

## Risks / Trade-offs

- **[Map display terms]** Google's terms restrict showing Google content on a non-Google map. The rules differ between the EEA and the rest of the world, and have changed recently. → Before go-live, confirm the current Google Maps Platform Service Specific Terms for this account's region (open question 1). Until confirmed, `DISTANCE_MAP=leaflet|off` (default `off` for Google-resolved points, map always shown for coordinates-only calculations). If needed, a Google Maps JS map can replace Leaflet on this page in a follow-up.
- **[Cost runaway]** A script hitting the endpoint could run up the bill. → Per-user limit, global rps limit, caching, a Google Cloud budget alert and API quota caps (ops task). Restrict the key to the Geocoding and Routes APIs and the server's egress IP.
- **[Ambiguous short inputs]** "Springfield" plus no country returns a fairly arbitrary top match. → Show alternatives and the partial-match warning. The UI nudges the user to set a country.
- **[Inconsistent precision across sources]** Google precision differs from GeoNames/OpenCage precision in the jobs UI. → Same vocabulary, and the source is always shown. The calculator does not produce a confidence score (that is a job concept).
- **[Rule drift]** A later developer may reuse the Google client in the job pipeline. → Keep it in a separate package `app/distance/` (not `app/geocode/`), add the startup validator, document it in config.yaml, and add a test that asserts `build_provider("google", …)` raises.
- **[Spec base not archived]** The geocoding delta applies to a spec that exists only in `geocoding-workbench`. → Archive `geocoding-workbench` before archiving this change.

## Migration Plan

1. Create a Google Cloud project/key, enable Geocoding API and Routes API, restrict the key, set a budget alert.
2. Add `GOOGLE_MAPS_API_KEY` (and optional tuning) to `.env`. Deploy api and web. No DB migration.
3. Rollback: clear `GOOGLE_MAPS_API_KEY`. The page drops to coordinates-only. Or revert the deploy. Redis keys expire by themselves.

## Open Questions

1. **Map display:** may Google-geocoded points and Google route polylines be drawn on the Leaflet/OSM map for this account's region? This decides whether `DISTANCE_MAP` defaults to `leaflet` or whether a Google map is needed.
2. Should the calculator fall back to offline GeoNames (postcode/city centroid) when there is no Google key or Google is unavailable? This is cheap to add and gives degraded address support. Currently out of scope.
3. Default result language: fixed `en`, or follow the user's browser locale?
4. Is Places Autocomplete on the address inputs wanted as a follow-up (better input quality, extra cost, browser key)?
