## ADDED Requirements

### Requirement: Background job processing
Processing SHALL run in background workers, not in the HTTP request. Starting a job SHALL return immediately with a job id. Job statuses SHALL be `queued`, `ingesting`, `normalizing`, `ai_repair`, `geocoding`, `scoring`, `ready`, `paused`, `failed` and `cancelled`.

#### Scenario: Start job
- **WHEN** a user confirms the column mapping and starts processing
- **THEN** the API responds within 2 seconds with a job id and status `queued`

### Requirement: Job options
When starting a job, the user SHALL set: a job name, AI repair on/off (default on), and an optional default country.

#### Scenario: AI off recorded
- **WHEN** a user starts a job with AI repair off
- **THEN** the job stores `ai_repair=false` and the job page shows it

### Requirement: Deduplication
Before normalization and geocoding, the system SHALL group rows by a normalized address key, process each unique key once, and apply the result to every row in the group.

#### Scenario: Duplicates processed once
- **WHEN** a file of 10,000 rows contains 800 unique address keys
- **THEN** normalization, AI calls and geocoding run 800 times, and all 10,000 rows receive results

### Requirement: Chunked and resumable processing
Work SHALL be processed in chunks (default 500 unique addresses), with progress committed after each chunk. If a worker restarts, the job SHALL resume from the last committed chunk without reprocessing completed chunks.

#### Scenario: Worker crash
- **WHEN** a worker crashes after committing 40 of 100 chunks
- **THEN** after restart the job continues from chunk 41

### Requirement: Large-job AI batching
For AI repair, the system SHALL send requests in batches. When a job's unresolved count exceeds a configurable threshold (default 2,000), it SHALL use the OpenAI Batch API; otherwise it SHALL use concurrent synchronous requests within configured rate limits.

#### Scenario: Large job uses Batch API
- **WHEN** a job has 5,000 unresolved addresses
- **THEN** AI repair is submitted through the OpenAI Batch API and the job shows status `ai_repair` with "waiting for batch" until it completes

### Requirement: Live progress
The system SHALL stream job progress to the frontend via Server-Sent Events: current stage, processed and total counts per stage, counts per band so far, and estimated time remaining.

#### Scenario: Progress updates
- **WHEN** a user has the job page open during geocoding
- **THEN** the progress bar and band counts update at least every 2 seconds without reloading the page

### Requirement: Partial review while processing
Rows that are already scored SHALL be viewable in the review grid while the job is still running.

#### Scenario: Early review
- **WHEN** a job is 50% through geocoding
- **THEN** the user can open the grid and see the scored rows

### Requirement: Cancel, retry and resume
Users SHALL be able to cancel their running jobs, resume `paused` jobs, and retry only the rows with `geocode_error` or `ai_error` in a finished job.

#### Scenario: Cancel job
- **WHEN** a user cancels a running job
- **THEN** workers stop after the current chunk, status becomes `cancelled`, and already-processed rows stay reviewable

#### Scenario: Retry errors
- **WHEN** a user retries a ready job that has 12 `geocode_error` rows
- **THEN** only those 12 rows are reprocessed

### Requirement: Job failure handling
Unrecoverable errors (for example a corrupted file or a database failure) SHALL set the job to `failed` with a user-readable message; technical details SHALL go to server logs only.

#### Scenario: Corrupted file
- **WHEN** an Excel file cannot be parsed after upload
- **THEN** the job status is `failed` with the message "the file could not be read as Excel"

### Requirement: Job list and summary
Users SHALL see a list of their jobs (admins: all jobs) with name, owner, created time, status, row count, band distribution and review progress (confirmed/total).

#### Scenario: Job list
- **WHEN** a user opens the jobs page
- **THEN** each job shows its status, band distribution and review progress

### Requirement: Job deletion and retention
Owners and admins SHALL be able to delete a job, which removes its uploaded file, rows and results but keeps the audit entries anonymized to the job id. Uploaded source files SHALL be deleted automatically after a configurable retention period (default 30 days); rows remain until the job is deleted.

#### Scenario: Delete job
- **WHEN** an owner deletes a job
- **THEN** the job no longer appears and its rows and file are removed
