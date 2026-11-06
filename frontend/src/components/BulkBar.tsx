import { Button, Group, Text } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { RowFilter } from '../api/types'
import { notifyError, notifyOk } from '../lib/hooks'

type BulkKind = 'confirm' | 'reject' | 'reset'
const LABEL: Record<BulkKind, string> = { confirm: 'Confirm', reject: 'Reject', reset: 'Reset to pending' }
const ELIGIBLE: Record<BulkKind, string> = {
  confirm: 'pending rows', reject: 'rows that are not already rejected', reset: 'reviewed rows',
}
const CONFIRM_THRESHOLD = 100

export function BulkBar({ jobId, filter, selected }: { jobId: string; filter: RowFilter; selected: number[] }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<BulkKind | null>(null)

  async function run(action: BulkKind, f: RowFilter) {
    setBusy(action)
    try {
      const { count } = await api<{ count: number }>(`/jobs/${jobId}/bulk/preview`, { json: { action, filter: f } })
      if (count === 0) {
        notifyOk(`No ${ELIGIBLE[action]} match.`)
        return
      }
      const apply = async () => {
        try {
          const { updated } = await api<{ updated: number }>(`/jobs/${jobId}/bulk`, { json: { action, filter: f, expected_count: count } })
          notifyOk(`${updated.toLocaleString()} rows updated`)
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) notifyError(e, 'Matching rows changed')
          else notifyError(e)
        } finally {
          void qc.invalidateQueries({ queryKey: ['rows', jobId] })
          void qc.invalidateQueries({ queryKey: ['job', jobId] })
        }
      }
      if (count > CONFIRM_THRESHOLD) {
        modals.openConfirmModal({
          title: `${LABEL[action]} ${count.toLocaleString()} rows?`,
          children: <Text size="sm">This applies to exactly <b>{count.toLocaleString()}</b> {ELIGIBLE[action]} matching the current filter. The action is recorded in the audit trail.</Text>,
          labels: { confirm: `${LABEL[action]} ${count.toLocaleString()} rows`, cancel: 'Cancel' },
          confirmProps: { color: action === 'reject' ? 'red' : undefined },
          onConfirm: () => void apply(),
        })
      } else await apply()
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(null)
    }
  }

  const hasSelection = selected.length > 0
  const target = hasSelection ? { row_ids: selected } : filter
  return (
    <Group gap="xs">
      <Text size="xs" c="dimmed">{hasSelection ? `${selected.length} selected:` : 'All matching:'}</Text>
      <Button size="xs" color="teal" loading={busy === 'confirm'} onClick={() => run('confirm', target)}>Confirm</Button>
      <Button size="xs" color="red" variant="light" loading={busy === 'reject'} onClick={() => run('reject', target)}>Reject</Button>
      <Button size="xs" variant="default" loading={busy === 'reset'} onClick={() => run('reset', target)}>Reset</Button>
    </Group>
  )
}
