## ADDED Requirements

### Requirement: Row review status
Each row SHALL have a review status, one of `pending`, `confirmed`, `edited` or `rejected`. All rows start as `pending`. `edited` means the user changed the result (picked an alternative, moved the pin, or edited the input and re-ran it) and SHALL count as confirmed for export. Users SHALL be able to reset a row to `pending`.

#### Scenario: Initial status
- **WHEN** a job finishes processing
- **THEN** every row has status `pending`

### Requirement: Results grid
The system SHALL show job results in a virtualized grid with server-side paging, sorting and filtering, which stays responsive for 200,000 rows. Columns SHALL include row number, original street/city/postcode/country, matched street/city/postcode/country, lat, lon, confidence (with a band color), precision, corrections indicator and review status.

#### Scenario: Large job scrolling
- **WHEN** a user scrolls through a 200,000-row job
- **THEN** only visible pages are fetched and scrolling stays smooth

#### Scenario: Sort by confidence
- **WHEN** the user sorts by confidence ascending
- **THEN** the lowest-scoring rows appear first

### Requirement: Filters and search
The grid SHALL support filtering by band, confidence range, review status, precision level, correction type (country changed, postcode repaired, city fuzzy, AI repaired, AI disambiguated), error state and country, plus free-text search on original and matched values. The current filter's row count SHALL be shown.

#### Scenario: Filter low and failed
- **WHEN** the user filters by bands Low and Failed
- **THEN** only those rows are shown with the matching count

#### Scenario: Filter AI-repaired
- **WHEN** the user filters by correction type "AI repaired"
- **THEN** only rows with the `ai_repaired` flag are shown

### Requirement: Row detail panel
Selecting a row SHALL open a detail panel showing original and normalized values side by side, the correction log, score reasons with penalties, the source and precision, a map with the result pin, and alternative candidates as differently styled pins with their own scores.

#### Scenario: Open detail
- **WHEN** a user clicks a row
- **THEN** the detail panel opens with the map centered on the result and the alternatives listed

### Requirement: Single-row actions
From the grid or the detail panel, users SHALL be able to: confirm, reject, pick an alternative candidate, drag the pin to set manual coordinates, or edit the input fields and re-run processing for that row. Keyboard shortcuts SHALL exist for confirm, reject and next row.

#### Scenario: Pick alternative
- **WHEN** the user selects alternative candidate 2
- **THEN** the row's result becomes candidate 2, it is re-scored, and its status becomes `edited`

#### Scenario: Manual pin
- **WHEN** the user drags the pin to a new location and saves
- **THEN** lat/lon are updated, the source is `manual`, precision is `manual`, the reason "manually placed by <username>" is recorded, and the status is `edited`

#### Scenario: Edit and re-run
- **WHEN** the user corrects the city input and clicks re-run
- **THEN** the row is normalized, geocoded and scored again synchronously, and the new result is shown for confirmation

#### Scenario: Keyboard review
- **WHEN** a user presses the confirm shortcut on a selected row
- **THEN** the row is confirmed and the selection moves to the next row

### Requirement: Bulk actions on filter
Users SHALL be able to confirm, reject or reset to pending all rows matching the current filter, and also an explicitly selected set of rows. Before a bulk action affecting more than 100 rows runs, the system SHALL show a confirmation dialog with the exact row count. Bulk actions SHALL run server-side using the filter definition, not a client-side list of ids.

#### Scenario: Confirm all high-confidence
- **WHEN** a user filters by confidence ≥ 90 and status pending, then clicks "Confirm all matching" and accepts the dialog showing 18,240 rows
- **THEN** exactly those 18,240 rows become `confirmed`

#### Scenario: Confirm entire job
- **WHEN** the user clears all filters and confirms all
- **THEN** every row that is not `rejected` becomes `confirmed`

### Requirement: Map overview
The job SHALL have a map view showing all geocoded rows of the current filter as clustered markers colored by band. Clicking a marker SHALL open its row detail.

#### Scenario: Outlier spotting
- **WHEN** a user opens the map for a job of German addresses
- **THEN** a row geocoded in another country is visible as a separate marker

### Requirement: Audit trail
Every review action (single or bulk) SHALL be recorded with user, timestamp, action, affected row ids or bulk filter definition with count, and before/after values for edits. Each row's detail panel SHALL show its history.

#### Scenario: Row history
- **WHEN** user A moves a pin and user B (admin) later confirms the row
- **THEN** the row history shows both actions with usernames and times

### Requirement: Concurrent review safety
When two users review the same job (owner and admin), the system SHALL use optimistic concurrency per row; a stale update SHALL be rejected and the user shown the current value.

#### Scenario: Conflicting edit
- **WHEN** two users edit the same row and the second saves based on an outdated version
- **THEN** the second save is rejected with a message and the panel refreshes to the current state

### Requirement: Review progress
The job page SHALL show review progress: counts of pending, confirmed, edited and rejected rows, overall and per band.

#### Scenario: Progress counts
- **WHEN** a user confirms 10 rows
- **THEN** the confirmed count increases by 10 and the pending count decreases by 10
