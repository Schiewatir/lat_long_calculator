import { notifications } from '@mantine/notifications'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, calculateDistance } from '../api/client'
import type { Capabilities, DistanceRequest } from '../api/types'

export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: () => api<Capabilities>('/capabilities'), staleTime: 300_000 })
}

export function notifyError(e: unknown, title = 'Something went wrong') {
  notifications.show({ color: 'red', title, message: e instanceof Error ? e.message : String(e) })
}

export function notifyOk(message: string, title?: string) {
  notifications.show({ color: 'teal', title, message })
}

export function useCalculateDistance() {
  return useMutation({ mutationFn: (req: DistanceRequest) => calculateDistance(req) })
}
