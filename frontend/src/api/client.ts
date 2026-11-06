/** Fetch wrapper: same-origin cookies, CSRF double-submit header, 401 -> login redirect. */
import type { DistanceRequest, DistanceResponse } from './types'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function csrfToken(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)gw_csrf=([^;]+)/)
  return m ? decodeURIComponent(m[1]) : null
}

function messageOf(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>
    if (typeof d.message === 'string') return d.message
    if (Array.isArray(d.problems)) return d.problems.join('; ')
    if (Array.isArray(detail)) {
      return (detail as { msg?: string }[]).map((e) => e.msg).filter(Boolean).join('; ') || fallback
    }
  }
  return fallback
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const method = (init.method ?? (init.json !== undefined ? 'POST' : 'GET')).toUpperCase()
  let body = init.body
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(init.json)
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = csrfToken()
    if (token) headers.set('X-CSRF-Token', token)
  }
  const res = await fetch(`/api${path}`, { ...init, method, headers, body, credentials: 'same-origin' })
  if (res.status === 204) return undefined as T
  const isJson = res.headers.get('content-type')?.includes('application/json')
  const payload = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = isJson ? (payload as { detail?: unknown }).detail : payload
    if (res.status === 401 && !path.startsWith('/auth/login')) onUnauthorized?.()
    throw new ApiError(res.status, messageOf(detail, `Request failed (${res.status})`), detail)
  }
  return payload as T
}

export function calculateDistance(req: DistanceRequest) {
  return api<DistanceResponse>('/distance', { json: req })
}
