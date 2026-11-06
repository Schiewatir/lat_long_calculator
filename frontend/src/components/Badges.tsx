import { Badge, Group, Progress, Tooltip } from '@mantine/core'
import type { Band, JobStatus, JobSummary, ReviewStatus } from '../api/types'
import { BAND_COLOR, BANDS, JOB_STATUS_COLOR, STATUS_COLOR, statusLabel } from '../lib/format'

export function BandBadge({ band, score }: { band: Band | null; score?: number | null }) {
  if (!band) return <Badge color="gray" variant="light">–</Badge>
  return (
    <Badge color={BAND_COLOR[band]} variant="light" miw={78}>
      {score != null ? `${score} · ${band}` : band}
    </Badge>
  )
}

export function StatusBadge({ status }: { status: ReviewStatus }) {
  return (
    <Badge color={STATUS_COLOR[status]} variant={status === 'pending' ? 'outline' : 'light'} size="sm">
      {status}
    </Badge>
  )
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge color={JOB_STATUS_COLOR[status]} variant="light">
      {statusLabel(status)}
    </Badge>
  )
}

/** Stacked bar of band distribution. */
export function BandBar({ bands, h = 10 }: { bands: JobSummary['bands']; h?: number }) {
  const total = Object.values(bands).reduce((a, b) => a + (b ?? 0), 0)
  if (!total) return <Progress value={0} h={h} />
  return (
    <Progress.Root h={h}>
      {BANDS.map((b) =>
        bands[b] ? (
          <Tooltip key={b} label={`${b}: ${bands[b]!.toLocaleString()}`}>
            <Progress.Section value={(bands[b]! / total) * 100} color={BAND_COLOR[b]} />
          </Tooltip>
        ) : null,
      )}
    </Progress.Root>
  )
}

export function BandCounts({ bands }: { bands: JobSummary['bands'] }) {
  return (
    <Group gap={6}>
      {BANDS.map((b) => (
        <Badge key={b} color={BAND_COLOR[b]} variant="dot">
          {b}: {(bands[b] ?? 0).toLocaleString()}
        </Badge>
      ))}
    </Group>
  )
}
