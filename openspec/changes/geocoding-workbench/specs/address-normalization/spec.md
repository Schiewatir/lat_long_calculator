## ADDED Requirements

### Requirement: Basic text cleanup
The system SHALL trim whitespace, collapse internal whitespace, apply Unicode NFC normalization and remove control characters on all mapped fields before any other step. Raw input values MUST be kept unchanged alongside the normalized values.

#### Scenario: Whitespace and control characters
- **WHEN** the city input is "  Berlin\t "
- **THEN** the normalized city is "Berlin" and the raw value is kept

### Requirement: Country resolution
The system SHALL resolve the country input to an ISO 3166-1 alpha-2 code using an alias table that covers alpha-2, alpha-3, numeric codes, English names, local names and common variants (for example "GER", "Deutschland", "D", "UK", "Holland", "U.S.A."), matched case- and accent-insensitively. The resolution method SHALL be recorded as one of `exact`, `alias`, `fuzzy`, `inferred` or `job_default`.

#### Scenario: Alpha-3 alias
- **WHEN** the country input is "DEU"
- **THEN** the resolved country is "DE" with method `alias`

#### Scenario: Common non-ISO code
- **WHEN** the country input is "UK"
- **THEN** the resolved country is "GB" with method `alias`

#### Scenario: Misspelled country name
- **WHEN** the country input is "Germnay"
- **THEN** the resolved country is "DE" with method `fuzzy` and the fuzzy score is recorded

### Requirement: Cross-field country validation and override
The system SHALL check the resolved country against postcode and city evidence: the postcode format for that country, whether the postcode exists in that country's gazetteer, and whether the city exists in that country. If the given country conflicts with the evidence and another country is supported by both the postcode and the city, the system SHALL override the country, set method `inferred`, and record the correction with its reason.

#### Scenario: Corrupted country code
- **WHEN** the input is city "Munchen", postcode "80331", country "GE"
- **THEN** the resolved country is "DE" with method `inferred` and the reason "country GE→DE: postcode 80331 and city München match DE, not GE"

#### Scenario: Consistent input not overridden
- **WHEN** the input is city "Tbilisi", postcode "0105", country "GE"
- **THEN** the resolved country stays "GE"

#### Scenario: Missing country inferred
- **WHEN** the country input is empty, no job default is set, and postcode "75001" with city "Paris" match only FR
- **THEN** the resolved country is "FR" with method `inferred`

### Requirement: Postcode repair
The system SHALL validate postcodes against per-country format rules and SHALL repair common corruptions: restoring leading zeros lost by spreadsheets (for example DE "1067"→"01067", US "2134"→"02134", FR "6000"→"06000"), canonical spacing and casing (GB "sw1a1aa"→"SW1A 1AA", NL "1234ab"→"1234 AB"), removing country prefixes ("D-80331"→"80331"), stripping a ".0" suffix from Excel numeric conversion, and reducing extended formats to the gazetteer key (US ZIP+4 "02134-1234"→"02134"). Each repair SHALL be recorded.

#### Scenario: Leading zero restored
- **WHEN** the country is DE and the postcode input is "1067"
- **THEN** the normalized postcode is "01067" and the correction "postcode zero-padded 1067→01067" is recorded

#### Scenario: Excel float artifact
- **WHEN** the postcode input is "80331.0"
- **THEN** the normalized postcode is "80331"

#### Scenario: Invalid postcode kept and flagged
- **WHEN** the country is DE and the postcode input is "ABCDE"
- **THEN** the postcode is flagged `invalid_format` and not used for lookup

### Requirement: City matching
The system SHALL match the city input against gazetteer place names and alternate names (including exonyms such as "Munich"/"München" and transliterations) scoped to the resolved country, accent- and case-insensitively, normalizing common abbreviations ("St."→"Saint"/"Sankt" as appropriate for the country). If there is no exact match, the system SHALL fuzzy-match with a similarity threshold (default 85) and record the best score. If several places match equally, the system SHALL mark the city `ambiguous`, prefer candidates consistent with the postcode, and keep the alternatives.

