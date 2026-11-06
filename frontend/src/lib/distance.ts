/** Pure helpers for the Distance page (parsing, formatting, polyline decoding). No React here. */
import type { AddressFields, DistanceResponse, LocationInput, Place, Precision } from '../api/types'

export type Unit = 'km' | 'mi'
export const UNIT_STORAGE_KEY = 'gw.distanceUnit'

export const EMPTY_ADDRESS: AddressFields = {
  street: '', house_number: '', postcode: '', city: '', region: '', country: '', text: '',
}

export interface LocationForm {
  mode: 'address' | 'coords'
  address: AddressFields
  lat: string
  lon: string
}

export const emptyForm = (mode: LocationForm['mode'] = 'address'): LocationForm => ({
  mode, address: { ...EMPTY_ADDRESS }, lat: '', lon: '',
})

/** A single coordinate; accepts a decimal comma ("48,137") when there is no dot. */
export function parseCoord(raw: string): number | null {
  const s = raw.trim()
  if (!s) return null
  const normalized = s.includes('.') ? s : s.replace(',', '.')
  if (!/^[+-]?\d+(\.\d+)?$/.test(normalized)) return null
  const n = Number(normalized)
  return Number.isFinite(n) ? n : null
}

/** "lat, lon" | "lat; lon" | "lat lon" (decimal commas allowed with ";") -> [lat, lon], or null. */
export function parseLatLonPair(raw: string): [number, number] | null {
  const s = raw.trim()
  let parts: string[]
  if (s.includes(';')) parts = s.split(';')
  else if (/^\S+\s*,\s*\S+$/.test(s) && (s.match(/,/g) ?? []).length === 1) parts = s.split(',')
  else parts = s.split(/\s+/)
  if (parts.length !== 2) return null
  const lat = parseCoord(parts[0])
  const lon = parseCoord(parts[1])
  if (lat === null || lon === null || !validLat(lat) || !validLon(lon)) return null
  return [lat, lon]
}

export const validLat = (n: number) => n >= -90 && n <= 90
export const validLon = (n: number) => n >= -180 && n <= 180

/** Client-side validation mirroring the API; returns field -> message. */
export function validateForm(f: LocationForm): Record<string, string> {
  const errors: Record<string, string> = {}
  if (f.mode === 'address') {
    if (!Object.values(f.address).some((v) => v.trim())) errors.address = 'Enter at least one address field'
    return errors
  }
  const lat = parseCoord(f.lat)
  const lon = parseCoord(f.lon)
  if (lat === null) errors.lat = 'Enter a decimal latitude'
  else if (!validLat(lat)) errors.lat = 'Latitude must be between -90 and 90'
  if (lon === null) errors.lon = 'Enter a decimal longitude'
  else if (!validLon(lon)) errors.lon = 'Longitude must be between -180 and 180'
  return errors
}

/** The API payload for one side. A picked Google alternative is sent as coordinates (no new geocoding). */
export function toLocationInput(f: LocationForm, picked: Place | null): LocationInput {
  if (picked) {
    return { type: 'coords', lat: picked.lat, lon: picked.lon, label: picked.label, precision: picked.precision,
      place_id: picked.place_id }
  }
  if (f.mode === 'coords') return { type: 'coords', lat: parseCoord(f.lat)!, lon: parseCoord(f.lon)! }
  const address: Partial<AddressFields> = {}
  for (const [k, v] of Object.entries(f.address) as [keyof AddressFields, string][]) if (v.trim()) address[k] = v.trim()
  return { type: 'address', ...address }
}

export function fmtDistance(d: { km: number; mi: number }, unit: Unit): string {
  const v = unit === 'km' ? d.km : d.mi
  const digits = v < 10 ? 2 : v < 1000 ? 1 : 0
  return `${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${unit}`
}

export function fmtDriveTime(seconds: number): string {
  const mins = Math.round(seconds / 60)
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h} h ${m} min` : `${h} h`
}

export const PRECISION_LABEL: Record<Precision, string> = {
  rooftop: 'Rooftop', street: 'Street', postcode: 'Postcode', city: 'City', region: 'Region', country: 'Country',
  manual: 'Coordinates',
}

export const ROAD_UNAVAILABLE_TEXT: Record<string, string> = {
  disabled: 'Road distance not enabled',
  no_route: 'No drivable route between these points',
  provider_error: 'Road distance temporarily unavailable',
  same_point: 'Origin and destination are the same point',
}

export function summaryText(r: DistanceResponse, unit: Unit): string {
  const side = (name: string, p: DistanceResponse['origin']) =>
    `${name}: ${p.label} (${p.lat.toFixed(6)}, ${p.lon.toFixed(6)})`
  const lines = [
    side('Origin', r.origin),
    side('Destination', r.destination),
    `Straight-line distance: ${fmtDistance(r.straight_line, unit)}`,
  ]
  if (r.road) lines.push(`Road distance: ${fmtDistance(r.road, unit)} (${fmtDriveTime(r.road.duration_s)} driving)`)
  if (r.attribution.length) lines.push(r.attribution.join('; '))
  return lines.join('\n')
}

/** Google encoded polyline -> [lat, lon][] (https://developers.google.com/maps/documentation/utilities/polylinealgorithm). */
export function decodePolyline(encoded: string): [number, number][] {
  const points: [number, number][] = []
  let index = 0
  let lat = 0
  let lon = 0
  const next = () => {
    let result = 0
    let shift = 0
    let b: number
    do {
      b = encoded.charCodeAt(index++) - 63
      result |= (b & 0x1f) << shift
      shift += 5
    } while (b >= 0x20)
    return result & 1 ? ~(result >> 1) : result >> 1
  }
  while (index < encoded.length) {
    lat += next()
    lon += next()
    points.push([lat / 1e5, lon / 1e5])
  }
  return points
}

/** Coordinates-only sides are shareable in the URL ("o=lat,lon"); address sides never are. */
export function formFromParam(value: string | null): LocationForm | null {
  if (!value) return null
  const pair = parseLatLonPair(value)
  return pair ? { ...emptyForm('coords'), lat: String(pair[0]), lon: String(pair[1]) } : null
}

export function paramFromForm(f: LocationForm, picked: Place | null): string | null {
  if (picked || f.mode !== 'coords') return null
  const lat = parseCoord(f.lat)
  const lon = parseCoord(f.lon)
  return lat === null || lon === null ? null : `${lat},${lon}`
}
