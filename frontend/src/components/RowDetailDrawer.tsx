import {
  Accordion, Alert, Badge, Button, Divider, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, TextInput, Timeline,
  Tooltip,
} from '@mantine/core'
import { IconCheck, IconMapPin, IconRefresh, IconRotate, IconSparkles, IconX } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Marker, TileLayer, Tooltip as LTooltip, useMap } from 'react-leaflet'
import { api } from '../api/client'
import type { Job, RowDetail } from '../api/types'
import { BAND_COLOR, fmtCoord, fmtDate } from '../lib/format'
import { pinIcon, TILE_ATTRIBUTION, TILE_URL } from '../lib/leaflet'
import { useRowAction } from '../lib/rows'
import { BandBadge, StatusBadge } from './Badges'

const FIELDS: [string, string, string][] = [
  ['Street', 'in_street', 'street'], ['House no.', 'in_house_number', 'house_number'], ['City', 'in_city', 'city'],
  ['Postcode', 'in_postcode', 'postcode'], ['Country', 'in_country', 'country'],
]

function FitTo({ points }: { points: [number, number][] }) {
  const map = useMap()
  const key = JSON.stringify(points)
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 13)
    else if (points.length > 1) map.fitBounds(points, { padding: [30, 30], maxZoom: 13 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return null
}

export function RowDetailDrawer({ job, rowId, onClose }: { job: Job; rowId: number | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ['row', job.id, rowId],
    queryFn: () => api<RowDetail>(`/jobs/${job.id}/rows/${rowId}`),
    enabled: rowId !== null,
  })
  const d = detail.data
  return (
    <Drawer opened={rowId !== null} onClose={onClose} position="right" size="xl" title={d ? `Row ${d.row.row_index}` : 'Row'}>
      {!d && <Loader />}
      {d && rowId !== null && <RowDetailBody key={`${d.row.id}:${d.row.version}`} job={job} rowId={rowId} d={d} />}
    </Drawer>
  )
}

