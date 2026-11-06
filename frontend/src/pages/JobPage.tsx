import { Alert, Anchor, Badge, Breadcrumbs, Group, Loader, Stack, Tabs, Text, Title } from '@mantine/core'
import { IconChartBar, IconDownload, IconMap, IconTable } from '@tabler/icons-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { RowFilter } from '../api/types'
import { JobStatusBadge } from '../components/Badges'
import { ExportPanel } from '../components/ExportPanel'
import { MapPanel } from '../components/MapPanel'
import { ProgressPanel } from '../components/ProgressPanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { RowDetailDrawer } from '../components/RowDetailDrawer'
import { ACTIVE_STATUSES, fmtNumber } from '../lib/format'
import { useJob, useJobEvents } from '../lib/jobs'

export function JobPage() {
  const { jobId = '' } = useParams()
  const job = useJob(jobId)
  const event = useJobEvents(job.data)
  const [tab, setTab] = useState<string | null>(null)
  const [filter, setFilter] = useState<RowFilter>({})
  const [detailRow, setDetailRow] = useState<number | null>(null)

  if (job.isLoading) return <Loader />
  if (job.error || !job.data) return <Alert color="red">Job not found.</Alert>
  const j = job.data
  const status = event?.status ?? j.status
  const running = ACTIVE_STATUSES.includes(status)
  const activeTab = tab ?? (running || status === 'failed' || status === 'paused' ? 'progress' : 'review')

  return (
    <Stack maw={1600} mx="auto" gap="sm">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Jobs</Anchor>
        <Text>{j.name}</Text>
      </Breadcrumbs>
      <Group justify="space-between">
        <Group gap="sm">
          <Title order={2}>{j.name}</Title>
          <JobStatusBadge status={status} />
          {j.options.ai_repair ? <Badge variant="light" color="violet">AI repair on</Badge> : <Badge variant="light" color="gray">AI repair off</Badge>}
          {j.options.default_country && <Badge variant="light">default country {j.options.default_country}</Badge>}
        </Group>
        <Text c="dimmed" size="sm">
          {j.file_name} · {fmtNumber(j.counts.total_rows)} rows · {fmtNumber(j.counts.unique_keys)} unique addresses
          {j.owner ? ` · ${j.owner}` : ''}
        </Text>
      </Group>
      <Tabs value={activeTab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="progress" leftSection={<IconChartBar size={16} />}>Progress</Tabs.Tab>
          <Tabs.Tab value="review" leftSection={<IconTable size={16} />}>Review</Tabs.Tab>
          <Tabs.Tab value="map" leftSection={<IconMap size={16} />}>Map</Tabs.Tab>
          <Tabs.Tab value="export" leftSection={<IconDownload size={16} />}>Export</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="progress" pt="md">
          <ProgressPanel job={j} event={event} />
        </Tabs.Panel>
        <Tabs.Panel value="review" pt="md">
          <ReviewPanel job={j} filter={filter} setFilter={setFilter} onOpen={setDetailRow} openRow={detailRow} />
        </Tabs.Panel>
        <Tabs.Panel value="map" pt="md">
          <MapPanel jobId={j.id} filter={filter} setFilter={setFilter} onOpen={setDetailRow} />
        </Tabs.Panel>
        <Tabs.Panel value="export" pt="md">
          <ExportPanel job={j} filter={filter} />
        </Tabs.Panel>
      </Tabs>
      <RowDetailDrawer job={j} rowId={detailRow} onClose={() => setDetailRow(null)} />
    </Stack>
  )
}
