import { ActionIcon, Alert, Button, Grid, Group, Stack, Text, Title, Tooltip } from '@mantine/core'
import { IconArrowsLeftRight, IconRuler } from '@tabler/icons-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { DistanceRequest, DistanceResponse, DistanceSide, DistanceSideError, Place } from '../api/types'
import { DistanceMap } from '../components/distance/DistanceMap'
import { DistanceResult } from '../components/distance/DistanceResult'
import { LocationInput } from '../components/distance/LocationInput'
import {
  emptyForm, formFromParam, type LocationForm, paramFromForm, toLocationInput, type Unit, UNIT_STORAGE_KEY,
  validateForm,
} from '../lib/distance'
import { useCalculateDistance, useCapabilities } from '../lib/hooks'

type PerSide<T> = Record<DistanceSide, T>
const SIDES: DistanceSide[] = ['origin', 'destination']
const PARAM: PerSide<string> = { origin: 'o', destination: 'd' }

function sideErrorsFrom(e: unknown): { sides: Partial<PerSide<string>>; general: string | null } {
  if (!(e instanceof ApiError)) return { sides: {}, general: e instanceof Error ? e.message : String(e) }
  const detail = e.detail
  if (e.status === 422 && detail && typeof detail === 'object' && !Array.isArray(detail) && 'side' in detail) {
    const d = detail as DistanceSideError
    return { sides: { [d.side]: d.message }, general: null }
  }
  if (e.status === 422 && Array.isArray(detail)) {
    // Pydantic errors: loc = ["body", "origin", "<type>", "<field>"]
    const sides: Partial<PerSide<string>> = {}
    for (const err of detail as { loc?: unknown[]; msg?: string }[]) {
      const side = err.loc?.[1]
      if (side === 'origin' || side === 'destination') {
        const field = err.loc?.[3]
        sides[side] = [sides[side], field ? `${String(field)}: ${err.msg}` : err.msg].filter(Boolean).join('; ')
      }
    }
    if (Object.keys(sides).length) return { sides, general: null }
  }
  return { sides: {}, general: e.message }
}

