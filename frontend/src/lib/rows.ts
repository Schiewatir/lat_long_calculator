import { useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import type { ResultRow, RowsPage } from '../api/types'
import { notifyError } from './hooks'

export interface RowActionBody {
  action: 'confirm' | 'reject' | 'reset' | 'pick_candidate' | 'manual_pin' | 'rerun'
  version: number
  candidate_id?: number
  lat?: number
  lon?: number
  input?: Record<string, string>
}

/** Row actions with optimistic-concurrency handling; updates every cached grid page in place. */
export function useRowAction(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ rowId, body }: { rowId: number; body: RowActionBody }) =>
      api<ResultRow>(`/jobs/${jobId}/rows/${rowId}/action`, { json: body }),
    onSuccess: (row) => {
      qc.setQueriesData<InfiniteData<RowsPage>>({ queryKey: ['rows', jobId] }, (data) =>
        data && {
          ...data,
          pages: data.pages.map((p) => ({ ...p, rows: p.rows.map((r) => (r.id === row.id ? row : r)) })),
        },
      )
      void qc.invalidateQueries({ queryKey: ['row', jobId, row.id] })
      void qc.invalidateQueries({ queryKey: ['job', jobId] })
      void qc.invalidateQueries({ queryKey: ['map', jobId] })
    },
    onError: (e, { rowId }) => {
      if (e instanceof ApiError && e.status === 409) {
        notifyError(e, 'Row changed meanwhile')
        void qc.invalidateQueries({ queryKey: ['rows', jobId] })
        void qc.invalidateQueries({ queryKey: ['row', jobId, rowId] })
      } else notifyError(e, 'Action failed')
    },
  })
}
