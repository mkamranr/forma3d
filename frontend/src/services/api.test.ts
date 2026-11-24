import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '../stores/authStore'
import { json, mockFetch } from '../test/utils'
import { ApiError, apiRequest } from './api'

const user = { id: 'u1', email: 'a@example.com', display_name: 'Alice', is_active: true, created_at: '2026-01-01T00:00:00Z' }

describe('apiRequest', () => {
  beforeEach(() => useAuthStore.setState({ status: 'authenticated', user, accessToken: 'old-token' }))

  it('sends the bearer token and parses JSON', async () => {
    const fetchMock = mockFetch({ 'GET /api/v1/projects': () => json(200, { items: [], total: 0 }) })
    await expect(apiRequest('/projects')).resolves.toEqual({ items: [], total: 0 })
    const headers = fetchMock.mock.calls[0][1]!.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer old-token')
  })

  it('refreshes once on 401 and retries with the new token', async () => {
    let calls = 0
    const fetchMock = mockFetch({
      'GET /api/v1/auth/me': (_url, init) => {
        calls += 1
        const auth = (init.headers as Record<string, string>).Authorization
        return auth === 'Bearer new-token' ? json(200, user) : json(401, { detail: 'expired', code: 'token_expired' })
      },
      'POST /api/v1/auth/refresh': () => json(200, { access_token: 'new-token', token_type: 'bearer', expires_in: 900, user }),
    })

    // Two concurrent requests share a single refresh call.
    const [a, b] = await Promise.all([apiRequest('/auth/me'), apiRequest('/auth/me')])
    expect(a).toEqual(user)
    expect(b).toEqual(user)
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/auth/refresh'))).toHaveLength(1)
    expect(calls).toBe(4)
    expect(useAuthStore.getState().accessToken).toBe('new-token')
  })

  it('clears the session when refresh fails', async () => {
    mockFetch({
      'GET /api/v1/projects': () => json(401, { detail: 'expired' }),
      'POST /api/v1/auth/refresh': () => json(401, { detail: 'invalid', code: 'invalid_refresh' }),
    })
    await expect(apiRequest('/projects')).rejects.toMatchObject({ status: 401 })
    expect(useAuthStore.getState().status).toBe('anonymous')
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('maps error bodies to ApiError with code and message', async () => {
    mockFetch({ 'POST /api/v1/projects': () => json(409, { detail: 'Taken', code: 'conflict' }) })
    const err = await apiRequest('/projects', { method: 'POST', body: {} }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: 'conflict', message: 'Taken' })
  })

  it('uses the first pydantic validation message for 422', async () => {
    mockFetch({ 'POST /api/v1/projects': () => json(422, { detail: [{ msg: 'name must not be blank' }] }) })
    await expect(apiRequest('/projects', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
      message: 'name must not be blank',
    })
  })

  it('returns undefined for 204', async () => {
    mockFetch({ 'DELETE /api/v1/projects/p1': () => json(204, null) })
    await expect(apiRequest('/projects/p1', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})
