import { Alert, Badge, Button, Card, CopyButton, Group, Menu, SegmentedControl, SimpleGrid, Stack, Text } from '@mantine/core'
import { IconAlertTriangle, IconCheck, IconChevronDown, IconCopy, IconRoute, IconRuler2 } from '@tabler/icons-react'
import type { DistanceResponse, DistanceSide, Place, ResolvedLocation } from '../../api/types'
import { fmtCoord } from '../../lib/format'
import {
  fmtDistance, fmtDriveTime, PRECISION_LABEL, ROAD_UNAVAILABLE_TEXT, summaryText, type Unit,
} from '../../lib/distance'

interface Props {
  result: DistanceResponse
  unit: Unit
  onUnit: (u: Unit) => void
  choices: Record<DistanceSide, Place[]>
  onPick: (side: DistanceSide, place: Place) => void
  picking: boolean
}

const samePlace = (a: Place, b: Place) => (a.place_id && a.place_id === b.place_id) || (a.lat === b.lat && a.lon === b.lon)

function SideCard({ title, loc, choices, onPick, picking }: {
  title: string; loc: ResolvedLocation; choices: Place[]; onPick: (p: Place) => void; picking: boolean
}) {
  const others = choices.filter((c) => !samePlace(c, loc))
  return (
    <Card withBorder padding="sm">
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{title}</Text>
          <Group gap={4}>
            <Badge size="sm" variant="outline">{PRECISION_LABEL[loc.precision]}</Badge>
            <Badge size="sm" variant="outline" color="gray">{loc.source === 'google' ? 'Google' : 'Input'}</Badge>
          </Group>
        </Group>
        <Text fw={500}>{loc.label}</Text>
        <Text size="sm" className="mono">{fmtCoord(loc.lat)}, {fmtCoord(loc.lon)}</Text>
        {loc.partial_match && (
          <Alert color="yellow" variant="light" p={6} icon={<IconAlertTriangle size={14} />}>
            Partial match. Check the resolved address.
          </Alert>
        )}
        {others.length > 0 && (
          <Menu position="bottom-start" withinPortal>
            <Menu.Target>
              <Button size="compact-xs" variant="light" rightSection={<IconChevronDown size={12} />} loading={picking} w="fit-content">
                {others.length} other match{others.length > 1 ? 'es' : ''}
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {others.map((p) => (
                <Menu.Item key={`${p.place_id}:${p.lat}:${p.lon}`} onClick={() => onPick(p)}>
                  <Text size="sm">{p.label}</Text>
                  <Text size="xs" c="dimmed">{PRECISION_LABEL[p.precision]} · {fmtCoord(p.lat)}, {fmtCoord(p.lon)}</Text>
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        )}
      </Stack>
    </Card>
  )
}

export function DistanceResult({ result, unit, onUnit, choices, onPick, picking }: Props) {
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text fw={600}>Result</Text>
        <Group gap="xs">
          <SegmentedControl size="xs" value={unit} onChange={(v) => onUnit(v as Unit)} data={['km', 'mi']} />
          <CopyButton value={summaryText(result, unit)}>
            {({ copied, copy }) => (
              <Button size="xs" variant="default" onClick={copy}
                leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
            )}
          </CopyButton>
        </Group>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Card withBorder padding="md">
          <Group gap="xs"><IconRuler2 size={18} /><Text size="sm" c="dimmed">Straight line</Text></Group>
          <Text size="xl" fw={700}>{fmtDistance(result.straight_line, unit)}</Text>
          <Text size="xs" c="dimmed">Great-circle distance</Text>
        </Card>
        <Card withBorder padding="md">
          <Group gap="xs"><IconRoute size={18} /><Text size="sm" c="dimmed">By road</Text></Group>
          {result.road ? (
            <>
              <Text size="xl" fw={700}>{fmtDistance(result.road, unit)}</Text>
              <Text size="xs" c="dimmed">{fmtDriveTime(result.road.duration_s)} driving</Text>
            </>
          ) : (
            <Text size="sm" mt={6}>{ROAD_UNAVAILABLE_TEXT[result.road_distance_unavailable ?? 'disabled']}</Text>
          )}
        </Card>
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        {(['origin', 'destination'] as const).map((side) => (
          <SideCard key={side} title={side === 'origin' ? 'Origin' : 'Destination'} loc={result[side]}
            choices={choices[side]} onPick={(p) => onPick(side, p)} picking={picking} />
        ))}
      </SimpleGrid>
      {result.attribution.length > 0 && <Text size="xs" c="dimmed">{result.attribution.join(' · ')}</Text>}
    </Stack>
  )
}
