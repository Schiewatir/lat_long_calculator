## ADDED Requirements

### Requirement: Distance page
The frontend SHALL provide a Distance page, reachable from the main navigation for every signed-in user. It SHALL have one Origin input and one Destination input, and a Calculate action.

#### Scenario: Navigation
- **WHEN** a signed-in user opens the app
- **THEN** a "Distance" entry appears in the header navigation and opens the Distance page

#### Scenario: Unauthenticated access
- **WHEN** a request to `POST /api/distance` has no valid session
- **THEN** the API responds 401 and makes no external call

### Requirement: Location input modes
Each location input (origin and destination, set independently) SHALL support two modes:
- **Address**: optional structured fields (street, house number, postcode, city, state/region, country) and an optional single free-text line. At least one field MUST be non-empty.
- **Coordinates**: decimal latitude and longitude.

The coordinates mode SHALL accept a pasted `"lat, lon"` pair (comma, semicolon or whitespace separated) and split it into the two fields.

#### Scenario: Mixed modes
- **WHEN** the origin is the address `postcode "80331", country "DE"` and the destination is coordinates `52.5200, 13.4050`
- **THEN** the system geocodes only the origin and uses the destination coordinates as given

#### Scenario: Partial address accepted
- **WHEN** the origin has only city "Lyon" and country "FR"
- **THEN** the system accepts it and geocodes it

#### Scenario: Empty address rejected
- **WHEN** an address-mode input has every field empty
- **THEN** the form shows a validation error and no request is sent

#### Scenario: Pasted coordinate pair
- **WHEN** the user pastes `48.1374; 11.5755` into the latitude field
- **THEN** latitude becomes 48.1374 and longitude becomes 11.5755

### Requirement: Coordinate validation
Coordinates SHALL be validated on both client and server: latitude MUST be within [-90, 90] and longitude within [-180, 180]. Values outside these ranges MUST be rejected with a field-level error.

#### Scenario: Out-of-range latitude
- **WHEN** a request contains latitude 95.0
- **THEN** the API responds 422 and names the offending field

#### Scenario: Coordinates need no geocoding
- **WHEN** both origin and destination are given as valid coordinates
- **THEN** no Google Geocoding request is made

### Requirement: Country normalization
A country given as a name, ISO 3166-1 alpha-2 or alpha-3 code SHALL be normalized to alpha-2 using the existing country normalizer. It SHALL then be passed to Google as a hard `country` component restriction. An unrecognized country MUST be rejected before any external call.

#### Scenario: Country name normalized
- **WHEN** the address has country "Deutschland"
- **THEN** the Google request restricts results to `country:DE`

#### Scenario: Unknown country
- **WHEN** the address has country "Atlantis"
- **THEN** the API responds 422 with a country error and makes no external call

### Requirement: Address resolution with Google Geocoding
Address-mode inputs SHALL be resolved with the Google Geocoding API, called only from the backend. Structured fields SHALL be sent as `components` (postal_code, locality, administrative_area, country) where present. Street and free text SHALL be sent as `address`. For each side, the response SHALL return the top match's formatted address, latitude, longitude, Google `place_id`, a precision level and a `partial_match` flag, plus up to 4 alternatives.

The precision level SHALL map from Google's `location_type` and result types as follows:
- `ROOFTOP` → `rooftop`
- `RANGE_INTERPOLATED`, or result types `street_address`/`route`/`premise`/`subpremise`/`intersection` → `street`
- result type `postal_code` → `postcode`
- locality types → `city`
- administrative-area types → `region`
- `country` → `country`
- anything else → `region`

#### Scenario: Structured address resolved
- **WHEN** the origin is street "Marienplatz 1", postcode "80331", city "München", country "DE"
- **THEN** the response contains a formatted address in München, coordinates near 48.137/11.575, precision `rooftop` or `street`, and source `google`

#### Scenario: No match
- **WHEN** Google returns `ZERO_RESULTS` for the destination
- **THEN** the API responds 422 saying the destination could not be found, and the origin result (if resolved) is still included

#### Scenario: Partial match flagged
- **WHEN** Google marks the top result `partial_match`
- **THEN** the UI shows a "partial match – check the resolved address" warning for that side

### Requirement: Ambiguity and alternative selection
When a side has alternatives, the UI SHALL list them. The user SHALL be able to pick one, which recomputes the distance using the chosen alternative's coordinates and makes no new geocoding request for that side.

#### Scenario: Pick alternative
- **WHEN** the destination "Frankfurt" resolves to Frankfurt am Main with Frankfurt (Oder) as an alternative, and the user picks Frankfurt (Oder)
- **THEN** the distance is recomputed using Frankfurt (Oder)'s coordinates and the destination label updates

