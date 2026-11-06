## MODIFIED Requirements

### Requirement: Provider abstraction
External geocoding for jobs SHALL go through a provider interface that returns normalized candidates (lat, lon, formatted address, components, precision level, provider-native confidence). OpenCage SHALL be the default implementation. The provider SHALL be selected by configuration. Providers whose terms forbid storing or exporting results (for example Google Geocoding) MUST NOT be implemented as job geocoding providers, and MUST NOT be selectable via `GEOCODER_PROVIDER`. Google Geocoding MAY be used only by the interactive distance calculator, under the retention rules of the `distance-calculator` capability.

#### Scenario: Provider selected by config
- **WHEN** `GEOCODER_PROVIDER=opencage` and `GEOCODER_API_KEY` are set in `.env`
- **THEN** external lookups use OpenCage

#### Scenario: Missing provider key
- **WHEN** no geocoder key is configured
- **THEN** the system runs offline-only, street rows fall back to postcode or city level, and the UI shows that street-level geocoding is unavailable

#### Scenario: Google not selectable for jobs
- **WHEN** an operator sets `GEOCODER_PROVIDER=google`
- **THEN** the application refuses to start with an error saying Google is not permitted as a job geocoding provider

#### Scenario: Google key does not affect jobs
- **WHEN** `GOOGLE_MAPS_API_KEY` is set and a job is processed
- **THEN** no job row is geocoded with Google