#### Scenario: Exonym
- **WHEN** the country is DE and the city input is "Munich"
- **THEN** the matched city is "München" with match type `alias`

#### Scenario: Typo
- **WHEN** the country is DE and the city input is "Muenchn"
- **THEN** the matched city is "München" with match type `fuzzy` and the score is recorded

#### Scenario: Ambiguous city resolved by postcode
- **WHEN** the country is DE, the city input is "Frankfurt" and the postcode is "15230"
- **THEN** the matched city is "Frankfurt (Oder)" and the reason cites the postcode

#### Scenario: Ambiguous city without postcode
- **WHEN** the country is DE, the city input is "Frankfurt" and the postcode is empty
- **THEN** the city is marked `ambiguous` with candidates "Frankfurt am Main" and "Frankfurt (Oder)"

### Requirement: Swapped and misplaced field detection
The system SHALL detect values that clearly belong to another field (for example a postcode in the city column, "80331 München" combined in one field, or city and postcode swapped) and SHALL split or move them, recording the correction.

#### Scenario: Combined postcode and city
- **WHEN** the city input is "80331 München" and the postcode input is empty
- **THEN** the normalized postcode is "80331", the city is "München", and the correction is recorded

### Requirement: Deterministic resolution status
After the deterministic tier, each unique address SHALL be classified as `resolved` (country, plus postcode or city, determined consistently), `unresolved` (required evidence missing or inconsistent) or `ambiguous`.

#### Scenario: Clean row resolved without AI
- **WHEN** the input is "Paris", "75001", "FR"
- **THEN** the status is `resolved` and no OpenAI call is made for it

### Requirement: AI repair tier
When AI repair is enabled for the job, the system SHALL send only `unresolved` addresses to OpenAI, with only the mapped address fields (street, house number, city, postcode, country) and an opaque identifier, and request structured JSON output with corrected fields and a short reason. AI output MUST NOT include coordinates and MUST be re-validated by the deterministic tier; a correction that fails re-validation SHALL be discarded and the row left `unresolved`. AI-corrected rows SHALL be marked `ai_repaired`.

#### Scenario: AI repairs garbled row
- **WHEN** an unresolved row "Frnakfurt a.M.", "6031", "DEU." is sent to OpenAI and it returns city "Frankfurt am Main", postcode "60311", country "DE"
- **THEN** the deterministic tier confirms the postcode exists in that city, and the row becomes `resolved` with flag `ai_repaired` and the AI reason stored

#### Scenario: AI hallucination rejected
- **WHEN** OpenAI returns a postcode that does not exist in the returned country
- **THEN** the AI correction is discarded and the row stays `unresolved` with the reason "AI suggestion failed validation"

#### Scenario: No extra columns sent
- **WHEN** a job contains a "Customer Name" column
- **THEN** no OpenAI request payload contains values from that column

### Requirement: AI repair disabled
When AI repair is disabled for the job, the system MUST NOT send any job data to OpenAI. `unresolved` rows SHALL proceed to geocoding with the best deterministic input.

#### Scenario: AI disabled
- **WHEN** a job has AI repair disabled and contains unresolved rows
- **THEN** no OpenAI API call is made for that job

### Requirement: AI disambiguation tier
When AI repair is enabled and geocoding yields several plausible candidates with no deterministic winner, the system SHALL ask OpenAI to choose one candidate by its identifier given the original input fields, or to choose none. The chosen candidate's coordinates SHALL come from the geocoder, never from the AI response.

#### Scenario: AI picks candidate
- **WHEN** two candidates remain and OpenAI selects candidate id "c2" with a reason
- **THEN** candidate "c2" becomes the result with flag `ai_disambiguated` and the other candidate is kept as an alternative

### Requirement: Correction log
Every change made to an input value by any tier SHALL be recorded per row as a structured entry (field, from, to, tier, reason) and as a human-readable sentence.

#### Scenario: Multiple corrections
- **WHEN** a row has its country aliased and its postcode zero-padded
- **THEN** the row's correction log contains two entries, in the order they were applied