function RowDetailBody({ job, rowId, d }: { job: Job; rowId: number; d: RowDetail }) {
  const action = useRowAction(job.id)
  const [pin, setPin] = useState<[number, number] | null>(null)
  const [edit, setEdit] = useState<Record<string, string>>(() => ({
    street: d.row.in_street, house_number: d.row.in_house_number, city: d.row.in_city, postcode: d.row.in_postcode,
    country: d.row.in_country,
  }))

  const busy = action.isPending
  const run = (body: Parameters<typeof action.mutate>[0]['body']) => action.mutate({ rowId, body })
  const points = useMemo(() => {
    const pts: [number, number][] = d.candidates.map((c) => [c.lat, c.lon])
    if (d.row.lat != null && d.row.lon != null) pts.push([d.row.lat, d.row.lon])
    return pts
  }, [d])

  return (
        <Stack>
          <Group justify="space-between">
            <Group gap="xs">
              <BandBadge band={d.row.band} score={d.row.score} />
              <StatusBadge status={d.row.status} />
              {d.row.precision && <Badge variant="outline">{d.row.precision}</Badge>}
              {d.row.source && <Badge variant="outline" color="gray">{d.row.source}</Badge>}
            </Group>
            <Group gap="xs">
              <Button size="xs" color="teal" leftSection={<IconCheck size={14} />} loading={busy}
                onClick={() => run({ action: 'confirm', version: d.row.version })}>Confirm</Button>
              <Button size="xs" color="red" variant="light" leftSection={<IconX size={14} />} loading={busy}
                onClick={() => run({ action: 'reject', version: d.row.version })}>Reject</Button>
              {d.row.status !== 'pending' && (
                <Button size="xs" variant="default" leftSection={<IconRotate size={14} />} loading={busy}
                  onClick={() => run({ action: 'reset', version: d.row.version })}>Reset</Button>
              )}
            </Group>
          </Group>

          <div style={{ height: 300 }}>
            <MapContainer center={[50, 10]} zoom={4} style={{ height: '100%' }} scrollWheelZoom>
              <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
              <FitTo points={points} />
              {d.candidates.filter((c) => !c.selected).map((c) => (
                <CircleMarker key={c.id} center={[c.lat, c.lon]} radius={8}
                  pathOptions={{ color: 'white', weight: 2, fillColor: `var(--mantine-color-${c.band ? BAND_COLOR[c.band] : 'gray'}-6)`, fillOpacity: 0.9 }}
                  eventHandlers={{ click: () => run({ action: 'pick_candidate', version: d.row.version, candidate_id: c.id }) }}>
                  <LTooltip>Alternative: {c.label} ({c.precision}, {c.score}) — click to use</LTooltip>
                </CircleMarker>
              ))}
              {d.row.lat != null && d.row.lon != null && (
                <Marker position={pin ?? [d.row.lat, d.row.lon]} draggable icon={pinIcon('var(--mantine-primary-color-filled)', 26)}
                  eventHandlers={{ dragend: (e) => { const ll = e.target.getLatLng(); setPin([ll.lat, ll.lng]) } }}>
                  <LTooltip>Current result — drag to place manually</LTooltip>
                </Marker>
              )}
            </MapContainer>
          </div>
          {d.row.lat == null && (
            <Alert color="orange" icon={<IconMapPin size={16} />}>
              No location found. Fix the input below and re-run, or place a pin:
              <Button size="xs" ml="sm" variant="light" onClick={() => setPin(points[0] ?? [50, 10])}>Place pin on map</Button>
            </Alert>
          )}
          {pin && (
            <Group>
              <Text size="sm">New position <span className="mono">{fmtCoord(pin[0])}, {fmtCoord(pin[1])}</span></Text>
              <Button size="xs" loading={busy} onClick={() => run({ action: 'manual_pin', version: d.row.version, lat: pin[0], lon: pin[1] })}>Save pin</Button>
              <Button size="xs" variant="subtle" onClick={() => setPin(null)}>Discard</Button>
            </Group>
          )}
          {d.row.lat == null && pin && (
            <MapContainer center={pin} zoom={5} style={{ height: 220 }}>
              <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
              <Marker position={pin} draggable icon={pinIcon('#fa5252', 26)}
                eventHandlers={{ dragend: (e) => { const ll = e.target.getLatLng(); setPin([ll.lat, ll.lng]) } }} />
            </MapContainer>
          )}

          <SimpleGrid cols={2}>
            <div>
              <Text fw={600} size="sm" mb={4}>Why this confidence</Text>
              <Stack gap={4}>
                {d.reasons.map((r, i) => (
                  <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
                    <Badge size="sm" variant="light" color={r.penalty ? 'orange' : 'teal'} miw={44} style={{ flexShrink: 0 }}>{r.penalty ? `−${r.penalty}` : '✓'}</Badge>
                    <Text size="sm">{r.text}</Text>
                  </Group>
                ))}
              </Stack>
            </div>
            <div>
              <Text fw={600} size="sm" mb={4}>Corrections applied</Text>
              {d.corrections.length === 0 && <Text size="sm" c="dimmed">None</Text>}
              <Stack gap={4}>
                {d.corrections.map((c, i) => (
                  <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
                    {c.tier === 'ai'
                      ? <Badge size="sm" color="violet" leftSection={<IconSparkles size={10} />} style={{ flexShrink: 0 }}>AI</Badge>
                      : <Badge size="sm" variant="outline" style={{ flexShrink: 0 }}>{c.tier === 'job_default' ? 'default' : 'rule'}</Badge>}
                    <Text size="sm">{c.sentence}</Text>
                  </Group>
                ))}
              </Stack>
              {d.ai_reason && <Text size="xs" c="dimmed" mt={4}>AI: {d.ai_reason}</Text>}
            </div>
          </SimpleGrid>

          <Table withTableBorder fz="sm">
            <Table.Thead>
              <Table.Tr><Table.Th /><Table.Th>Original</Table.Th><Table.Th>Normalized</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {FIELDS.map(([label, inKey, nKey]) => {
                const orig = (d.row as unknown as Record<string, string>)[inKey]
                const norm = d.normalized[nKey] as string | null
                const changed = (norm ?? '') !== '' && String(norm) !== orig
                return (
                  <Table.Tr key={label}>
                    <Table.Td c="dimmed">{label}</Table.Td>
                    <Table.Td>{orig || '—'}</Table.Td>
                    <Table.Td fw={changed ? 600 : undefined} c={changed ? 'blue' : undefined}>{norm || '—'}</Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>

          <div>
            <Text fw={600} size="sm" mb={4}>Candidates</Text>
            <Table fz="sm" highlightOnHover>
              <Table.Tbody>
                {d.candidates.map((c) => (
                  <Table.Tr key={c.id}>
                    <Table.Td>{c.label || '—'}</Table.Td>
                    <Table.Td><Badge variant="outline" size="sm">{c.precision}</Badge></Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{c.source}</Text></Table.Td>
                    <Table.Td><BandBadge band={c.band} score={c.score} /></Table.Td>
                    <Table.Td ta="right">
                      {c.selected ? <Badge size="sm">in use</Badge> : (
                        <Tooltip label={c.reasons.map((r) => (r.penalty ? `−${r.penalty} ${r.text}` : r.text)).join('\n')} multiline w={320}>
                          <Button size="compact-xs" variant="light" loading={busy}
                            onClick={() => run({ action: 'pick_candidate', version: d.row.version, candidate_id: c.id })}>Use this</Button>
                        </Tooltip>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {d.candidates.length === 0 && <Text size="sm" c="dimmed">No candidates were found.</Text>}
          </div>

          <Accordion variant="contained">
            <Accordion.Item value="edit">
              <Accordion.Control icon={<IconRefresh size={16} />}>Edit input and re-run</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={2}>
                  {FIELDS.map(([label, , key]) => (
                    <TextInput key={key} size="xs" label={label} value={edit[key] ?? ''} onChange={(e) => setEdit({ ...edit, [key]: e.currentTarget.value })} />
                  ))}
                </SimpleGrid>
                <Button mt="sm" size="xs" loading={busy} disabled={job.status !== 'ready' && job.status !== 'cancelled' && job.status !== 'paused'}
                  onClick={() => run({ action: 'rerun', version: d.row.version, input: edit })}>Re-run this row</Button>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="raw">
              <Accordion.Control>Original file columns</Accordion.Control>
              <Accordion.Panel>
                <Table fz="xs">
                  <Table.Tbody>
                    {d.columns.map((c, i) => (
                      <Table.Tr key={c}><Table.Td c="dimmed">{c}</Table.Td><Table.Td>{d.raw[i]}</Table.Td></Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Divider label="History" labelPosition="left" />
          {d.history.length === 0 ? <Text size="sm" c="dimmed">No review actions yet.</Text> : (
            <Timeline bulletSize={14} lineWidth={2}>
              {d.history.map((h, i) => (
                <Timeline.Item key={i} title={<Text size="sm">{h.action.replace('row_', '').replace('_', ' ')} by {h.user ?? 'system'}</Text>}>
                  <Text size="xs" c="dimmed">{fmtDate(h.at)}</Text>
                </Timeline.Item>
              ))}
            </Timeline>
          )}
        </Stack>
  )
}