### Requirement: Straight-line distance
The system SHALL always compute the great-circle (haversine, mean Earth radius 6371.0 km) distance between the resolved origin and destination. It SHALL return the distance in kilometres and miles.

#### Scenario: Known distance
- **WHEN** origin is 52.5200, 13.4050 (Berlin) and destination is 48.1374, 11.5755 (Munich)
- **THEN** the straight-line distance is 504 km ± 1 km

#### Scenario: Identical points
- **WHEN** origin and destination coordinates are equal
- **THEN** the straight-line distance is 0 and no Routes request is made

### Requirement: Road distance via Google Routes
When road distance is enabled and a Google key is configured, the system SHALL request a driving route (travel mode DRIVE) from the Google Routes API between the resolved coordinates. It SHALL return the road distance (km and mi), the duration, and an encoded route polyline. If no route exists, or the Routes call fails, the response SHALL still return the straight-line distance with a `road_distance_unavailable` reason, and MUST NOT fail overall.

#### Scenario: Route returned
- **WHEN** a route exists between origin and destination
- **THEN** the response contains road distance, duration and polyline, and the UI shows them next to the straight-line distance

#### Scenario: No drivable route
- **WHEN** the origin is in London and the destination is in New York
- **THEN** the response contains the straight-line distance and `road_distance_unavailable: "no_route"`

#### Scenario: Routes API error
- **WHEN** the Routes API times out after retries
- **THEN** the response contains the straight-line distance and `road_distance_unavailable: "provider_error"`

### Requirement: Result display
The result panel SHALL show, for each side, the label (resolved formatted address or the entered coordinates), coordinates to 6 decimals, source (`google` or `input`) and precision. It SHALL also show the straight-line distance, the road distance and duration when available, a km/mi unit toggle (remembered in local storage), a map with both points (and the route line when available), and a "Copy" action that puts a plain-text summary on the clipboard. Google attribution SHALL be shown wherever Google-derived content is displayed.

#### Scenario: Unit toggle
- **WHEN** the user switches the unit to miles
- **THEN** all distances display in miles, and the choice persists after reload

#### Scenario: Copy summary
- **WHEN** the user clicks "Copy"
- **THEN** the clipboard contains origin, destination, coordinates and distances as plain text

### Requirement: Degraded operation without Google key
If no Google key is configured, the capabilities endpoint SHALL report `distance_address_available: false` and `road_distance_available: false`. The Distance page SHALL still work in coordinates mode, showing straight-line distance only. Address mode SHALL be disabled, with an explanation.

#### Scenario: No key
- **WHEN** `GOOGLE_MAPS_API_KEY` is empty and the user opens the Distance page
- **THEN** the address mode is disabled with the note "Address lookup not configured", and coordinate inputs still produce a straight-line distance

### Requirement: Google data retention
Google-derived results (coordinates, formatted addresses, routes) MUST NOT be written to PostgreSQL. They MUST NOT be written to the shared geocode cache used by jobs, and MUST NOT appear in job rows, review data or CSV exports. They MAY be cached in Redis with a TTL of at most 30 days (configurable, default 24 hours). Google `place_id` values MAY be cached without that limit. The Google API key MUST stay server-side and MUST NOT appear in any API response, frontend bundle or log line.

#### Scenario: Cache TTL capped
- **WHEN** an operator sets the Google cache TTL to 60 days
- **THEN** the application clamps it to 30 days and logs a warning at startup

#### Scenario: Not persisted
- **WHEN** a user calculates a distance between two addresses
- **THEN** no row is added to any PostgreSQL table and the `geocode_cache` table is unchanged

#### Scenario: Cache hit
- **WHEN** the same normalized address is requested twice within the cache TTL
- **THEN** only the first request calls Google Geocoding

### Requirement: Rate limiting and errors
The distance endpoint SHALL enforce a per-user rate limit (configurable, default 30 calculations per minute) and a global Google request rate limit (configurable). Over the per-user limit, it SHALL respond 429 with a retry hint. Google `OVER_QUERY_LIMIT`, `OVER_DAILY_LIMIT` and `REQUEST_DENIED` SHALL map to a 503 with the message "Address lookup temporarily unavailable"; the Google error detail is logged server-side only. Transient failures (timeouts, HTTP 5xx, 429) SHALL be retried with exponential backoff up to 3 attempts.

#### Scenario: User over limit
- **WHEN** a user sends a 31st calculation within one minute
- **THEN** the API responds 429 with a `Retry-After` header

#### Scenario: Key rejected
- **WHEN** Google returns `REQUEST_DENIED`
- **THEN** the API responds 503 with a generic message, and the server log records the Google error message without the API key
