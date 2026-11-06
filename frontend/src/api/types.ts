export type Role = 'user' | 'admin'
export type Band = 'High' | 'Medium' | 'Low' | 'Failed'
export type ReviewStatus = 'pending' | 'confirmed' | 'edited' | 'rejected'
export type JobStatus =
  | 'draft' | 'queued' | 'ingesting' | 'normalizing' | 'ai_repair' | 'geocoding' | 'scoring'
  | 'ready' | 'paused' | 'failed' | 'cancelled'

export interface Me {
  id: number
  username: string
  role: Role
  must_change_password: boolean
}

export interface AdminUser extends Me {
  active: boolean
  created_at: string
}

export interface Capabilities {
  ai_available: boolean
  street_geocoding_available: boolean
  geocoder_provider: string | null
  gazetteer_imported_at: string | null
  gazetteer_countries: number
  max_upload_mb: number
  max_rows: number
  attribution: string[]
}

export type TargetField = 'street' | 'house_number' | 'city' | 'postcode' | 'country' | 'id' | 'full_address'
export type Mapping = Record<TargetField, string | null>

export interface JobSummary {
  bands: Partial<Record<Band | 'unscored', number>>
  review: Partial<Record<ReviewStatus, number>>
  review_by_band?: Record<string, Partial<Record<ReviewStatus, number>>>
}

export interface Job {
  id: string
  name: string
  owner_id: number
  owner: string | null
  status: JobStatus
  message: string | null
  file_name: string
  options: { ai_repair?: boolean; ai_requested?: boolean; default_country?: string | null }
  mapping: Mapping
  counts: Record<string, number>
  scoring_config_version: number | null
  created_at: string | null
  finished_at: string | null
  file_deleted: boolean
  summary?: JobSummary
}

export interface FileMeta {
  format: 'csv' | 'excel'
  columns: string[]
  encoding: string | null
  delimiter: string | null
  sheets: string[]
  sheet: string | null
  has_header: boolean
  empty_rows_skipped: number
  row_count: number
}

export interface Preview {
  file: FileMeta
  columns: string[]
  rows: string[][]
  mapping: Mapping
  mapping_source: Partial<Record<TargetField, 'heuristic' | 'ai'>>
}

export interface RowFilter {
  bands?: Band[]
  score_min?: number
  score_max?: number
  statuses?: ReviewStatus[]
  precisions?: string[]
  corrections?: string[]
  errors_only?: boolean
  countries?: string[]
  q?: string
  row_ids?: number[]
}

export interface ResultRow {
  id: number
  row_index: number
  in_street: string
  in_house_number: string
  in_city: string
  in_postcode: string
  in_country: string
  m_street: string | null
  m_city: string | null
  m_postcode: string | null
  m_country: string | null
  lat: number | null
  lon: number | null
  score: number | null
  band: Band | null
  precision: string | null
  source: string | null
  flags: string[]
  error: string | null
  status: ReviewStatus
  version: number
  overridden: boolean
  reviewed_by: string | null
  reviewed_at: string | null
}

export interface RowsPage {
  rows: ResultRow[]
  next_cursor: string | null
  total: number | null
}

export interface Reason {
  code: string
  penalty: number
  text: string
}

export interface CorrectionEntry {
  field: string
  from: string
  to: string
  tier: string
  reason: string
  sentence: string
}

export interface CandidateOut {
  id: number
  rank: number
  lat: number
  lon: number
  source: string
  precision: string
  label: string
  score: number | null
  band: Band | null
  reasons: Reason[]
  flags: string[]
  selected: boolean
}

export interface RowDetail {
  row: ResultRow
  columns: string[]
  raw: string[]
  normalized: Record<string, string | number | null>
  corrections: CorrectionEntry[]
  reasons: Reason[]
  ai_reason: string | null
  override: Record<string, unknown> | null
  candidates: CandidateOut[]
  history: { at: string; user: string | null; action: string; count: number | null; details: Record<string, unknown> }[]
}

export interface ProgressEvent {
  id: string
  status: JobStatus
  message: string | null
  counts: Record<string, number>
  stage_done?: number
  stage_total?: number
  eta_seconds?: number | null
  bands?: JobSummary['bands']
  review?: JobSummary['review']
}
