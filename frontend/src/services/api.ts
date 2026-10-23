const API_PREFIX = '/api'

type ErrorPayload = {
  error?: string
  message?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly path: string

  constructor(message: string, status: number, path: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.path = path
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  })

  const payload = (await response.json().catch(() => null)) as (T & ErrorPayload) | null
  if (!response.ok) {
    const message = payload?.message || payload?.error || `请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status, path)
  }
  return payload as T
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })

  const payload = (await response.json().catch(() => null)) as (T & ErrorPayload) | null
  if (!response.ok) {
    const message = payload?.message || payload?.error || `请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status, path)
  }
  return payload as T
}
