import { notifications } from '@mantine/notifications'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Capabilities } from '../api/types'

export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: () => api<Capabilities>('/capabilities'), staleTime: 300_000 })
}

export function notifyError(e: unknown, title = 'Something went wrong') {
  notifications.show({ color: 'red', title, message: e instanceof Error ? e.message : String(e) })
}

export function notifyOk(message: string, title?: string) {
  notifications.show({ color: 'teal', title, message })
}
