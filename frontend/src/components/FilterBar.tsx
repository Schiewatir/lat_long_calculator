import { Button, Chip, Group, MultiSelect, RangeSlider, Stack, Switch, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { IconSearch } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import type { Band, ReviewStatus, RowFilter } from '../api/types'
import { BAND_COLOR, BANDS, CORRECTION_TYPES, PRECISIONS, STATUSES } from '../lib/format'

export function FilterBar({ filter, setFilter, total }: { filter: RowFilter; setFilter: (f: RowFilter) => void; total?: number | null }) {
  const [q, setQ] = useState(filter.q ?? '')
  const [debounced] = useDebouncedValue(q, 350)
  useEffect(() => {
    if ((filter.q ?? '') !== debounced) setFilter({ ...filter, q: debounced || undefined })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])
  const [range, setRange] = useState<[number, number]>([filter.score_min ?? 0, filter.score_max ?? 100])
  useEffect(() => setRange([filter.score_min ?? 0, filter.score_max ?? 100]), [filter.score_min, filter.score_max])
  const set = (patch: Partial<RowFilter>) => {
    const next = { ...filter, ...patch }
    for (const k of Object.keys(next) as (keyof RowFilter)[]) {
      const v = next[k]
      if (v === undefined || v === false || (Array.isArray(v) && v.length === 0)) delete next[k]
    }
    setFilter(next)
  }

  return (
    <Stack gap="xs">
      <Group gap="md" align="flex-end" wrap="wrap">
        <TextInput leftSection={<IconSearch size={14} />} placeholder="Search city, postcode, street…" value={q}
          onChange={(e) => setQ(e.currentTarget.value)} w={240} size="xs" />
        <Chip.Group multiple value={filter.bands ?? []} onChange={(v) => set({ bands: v as Band[] })}>
          <Group gap={4}>
            {BANDS.map((b) => (
              <Chip key={b} value={b} color={BAND_COLOR[b]} size="xs" variant="light">{b}</Chip>
            ))}
          </Group>
        </Chip.Group>
        <MultiSelect size="xs" w={190} placeholder="Review status" data={STATUSES} value={filter.statuses ?? []}
          onChange={(v) => set({ statuses: v as ReviewStatus[] })} clearable />
        <MultiSelect size="xs" w={210} placeholder="Corrections" data={CORRECTION_TYPES} value={filter.corrections ?? []}
          onChange={(v) => set({ corrections: v })} clearable />
        <MultiSelect size="xs" w={170} placeholder="Precision" data={PRECISIONS} value={filter.precisions ?? []}
          onChange={(v) => set({ precisions: v })} clearable />
        <Stack gap={0} w={170}>
          <Text size="xs" c="dimmed">Confidence {range[0]}–{range[1]}</Text>
          <RangeSlider size="xs" min={0} max={100} step={1} minRange={0} value={range} onChange={setRange} label={null}
            onChangeEnd={([a, b]) => set({ score_min: a > 0 ? a : undefined, score_max: b < 100 ? b : undefined })} />
        </Stack>
        <Switch size="xs" label="Errors only" checked={!!filter.errors_only} onChange={(e) => set({ errors_only: e.currentTarget.checked })} />
      </Group>
      <Group gap="xs">
        <Text size="xs" c="dimmed">Quick filters:</Text>
        <Button size="compact-xs" variant="light" onClick={() => setFilter({ bands: ['Low', 'Failed'], statuses: ['pending'] })}>Needs attention</Button>
        <Button size="compact-xs" variant="light" onClick={() => setFilter({ score_min: 90, statuses: ['pending'] })}>High & pending</Button>
        <Button size="compact-xs" variant="light" onClick={() => setFilter({ corrections: ['ai_repaired', 'ai_disambiguated'] })}>AI touched</Button>
        <Button size="compact-xs" variant="subtle" onClick={() => { setQ(''); setFilter({}) }}>Clear</Button>
        {total != null && <Text size="xs" fw={600}>{total.toLocaleString()} matching rows</Text>}
      </Group>
    </Stack>
  )
}
