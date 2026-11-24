import { useAuthStore } from '../stores/authStore'
import type { TokenResponse } from '../types/api'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, message: string, code = 'error') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  /** Attach the access token and retry once through /auth/refresh on 401. */
  auth?: boolean
  signal?: AbortSignal
}

async function parseError(res: Response): Promise<ApiError> {
  let message = res.statusText || 'Request failed'
  let code = 'error'
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') message = data.detail
    else if (Array.isArray(data?.detail) && data.detail[0]?.msg) message = String(data.detail[0].msg)
    if (typeof data?.code === 'string') code = data.code
    else if (res.status === 422) code = 'validation_error'
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, message, code)
}

let refreshInFlight: Promise<boolean> | null = null

/** Exchange the httpOnly refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, { method: 'POST', credentials: 'same-origin' })
        if (!res.ok) {
          useAuthStore.getState().clear()
          return false
        }
        const data = (await res.json()) as TokenResponse
        useAuthStore.getState().setSession(data.access_token, data.user)
        return true
      } catch {
        useAuthStore.getState().clear()
        return false
      } finally {
        refreshInFlight = null
      }
    })()
  }
  return refreshInFlight
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, signal } = options

  const send = () => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const token = useAuthStore.getState().accessToken
    if (auth && token) headers.Authorization = `Bearer ${token}`
    return fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    })
  }

  let res = await send()
  if (res.status === 401 && auth) {
    if (await refreshSession()) res = await send()
  }
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
