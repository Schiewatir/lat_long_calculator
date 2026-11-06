## ADDED Requirements

### Requirement: Export scope
Users SHALL be able to export a job as CSV with one of these scopes: `confirmed` (rows with status `confirmed` or `edited`, the default), `all` (every row, with its status), or `current_filter` (rows matching the active grid filter).

#### Scenario: Confirmed only
- **WHEN** a job has 1,000 rows of which 900 are confirmed, 50 edited and 50 pending, and the user exports with scope `confirmed`
- **THEN** the CSV contains 950 data rows

#### Scenario: All rows
- **WHEN** the user exports with scope `all`
- **THEN** the CSV contains all 1,000 rows and the `status` column shows each row's review status

### Requirement: Export columns
The export SHALL contain all original columns in original order with their original headers, followed by: `lat`, `lon`, `confidence`, `confidence_band`, `precision_level`, `geocode_source`, `matched_street`, `matched_city`, `matched_postcode`, `matched_country`, `corrections`, `status`, `reviewed_by`, `reviewed_at`. If an original header collides with an added column name, the added column SHALL be prefixed with `geo_`.

#### Scenario: Header collision
- **WHEN** the original file already has a column named `lat`
- **THEN** the original column is kept and the added column is named `geo_lat`

### Requirement: Encoding and value formatting
The CSV SHALL be UTF-8 with a BOM, comma-delimited, RFC 4180 quoted, with CRLF line endings. Lat and lon SHALL be written with a dot decimal separator and 6 decimal places. Values SHALL be written as text exactly as stored, so postcodes keep leading zeros. `corrections` SHALL be a single semicolon-separated human-readable string. Unresolved rows SHALL have empty lat/lon.

#### Scenario: Umlauts in Excel
- **WHEN** the exported file containing "München" is opened in Excel
- **THEN** "München" is displayed correctly

#### Scenario: Coordinate format
- **WHEN** a row's lat is 48.1374
- **THEN** the exported value is "48.137400"

### Requirement: Formula injection protection
Any exported cell value that begins with `=`, `+`, `-` or `@` and is not a number SHALL be prefixed with a single quote to prevent spreadsheet formula execution.

#### Scenario: Malicious cell
- **WHEN** an original cell contains `=HYPERLINK("http://evil")`
- **THEN** the exported value is `'=HYPERLINK("http://evil")`

### Requirement: Streaming export for large jobs
Exports SHALL be streamed from the server without loading all rows into memory, and a 200,000-row export SHALL start downloading within 5 seconds.

#### Scenario: Large export
- **WHEN** a user exports a 200,000-row job
- **THEN** the download begins promptly and completes with all rows

### Requirement: Export filename and logging
The file SHALL be named `<job-name>_<scope>_<YYYYMMDD-HHMM>.csv` (sanitized). Every export SHALL be recorded in the audit trail with user, scope and row count.

#### Scenario: Export audited
- **WHEN** a user exports a job
- **THEN** an audit entry records the user, scope and number of rows exported

### Requirement: Attribution
The export download page SHALL show the required GeoNames and geocoding-provider attribution.

#### Scenario: Attribution shown
- **WHEN** a user opens the export dialog
- **THEN** the dialog shows the data source attributions