export function DistancePage() {
  const caps = useCapabilities()
  const addressAvailable = caps.data?.distance_address_available ?? false
  const [params, setParams] = useSearchParams()
  const calc = useCalculateDistance()

  const [forms, setForms] = useState<PerSide<LocationForm>>(() => ({
    origin: formFromParam(params.get('o')) ?? emptyForm(),
    destination: formFromParam(params.get('d')) ?? emptyForm(),
  }))
  const [picked, setPicked] = useState<PerSide<Place | null>>({ origin: null, destination: null })
  const [choices, setChoices] = useState<PerSide<Place[]>>({ origin: [], destination: [] })
  const [fieldErrors, setFieldErrors] = useState<PerSide<Record<string, string>>>({ origin: {}, destination: {} })
  const [serverErrors, setServerErrors] = useState<Partial<PerSide<string>>>({})
  const [generalError, setGeneralError] = useState<string | null>(null)
  const [result, setResult] = useState<DistanceResponse | null>(null)
  const [unit, setUnitState] = useState<Unit>(() => (localStorage.getItem(UNIT_STORAGE_KEY) === 'mi' ? 'mi' : 'km'))

  const setUnit = (u: Unit) => {
    localStorage.setItem(UNIT_STORAGE_KEY, u)
    setUnitState(u)
  }

  // Without a Google key, address mode is unavailable; fall back to coordinates.
  const effectiveForms: PerSide<LocationForm> = {
    origin: !addressAvailable && caps.data ? { ...forms.origin, mode: 'coords' } : forms.origin,
    destination: !addressAvailable && caps.data ? { ...forms.destination, mode: 'coords' } : forms.destination,
  }

  const updateForm = (side: DistanceSide, next: LocationForm) => {
    setForms((f) => ({ ...f, [side]: next }))
    setPicked((p) => ({ ...p, [side]: null }))
    setChoices((c) => ({ ...c, [side]: [] }))
    setFieldErrors((e) => ({ ...e, [side]: {} }))
    setServerErrors((e) => ({ ...e, [side]: undefined }))
  }

  const run = (nextPicked: PerSide<Place | null>) => {
    const errs: PerSide<Record<string, string>> = {
      origin: nextPicked.origin ? {} : validateForm(effectiveForms.origin),
      destination: nextPicked.destination ? {} : validateForm(effectiveForms.destination),
    }
    setFieldErrors(errs)
    if (SIDES.some((s) => Object.keys(errs[s]).length)) return
    setServerErrors({})
    setGeneralError(null)
    const req: DistanceRequest = {
      origin: toLocationInput(effectiveForms.origin, nextPicked.origin),
      destination: toLocationInput(effectiveForms.destination, nextPicked.destination),
      road: caps.data?.road_distance_available ?? false,
    }
    calc.mutate(req, {
      onSuccess: (res) => {
        setResult(res)
        setPicked(nextPicked)
        // Keep the original Google matches while the user switches between them.
        setChoices((prev) => {
          const next = { ...prev }
          for (const s of SIDES) {
            if (!nextPicked[s]) next[s] = res[s].source === 'google' ? [res[s], ...res[s].alternatives] : []
          }
          return next
        })
        const nextParams = new URLSearchParams()
        for (const s of SIDES) {
          const v = paramFromForm(effectiveForms[s], nextPicked[s])
          if (v) nextParams.set(PARAM[s], v)
        }
        setParams(nextParams, { replace: true })
      },
      onError: (e) => {
        const { sides, general } = sideErrorsFrom(e)
        setServerErrors(sides)
        setGeneralError(general)
      },
    })
  }

  const swap = () => {
    setForms((f) => ({ origin: f.destination, destination: f.origin }))
    setPicked((p) => ({ origin: p.destination, destination: p.origin }))
    setChoices((c) => ({ origin: c.destination, destination: c.origin }))
    setFieldErrors((e) => ({ origin: e.destination, destination: e.origin }))
    setServerErrors({})
    setResult(null)
  }

  const showGoogleOnMap = caps.data?.distance_map === 'leaflet'
  const usesGoogle = !!result && SIDES.some((s) => result[s].source === 'google')

  return (
    <Stack maw={1300} mx="auto">
      <div>
        <Title order={2}>Distance</Title>
        <Text c="dimmed" size="sm">
          Distance between two places, given as a postal address (any combination of fields) or as coordinates.
        </Text>
      </div>
      <Grid align="center">
        <Grid.Col span={{ base: 12, md: 'auto' }}>
          <LocationInput title="Origin" value={effectiveForms.origin} onChange={(f) => updateForm('origin', f)}
            errors={fieldErrors.origin} serverError={serverErrors.origin} addressAvailable={addressAvailable} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 'content' }}>
          <Group justify="center">
            <Tooltip label="Swap origin and destination">
              <ActionIcon variant="default" size="lg" onClick={swap} aria-label="Swap origin and destination">
                <IconArrowsLeftRight size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 'auto' }}>
          <LocationInput title="Destination" value={effectiveForms.destination}
            onChange={(f) => updateForm('destination', f)} errors={fieldErrors.destination}
            serverError={serverErrors.destination} addressAvailable={addressAvailable} />
        </Grid.Col>
      </Grid>
      <Group>
        <Button leftSection={<IconRuler size={16} />} loading={calc.isPending} onClick={() => run(picked)}>
          Calculate distance
        </Button>
        {caps.data && !caps.data.road_distance_available && (
          <Text size="sm" c="dimmed">Road distance is not configured. Only the straight-line distance is shown.</Text>
        )}
      </Group>
      {generalError && <Alert color="red">{generalError}</Alert>}
      {result && (
        <Grid>
          <Grid.Col span={{ base: 12, lg: 5 }}>
            <DistanceResult result={result} unit={unit} onUnit={setUnit} choices={choices} picking={calc.isPending}
              onPick={(side, place) => run({ ...picked, [side]: place })} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 7 }}>
            {usesGoogle && !showGoogleOnMap ? (
              <Alert color="gray" variant="light">The map is not shown for locations resolved by Google.</Alert>
            ) : (
              <DistanceMap result={result} showGoogle={showGoogleOnMap} />
            )}
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  )
}
