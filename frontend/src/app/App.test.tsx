import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '../stores/authStore'
import { json, mockFetch, renderWithProviders } from '../test/utils'
import { AppRoutes } from './App'

const user = { id: 'u1', email: 'a@example.com', display_name: 'Alice', is_active: true, created_at: '2026-01-01T00:00:00Z' }

describe('routing and auth guards', () => {
  beforeEach(() => useAuthStore.setState({ status: 'anonymous', user: null, accessToken: null }))

  it('redirects anonymous users to login', async () => {
    renderWithProviders(<AppRoutes />, { route: '/projects/p1' })
    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
  })

  it('logs in and lands on the dashboard', async () => {
    mockFetch({
      'POST /api/v1/auth/login': () => json(200, { access_token: 'tok', token_type: 'bearer', expires_in: 900, user }),
      'GET /api/v1/projects': () => json(200, { items: [], total: 0 }),
    })
    renderWithProviders(<AppRoutes />, { route: '/login' })
    await userEvent.type(screen.getByLabelText('Email'), 'a@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 's3cret-pass')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Your projects' })).toBeInTheDocument()
    expect(useAuthStore.getState().accessToken).toBe('tok')
  })

  it('shows the server error on failed login', async () => {
    mockFetch({ 'POST /api/v1/auth/login': () => json(401, { detail: 'Invalid email or password', code: 'invalid_credentials' }) })
    renderWithProviders(<AppRoutes />, { route: '/login' })
    await userEvent.type(screen.getByLabelText('Email'), 'a@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-pass')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
  })

  it('validates password length on register before calling the API', async () => {
    const fetchMock = mockFetch({})
    renderWithProviders(<AppRoutes />, { route: '/register' })
    await userEvent.type(screen.getByLabelText('Name'), 'Bob')
    await userEvent.type(screen.getByLabelText('Email'), 'b@example.com')
    const pw = screen.getByLabelText('Password')
    pw.removeAttribute('minLength')
    await userEvent.type(pw, 'short')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at least 8 characters')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps authenticated users away from the login page', async () => {
    useAuthStore.setState({ status: 'authenticated', user, accessToken: 'tok' })
    mockFetch({ 'GET /api/v1/projects': () => json(200, { items: [], total: 0 }) })
    renderWithProviders(<AppRoutes />, { route: '/login' })
    expect(await screen.findByRole('heading', { name: 'Your projects' })).toBeInTheDocument()
  })
})
