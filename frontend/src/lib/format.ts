import type { Band, JobStatus, ReviewStatus } from '../api/types'

export const BAND_COLOR: Record<Band, string> = { High: 'teal', Medium: 'yellow', Low: 'orange', Failed: 'red' }
export const BAND_HEX: Record<string, string> = { H: '#12b886', M: '#fab005', L: '#fd7e14', F: '#fa5252', '?': '#868e96' }
export const BANDS: Band[] = ['High', 'Medium', 'Low', 'Failed']
export const STATUSES: ReviewStatus[] = ['pending', 'confirmed', 'edited', 'rejected']
export const STATUS_COLOR: Record<ReviewStatus, string> = {
  pending: 'gray', confirmed: 'teal', edited: 'blue', rejected: 'red',
}
export const PRECISIONS = ['rooftop', 'street', 'postcode', 'city', 'region', 'country', 'manual', 'none']
export const CORRECTION_TYPES: { value: string; label: string }[] = [
  { value: 'country_changed', label: 'Country changed' },
  { value: 'postcode_repaired', label: 'Postcode repaired' },
  { value: 'city_fuzzy', label: 'City fuzzy-matched' },
  { value: 'fields_fixed', label: 'Fields split/swapped' },
  { value: 'ai_repaired', label: 'AI repaired' },
  { value: 'ai_disambiguated', label: 'AI disambiguated' },
  { value: 'candidate_picked', label: 'Alternative picked' },
  { value: 'manual', label: 'Manually placed' },
]
export const ACTIVE_STATUSES: JobStatus[] = ['queued', 'ingesting', 'normalizing', 'ai_repair', 'geocoding', 'scoring']
export const STAGES: { key: JobStatus; label: string }[] = [
  { key: 'ingesting', label: 'Reading file' },
  { key: 'normalizing', label: 'Repairing addresses' },
  { key: 'ai_repair', label: 'AI repair' },
  { key: 'geocoding', label: 'Geocoding & scoring' },
  { key: 'ready', label: 'Ready' },
]
export const JOB_STATUS_COLOR: Record<JobStatus, string> = {
  draft: 'gray', queued: 'gray', ingesting: 'blue', normalizing: 'blue', ai_repair: 'violet', geocoding: 'blue',
  scoring: 'blue', ready: 'teal', paused: 'yellow', failed: 'red', cancelled: 'gray',
}
export const FIELD_LABELS: Record<string, string> = {
  street: 'Street', house_number: 'House number', city: 'City', postcode: 'Postcode', country: 'Country',
  id: 'Record ID', full_address: 'Full address (one column)',
}

export function fmtNumber(n: number | null | undefined): string {
  return n == null ? '–' : n.toLocaleString()
}

export function fmtCoord(n: number | null | undefined): string {
  return n == null ? '–' : n.toFixed(6)
}

export function fmtDate(s: string | null | undefined): string {
  return s ? new Date(s).toLocaleString() : '–'
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${Math.round(seconds)} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export function statusLabel(s: JobStatus): string {
  return { ai_repair: 'AI repair', geocoding: 'Geocoding' }[s as string] ?? s.charAt(0).toUpperCase() + s.slice(1)
}
