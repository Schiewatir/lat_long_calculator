import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Job, ProgressEvent } from '../api/types'
import { ACTIVE_STATUSES } from './format'

export function useJob(jobId: string) {
  return useQuery({ queryKey: ['job', jobId], queryFn: () => api<Job>(`/jobs/${jobId}`) })
}

/** Live progress over Server-Sent Events while the job is running. */
export function useJobEvents(job: Job | undefined): ProgressEvent | null {
  const qc = useQueryClient()
  const [event, setEvent] = useState<ProgressEvent | null>(null)
  const active = !!job && ACTIVE_STATUSES.includes(job.status)
  const jobId = job?.id
  useEffect(() => {
    if (!jobId || !active) return
    const es = new EventSource(`/api/jobs/${jobId}/events`, { withCredentials: true })
    let lastRows = 0
    es.onmessage = (msg) => {
      const data = JSON.parse(msg.data) as ProgressEvent
      setEvent(data)
      const now = Date.now()
      if (data.bands && now - lastRows > 5000) {
        // Partial review: refresh the grid occasionally while results arrive.
        lastRows = now
        void qc.invalidateQueries({ queryKey: ['rows', jobId] })
      }
      if (!ACTIVE_STATUSES.includes(data.status)) {
        es.close()
        void qc.invalidateQueries({ queryKey: ['job', jobId] })
        void qc.invalidateQueries({ queryKey: ['rows', jobId] })
        void qc.invalidateQueries({ queryKey: ['map', jobId] })
      }
    }
    return () => es.close()
  }, [jobId, active, qc])
  return event
}
