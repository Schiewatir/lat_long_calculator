## ADDED Requirements

### Requirement: Coordinates only from geographic sources
Latitude and longitude SHALL come only from the offline gazetteer, the external geocoding provider, or a manual pin set by a user. Coordinates MUST NOT come from an LLM.

#### Scenario: Source recorded
- **WHEN** a row receives coordinates
- **THEN** the row records the source as one of `geonames_postcode`, `geonames_city`, `provider:<name>` or `manual`

### Requirement: Offline gazetteer
The system SHALL hold GeoNames postal-code data and city data (cities with population ≥ 500, plus alternate names) in PostgreSQL, loaded by an import command that can be re-run to refresh the data. The UI SHALL show GeoNames attribution (CC-BY 4.0).

#### Scenario: Import command
- **WHEN** an operator runs the gazetteer import command
- **THEN** the postal-code, place and alternate-name tables are replaced atomically and the import date is recorded

### Requirement: Routing between offline and external lookup
For rows without a street, the system SHALL geocode offline: postcode centroid if the postcode is valid and found, otherwise city centroid. For rows with a street, the system SHALL use the external provider. The system SHALL also use the external provider for rows without a street when the offline lookup finds nothing.

#### Scenario: Postcode-level row stays offline
- **WHEN** a resolved row has postcode "75001", city "Paris", country "FR" and no street
- **THEN** coordinates come from `geonames_postcode` and no external call is made

#### Scenario: Street row uses provider
- **WHEN** a row has street "Marienplatz 1", postcode "80331", city "München", country "DE"
- **THEN** the system queries the external provider with the structured address

#### Scenario: Offline miss falls back
- **WHEN** a row without a street has a city not found in the gazetteer
- **THEN** the system queries the external provider

### Requirement: Provider abstraction
External geocoding SHALL go through a provider interface that returns normalized candidates (lat, lon, formatted address, components, precision level, provider-native confidence). OpenCage SHALL be the default implementation. The provider SHALL be selected by configuration. Providers whose terms forbid storing results (for example Google Geocoding) MUST NOT be implemented.

#### Scenario: Provider selected by config
- **WHEN** `GEOCODER_PROVIDER=opencage` and `GEOCODER_API_KEY` are set in `.env`
- **THEN** external lookups use OpenCage

#### Scenario: Missing provider key
- **WHEN** no geocoder key is configured
- **THEN** the system runs offline-only, street rows fall back to postcode or city level, and the UI shows that street-level geocoding is unavailable

### Requirement: Country-restricted provider queries
External queries SHALL be restricted to the resolved country, and each returned candidate SHALL be checked against the input postcode and city.

#### Scenario: Result outside postcode area
- **WHEN** the provider returns a candidate more than 25 km from the input postcode's centroid
- **THEN** the candidate is kept but flagged `postcode_mismatch`

### Requirement: Precision level
Each result SHALL carry a precision level, one of `rooftop`, `street`, `postcode`, `city`, `region`, `country`, `manual` or `none`, derived from the source and the provider's match type (`manual` only for user-placed pins).

#### Scenario: Postcode centroid precision
- **WHEN** coordinates come from `geonames_postcode`
- **THEN** the precision level is `postcode`

#### Scenario: Provider house-number match
- **WHEN** the provider matches to a house number
- **THEN** the precision level is `rooftop`

### Requirement: Candidates and alternatives
The system SHALL keep up to 5 candidates per unique address, ranked, with the top candidate as the result. Alternatives SHALL be shown in review and selectable.

#### Scenario: Alternatives stored
- **WHEN** a lookup produces 3 candidates
- **THEN** the row stores all 3, the first is the result, and the others are listed as alternatives

### Requirement: Shared geocode cache
The system SHALL cache external provider responses, keyed by provider and normalized query, in PostgreSQL, shared across all users and jobs, with a configurable TTL (default 365 days). A cache hit MUST NOT call the provider.

#### Scenario: Cache hit across users
- **WHEN** user B's job contains an address that user A's earlier job already geocoded with the provider
- **THEN** the result comes from the cache and no provider call is made

### Requirement: Provider rate limits and failures
The system SHALL respect configured provider rate limits, retry transient failures (HTTP 429/5xx, timeouts) with exponential backoff up to 5 attempts, and mark rows `geocode_error` after that without failing the whole job. If the provider reports quota exhaustion, the job SHALL pause with a clear message.

#### Scenario: Transient error retried
- **WHEN** the provider returns 503 once and then succeeds
- **THEN** the row receives the successful result

#### Scenario: Quota exhausted
- **WHEN** the provider returns a quota-exceeded error
- **THEN** the job status becomes `paused` with reason "geocoder quota exhausted" and can be resumed later

### Requirement: No result
If no source yields coordinates, the row SHALL have null lat/lon, precision `none`, and band `Failed`.

#### Scenario: Unresolvable row
- **WHEN** a row's inputs are "???", "", ""
- **THEN** lat and lon are null and the row shows "no location found"
