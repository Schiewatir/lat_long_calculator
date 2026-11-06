import { Alert, Button, Card, Group, Progress, SimpleGrid, Stack, Stepper, Text } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Job, ProgressEvent } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { ACTIVE_STATUSES, fmtDuration, fmtNumber, STAGES } from '../lib/format'
import { notifyError, notifyOk } from '../lib/hooks'
import { BandBar, BandCounts } from './Badges'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={600} size="lg">{value}</Text>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </div>
  )
}

export function ProgressPanel({ job, event }: { job: Job; event: ProgressEvent | null }) {
  const { me } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const status = event?.status ?? job.status
  const counts = { ...job.counts, ...(event?.counts ?? {}) }
  const bands = event?.bands ?? job.summary?.bands ?? {}
  const running = ACTIVE_STATUSES.includes(status)
  const stageIndex = Math.max(0, STAGES.findIndex((s) => s.key === (status === 'scoring' ? 'geocoding' : status)))
  const pct = event?.stage_total ? Math.min(100, ((event.stage_done ?? 0) / event.stage_total) * 100) : undefined

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['job', job.id] })
    void qc.invalidateQueries({ queryKey: ['jobs'] })
  }
  const action = useMutation({
    mutationFn: (path: string) => api(`/jobs/${job.id}/${path}`, { method: 'POST' }),
    onSuccess: (_d, path) => {
      notifyOk({ cancel: 'Cancellation requested', resume: 'Job resumed', 'retry-errors': 'Retrying rows with errors', rescore: 'Job re-scored with the active configuration' }[path] ?? 'Done')
      refresh()
      void qc.invalidateQueries({ queryKey: ['rows', job.id] })
    },
    onError: (e) => notifyError(e),
  })
  const remove = useMutation({
    mutationFn: () => api(`/jobs/${job.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      notifyOk('Job deleted')
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      navigate('/')
    },
    onError: (e) => notifyError(e),
  })

  return (
    <Stack>
      {status === 'failed' && <Alert color="red" title="Processing failed">{job.message ?? event?.message}</Alert>}
      {status === 'paused' && (
        <Alert color="yellow" title="Paused">{event?.message ?? job.message} — resume when the quota is available again.</Alert>
      )}
      {(event?.message ?? job.message) && status === 'ai_repair' && <Alert color="violet">{event?.message ?? job.message}</Alert>}
      <Card withBorder>
        <Stepper active={status === 'ready' ? STAGES.length : stageIndex} size="sm">
          {STAGES.map((s) => <Stepper.Step key={s.key} label={s.label} loading={running && s.key === STAGES[stageIndex].key} />)}
        </Stepper>
        {running && (
          <Stack gap={4} mt="lg">
            <Progress value={pct ?? 100} animated striped={pct === undefined} />
            <Text size="sm" c="dimmed">
              {event?.stage_total ? `${fmtNumber(event.stage_done)} of ${fmtNumber(event.stage_total)} unique addresses` : 'Working…'}
              {event?.eta_seconds ? ` · about ${fmtDuration(event.eta_seconds)} left` : ''}
            </Text>
          </Stack>
        )}
      </Card>
      <Card withBorder>
        <Stack>
          <Text fw={600}>Confidence so far</Text>
          <BandBar bands={bands} h={16} />
          <BandCounts bands={bands} />
        </Stack>
      </Card>
      <Card withBorder>
        <SimpleGrid cols={{ base: 2, sm: 4, lg: 8 }}>
          <Stat label="Rows" value={fmtNumber(counts.total_rows)} hint={counts.empty_rows_skipped ? `${counts.empty_rows_skipped} empty skipped` : undefined} />
          <Stat label="Unique addresses" value={fmtNumber(counts.unique_keys)} />
          <Stat label="Normalized" value={fmtNumber(counts.normalized)} />
          <Stat label="Sent to AI" value={fmtNumber(counts.ai_sent)} />
          <Stat label="AI repaired" value={fmtNumber(counts.ai_repaired)} hint={counts.ai_rejected ? `${counts.ai_rejected} rejected by validation` : undefined} />
          <Stat label="Geocoded" value={fmtNumber(counts.geocoded)} />
          <Stat label="Geocoder calls" value={fmtNumber(counts.provider_calls)} hint={counts.cache_hits ? `${fmtNumber(counts.cache_hits)} from cache` : undefined} />
          <Stat label="Errors" value={fmtNumber(counts.errors)} />
        </SimpleGrid>
      </Card>
      <Group>
        {(running || status === 'paused') && (
          <Button color="red" variant="light" loading={action.isPending} onClick={() => action.mutate('cancel')}>Cancel job</Button>
        )}
        {status === 'paused' && <Button onClick={() => action.mutate('resume')}>Resume</Button>}
        {(status === 'ready' || status === 'cancelled') && (counts.errors ?? 0) > 0 && (
          <Button variant="light" onClick={() => action.mutate('retry-errors')}>Retry rows with errors</Button>
        )}
        {me?.role === 'admin' && status === 'ready' && (
          <Button variant="default" onClick={() => action.mutate('rescore')}>Re-score with active config</Button>
        )}
        {!running && (
          <Button color="red" variant="subtle" onClick={() => modals.openConfirmModal({
            title: 'Delete job?', children: <Text size="sm">The uploaded file, all rows and results are removed. This cannot be undone.</Text>,
            labels: { confirm: 'Delete', cancel: 'Cancel' }, confirmProps: { color: 'red' }, onConfirm: () => remove.mutate(),
          })}>Delete job</Button>
        )}
      </Group>
    </Stack>
  )
}
