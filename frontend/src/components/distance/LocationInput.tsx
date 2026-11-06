import { Alert, Card, Grid, SegmentedControl, Select, Stack, Text, TextInput, Tooltip } from '@mantine/core'
import type { ClipboardEvent } from 'react'
import type { AddressFields } from '../../api/types'
import { COUNTRY_OPTIONS } from '../../lib/countries'
import { type LocationForm, parseLatLonPair } from '../../lib/distance'

interface Props {
  title: string
  value: LocationForm
  onChange: (next: LocationForm) => void
  errors: Record<string, string>
  serverError?: string | null
  addressAvailable: boolean
}

export function LocationInput({ title, value, onChange, errors, serverError, addressAvailable }: Props) {
  const setAddress = (k: keyof AddressFields, v: string) => onChange({ ...value, address: { ...value.address, [k]: v } })

  const onPasteCoords = (e: ClipboardEvent<HTMLInputElement>) => {
    const pair = parseLatLonPair(e.clipboardData.getData('text'))
    if (!pair) return
    e.preventDefault()
    onChange({ ...value, lat: String(pair[0]), lon: String(pair[1]) })
  }

  const field = (k: keyof AddressFields, label: string, placeholder?: string) => (
    <TextInput label={label} placeholder={placeholder} value={value.address[k]} disabled={!addressAvailable}
      onChange={(e) => setAddress(k, e.currentTarget.value)} />
  )

  return (
    <Card withBorder padding="md">
      <Stack gap="sm">
        <Text fw={600}>{title}</Text>
        <SegmentedControl
          value={value.mode}
          onChange={(mode) => onChange({ ...value, mode: mode as LocationForm['mode'] })}
          data={[
            {
              value: 'address',
              disabled: !addressAvailable,
              label: addressAvailable ? 'Address' : (
                <Tooltip label="Address lookup not configured (no Google API key)"><span>Address</span></Tooltip>
              ),
            },
            { value: 'coords', label: 'Coordinates' },
          ]}
        />
        {value.mode === 'address' && !addressAvailable && (
          <Alert color="yellow" variant="light">Address lookup not configured. Use coordinates instead.</Alert>
        )}
        {value.mode === 'address' ? (
          <Grid gap="xs">
            <Grid.Col span={9}>{field('street', 'Street')}</Grid.Col>
            <Grid.Col span={3}>{field('house_number', 'No.')}</Grid.Col>
            <Grid.Col span={4}>{field('postcode', 'Postcode')}</Grid.Col>
            <Grid.Col span={8}>{field('city', 'City')}</Grid.Col>
            <Grid.Col span={6}>{field('region', 'State / region')}</Grid.Col>
            <Grid.Col span={6}>
              <Select label="Country" placeholder="Any" data={COUNTRY_OPTIONS} searchable clearable
                disabled={!addressAvailable} value={value.address.country || null}
                onChange={(v) => setAddress('country', v ?? '')} />
            </Grid.Col>
            <Grid.Col span={12}>
              {field('text', 'Or free text', 'e.g. Eiffel Tower, Paris')}
            </Grid.Col>
            {errors.address && <Grid.Col span={12}><Text size="sm" c="red">{errors.address}</Text></Grid.Col>}
          </Grid>
        ) : (
          <Grid gap="xs">
            <Grid.Col span={6}>
              <TextInput label="Latitude" placeholder="48.137154" inputMode="decimal" value={value.lat}
                error={errors.lat} onPaste={onPasteCoords} onChange={(e) => onChange({ ...value, lat: e.currentTarget.value })} />
            </Grid.Col>
            <Grid.Col span={6}>
              <TextInput label="Longitude" placeholder="11.576124" inputMode="decimal" value={value.lon}
                error={errors.lon} onPaste={onPasteCoords} onChange={(e) => onChange({ ...value, lon: e.currentTarget.value })} />
            </Grid.Col>
            <Grid.Col span={12}>
              <Text size="xs" c="dimmed">Tip: paste “lat, lon” into either field to fill both.</Text>
            </Grid.Col>
          </Grid>
        )}
        {serverError && <Alert color="red" variant="light">{serverError}</Alert>}
      </Stack>
    </Card>
  )
}
