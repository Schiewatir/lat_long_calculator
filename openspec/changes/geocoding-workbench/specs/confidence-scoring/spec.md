## ADDED Requirements

### Requirement: Deterministic confidence score
Each geocoded row SHALL receive an integer confidence score from 0 to 100, computed deterministically from recorded signals. The score MUST NOT come from any LLM self-assessment. The same inputs and configuration MUST always produce the same score.

#### Scenario: Reproducible
- **WHEN** a row is scored twice with the same signals and configuration
- **THEN** both scores are identical

### Requirement: Penalty model
The score SHALL start at 100 and subtract configurable penalties for each signal present. Default penalties:
- country resolved via alias: 3
- country resolved via fuzzy match: 8
- country inferred or overridden: 15
- country from job default: 2
- postcode repaired (padding/format): 5
- postcode invalid or not found: 25
- city matched via alias: 0
- city fuzzy matched: 5 + (100 − similarity) × 0.75, rounded half up, at most 20
- city ambiguous with no deterministic winner: 15
- field split or swapped: 5
- input repaired by AI: 10
- candidate chosen by AI disambiguation: 10
- postcode-to-city centroid distance 10–50 km: 15
- postcode-to-city centroid distance over 50 km: 35
- provider candidate `postcode_mismatch`: 20
- provider-native confidence low (provider-specific threshold): 10
- only a partial postcode given (e.g. GB outward code): 5
- city given but not found in the gazetteer (located by postcode): 10

The final score SHALL be clamped to the range 0–100.

#### Scenario: Clean postcode row
- **WHEN** the input is "Paris", "75001", "FR" with exact matches and consistent distance
- **THEN** the score is 100

#### Scenario: Corrupted country with fuzzy city
- **WHEN** the country is inferred (GE→DE) and the city is fuzzy matched at similarity 92, and everything else is consistent
- **THEN** the score is 100 − 15 − 11 = 74

### Requirement: Precision caps
The score SHALL be capped by precision level: `city` at most 70, `region` at most 40, `country` at most 20, `none` exactly 0. A street was given but only postcode precision was reached: at most 85. The cap is the starting maximum and penalties are subtracted from it, so that input problems still lower a capped score.

#### Scenario: City-level only
- **WHEN** a row with an otherwise perfect match is geocoded only to city level
- **THEN** its score is 70

#### Scenario: Ambiguous city at city level
- **WHEN** a row is geocoded to city level and its city is ambiguous (−15)
- **THEN** its score is 55 (band Low), lower than an unambiguous city-level match

#### Scenario: No result
- **WHEN** a row has no coordinates
- **THEN** its score is 0

### Requirement: Confidence bands
The score SHALL map to bands: High (90–100), Medium (70–89), Low (40–69), Failed (0–39). Band thresholds SHALL be configurable.

#### Scenario: Band assignment
- **WHEN** a row scores 74
- **THEN** its band is Medium

### Requirement: Explainable reasons
Each row SHALL store a list of reasons, one per signal that changed the score or applied a cap, each with its penalty and a human-readable sentence, ordered by penalty size. Rows with score 100 SHALL have a single positive reason such as "exact postcode and city match".

#### Scenario: Reasons displayed
- **WHEN** a user opens a row scored 74
- **THEN** they see "−15 country GE→DE inferred from postcode and city" and "−11 city 'Muenchn' fuzzy-matched to 'München' (92%)"

### Requirement: Scoring configuration
Penalties, caps, distance thresholds and band thresholds SHALL be held in one versioned configuration. Each job SHALL record the configuration version used to score it. Changing the configuration MUST NOT silently re-score existing jobs; admins SHALL be able to explicitly re-score a job, which does not change review statuses.

#### Scenario: Config version recorded
- **WHEN** a job finishes
- **THEN** the job stores the scoring configuration version used

#### Scenario: Explicit re-score
- **WHEN** an admin re-scores a job after a configuration change
- **THEN** scores and bands update, and confirmed rows stay confirmed

### Requirement: Calibration support
The system SHALL provide a command that, given a CSV of hand-labeled rows (input plus true lat/lon), reports for each band the share of rows within 1 km, 5 km and 25 km of the truth, so that penalties can be tuned.

#### Scenario: Calibration report
- **WHEN** the calibration command runs on a labeled file
- **THEN** it outputs per-band accuracy statistics and row counts

### Requirement: Manually placed coordinates
A row whose coordinates were placed manually by a user SHALL have score 100 and band High, with the reason "manually placed by <username>". Its earlier automatic score SHALL be kept in the row history.

#### Scenario: Manual pin scored
- **WHEN** a user drags a Low-band row's pin and saves
- **THEN** the row's score becomes 100, its band High, and its history keeps the previous score
