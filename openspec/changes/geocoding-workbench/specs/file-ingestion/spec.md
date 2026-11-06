## ADDED Requirements

### Requirement: Supported file formats
The system SHALL accept uploads of `.csv`, `.txt`, `.tsv`, `.xlsx` and `.xls` files. Any other format MUST be rejected with a clear error.

#### Scenario: Upload CSV
- **WHEN** a user uploads a valid `.csv` file
- **THEN** the system stores it and returns a preview of the first 20 rows with detected columns

#### Scenario: Unsupported format
- **WHEN** a user uploads a `.pdf` file
- **THEN** the system responds with 415 and lists the supported formats

### Requirement: Encoding and delimiter detection
For text-based files the system SHALL auto-detect the character encoding (at least UTF-8, UTF-8 with BOM, UTF-16, Windows-1252, ISO-8859-1) and the delimiter (comma, semicolon, tab, pipe). The user SHALL be able to override both detected values before processing.

#### Scenario: Semicolon-delimited Windows-1252 file
- **WHEN** a user uploads a semicolon-delimited file encoded in Windows-1252 that contains "München"
- **THEN** the preview shows the separate columns and the text "München" correctly

#### Scenario: User overrides delimiter
- **WHEN** the user changes the detected delimiter in the preview
- **THEN** the preview is re-parsed with the chosen delimiter

### Requirement: Excel sheet selection
For Excel files with more than one sheet, the system SHALL let the user choose which sheet to process; by default it SHALL use the first non-empty sheet.

#### Scenario: Multi-sheet workbook
- **WHEN** a user uploads an `.xlsx` with sheets "Info" (empty) and "Addresses"
- **THEN** the preview defaults to "Addresses" and offers a sheet selector

### Requirement: Values read as text
All cell values SHALL be read as text, with no type inference, so that postcodes and codes keep their original form (for example leading zeros and values like "0123").

#### Scenario: Leading zero preserved
- **WHEN** a CSV contains postcode "01067"
- **THEN** the stored raw value is the string "01067"

### Requirement: Header detection
The system SHALL detect whether the first row is a header. If none is detected, columns SHALL be named `column_1`, `column_2`, and so on. The user SHALL be able to toggle the header setting in the preview.

#### Scenario: Headerless file
- **WHEN** a file's first row contains data values rather than labels
- **THEN** the preview shows generated column names and the first row as data

### Requirement: Size and row limits
The system SHALL reject files larger than a configurable size limit (default 100 MB) or with more than a configurable number of data rows (default 250,000). Completely empty rows SHALL be skipped and counted.

#### Scenario: Too many rows
- **WHEN** a file contains 300,000 data rows and the limit is 250,000
- **THEN** the system rejects it with an error stating the row count and the limit

#### Scenario: Empty rows skipped
- **WHEN** a file contains 5 completely empty rows
- **THEN** those rows are not imported and the job summary reports "5 empty rows skipped"

### Requirement: Column mapping suggestion
The system SHALL suggest a mapping from file columns to the target fields `street`, `house_number`, `city`, `postcode`, `country`, and optionally `id`. It SHALL first use header-name heuristics in multiple languages (for example "PLZ", "Ort", "Land", "CP", "Ville", "zip", "Stadt"). Only if heuristics leave required fields unmapped and AI repair is enabled SHALL it ask OpenAI, sending only the headers and at most 5 sample rows.

#### Scenario: German headers
- **WHEN** a file has headers "Straße", "PLZ", "Ort", "Land"
- **THEN** the suggested mapping is street, postcode, city, country without calling OpenAI

#### Scenario: Ambiguous headers with AI enabled
- **WHEN** headers are "A", "B", "C" and AI repair is enabled
- **THEN** the system calls OpenAI with the headers and up to 5 sample rows and returns its suggested mapping marked "AI-suggested"

### Requirement: Column mapping confirmation
The user MUST confirm or edit the column mapping before processing starts. At least one of `city` or `postcode` MUST be mapped. `country` SHALL be required unless the user selects a default country for the job. The user SHALL be able to map a single "full address" column instead of separate fields.

#### Scenario: Missing required fields
- **WHEN** the user tries to start processing with neither city nor postcode mapped
- **THEN** the system blocks it and highlights the missing mapping

#### Scenario: Default country
- **WHEN** no country column exists and the user selects "DE" as the job's default country
- **THEN** processing is allowed and rows use "DE" as their country input, recorded as "country from job default"

### Requirement: Original data preserved
The system SHALL store every original row with all of its columns, in original order, so that exports can reproduce them.

#### Scenario: Unmapped columns retained
- **WHEN** a file has a "Customer ID" column that is not mapped
- **THEN** that column and its values appear unchanged in the export
