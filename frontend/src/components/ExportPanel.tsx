import { Alert, Button, Card, List, Radio, Stack, Text, Title } from '@mantine/core'
import { IconDownload } from '@tabler/icons-react'
import { useState } from 'react'
import type { Job, RowFilter } from '../api/types'
import { fmtNumber } from '../lib/format'
import { useCapabilities } from '../lib/hooks'

type Scope = 'confirmed' | 'all' | 'current_filter'

export function ExportPanel({ job, filter }: { job: Job; filter: RowFilter }) {
  const caps = useCapabilities()
  const [scope, setScope] = useState<Scope>('confirmed')
  const review = job.summary?.review ?? {}
  const confirmed = (review.confirmed ?? 0) + (review.edited ?? 0)
  const params = new URLSearchParams({ scope })
  if (scope === 'current_filter') params.set('filter', JSON.stringify(filter))
  const href = `/api/jobs/${job.id}/export?${params}`

  return (
    <Card withBorder maw={720}>
      <Stack>
        <Title order={4}>Export CSV</Title>
        <Radio.Group value={scope} onChange={(v) => setScope(v as Scope)}>
          <Stack gap="xs">
            <Radio value="confirmed" label={`Confirmed rows only (${fmtNumber(confirmed)} incl. edited)`} />
            <Radio value="all" label={`All rows with their review status (${fmtNumber(job.counts.total_rows)})`} />
            <Radio value="current_filter" label="Rows matching the current review filter" disabled={!Object.keys(filter).length} />
          </Stack>
        </Radio.Group>
        <Text size="sm" c="dimmed">
          UTF-8 with BOM (opens correctly in Excel), original columns first, then lat, lon, confidence, confidence_band,
          precision_level, geocode_source, matched address fields, corrections, status, reviewed_by and reviewed_at.
          Postcodes are written as text so leading zeros are kept.
        </Text>
        {scope === 'confirmed' && confirmed === 0 && <Alert color="yellow">No rows are confirmed yet.</Alert>}
        <Button component="a" href={href} leftSection={<IconDownload size={16} />} w="fit-content">Download CSV</Button>
        <div>
          <Text size="xs" fw={600}>Data sources</Text>
          <List size="xs" c="dimmed">
            {caps.data?.attribution.map((a) => <List.Item key={a}>{a}</List.Item>)}
          </List>
        </div>
      </Stack>
    </Card>
  )
}
