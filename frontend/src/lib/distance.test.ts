import { describe, expect, it } from 'vitest'
import {
  decodePolyline, emptyForm, fmtDistance, fmtDriveTime, formFromParam, paramFromForm, parseCoord, parseLatLonPair,
  toLocationInput, validateForm,
} from './distance'

describe('parseLatLonPair', () => {
  it.each([
    ['48.1374, 11.5755', [48.1374, 11.5755]],
    ['48.1374,11.5755', [48.1374, 11.5755]],
    ['48.1374; 11.5755', [48.1374, 11.5755]],
    ['48,1374; 11,5755', [48.1374, 11.5755]],
    ['48.1374 11.5755', [48.1374, 11.5755]],
    ['  -33.86\t151.21 ', [-33.86, 151.21]],
    ['+40.7,-74.0', [40.7, -74.0]],
  ])('parses %j', (raw, expected) => {
    expect(parseLatLonPair(raw)).toEqual(expected)
  })

  it.each(['48.1374', 'abc, def', '95, 10', '10, 200', '1, 2, 3', ''])('rejects %j', (raw) => {
    expect(parseLatLonPair(raw)).toBeNull()
  })
})

describe('parseCoord', () => {
  it('accepts decimal comma and rejects junk', () => {
    expect(parseCoord('48,137')).toBe(48.137)
    expect(parseCoord(' -0.5 ')).toBe(-0.5)
    expect(parseCoord('1e3')).toBeNull()
    expect(parseCoord('')).toBeNull()
  })
})

describe('validateForm', () => {
  it('requires one address field', () => {
    expect(validateForm(emptyForm('address'))).toHaveProperty('address')
    expect(validateForm({ ...emptyForm('address'), address: { ...emptyForm().address, city: 'Lyon' } })).toEqual({})
  })
  it('checks coordinate ranges', () => {
    expect(validateForm({ ...emptyForm('coords'), lat: '95', lon: '0' })).toHaveProperty('lat')
    expect(validateForm({ ...emptyForm('coords'), lat: '0', lon: '-181' })).toHaveProperty('lon')
    expect(validateForm({ ...emptyForm('coords'), lat: '52.52', lon: '13.405' })).toEqual({})
  })
})

describe('toLocationInput', () => {
  it('drops empty address fields and trims', () => {
    const f = { ...emptyForm('address'), address: { ...emptyForm().address, postcode: ' 80331 ', country: 'DE' } }
    expect(toLocationInput(f, null)).toEqual({ type: 'address', postcode: '80331', country: 'DE' })
  })
  it('sends a picked alternative as coords', () => {
    const picked = { label: 'Frankfurt (Oder)', lat: 52.34, lon: 14.55, precision: 'city' as const, place_id: 'p', partial_match: false }
    expect(toLocationInput(emptyForm('address'), picked)).toMatchObject({ type: 'coords', lat: 52.34, label: 'Frankfurt (Oder)' })
  })
})

describe('formatting', () => {
  it('formats distances and drive time', () => {
    expect(fmtDistance({ km: 504.23, mi: 313.31 }, 'km')).toMatch(/504[.,]2 km/)
    expect(fmtDistance({ km: 504.23, mi: 313.31 }, 'mi')).toMatch(/313[.,]3 mi/)
    expect(fmtDriveTime(19800)).toBe('5 h 30 min')
    expect(fmtDriveTime(600)).toBe('10 min')
  })
})

describe('decodePolyline', () => {
  it('decodes the reference example', () => {
    // Example from Google's polyline algorithm documentation.
    expect(decodePolyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')).toEqual([[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])
  })
})

describe('URL params', () => {
  it('round-trips coords and never exposes addresses', () => {
    const f = formFromParam('52.52,13.405')!
    expect(f.mode).toBe('coords')
    expect(paramFromForm(f, null)).toBe('52.52,13.405')
    expect(paramFromForm({ ...emptyForm('address'), address: { ...emptyForm().address, city: 'Berlin' } }, null)).toBeNull()
    expect(formFromParam('garbage')).toBeNull()
  })
})
