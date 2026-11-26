import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { RequireAdmin } from '../../app/App'
import { UserMenu } from '../../components/layout/UserMenu'
import { useAuthStore } from '../../stores/authStore'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import type { AppConfig } from '../../types/config'
import { LoginPage, RegisterPage } from '../auth/AuthPages'
import { ConfigurationPage } from './ConfigurationPage'

const admin = { id: 'u1', email: 'ada@example.com', display_name: 'Ada', is_active: true, is_admin: true, created_at: '2026-01-01T00:00:00Z' }
const member = { ...admin, id: 'u2', email: 'bob@example.com', display_name: 'Bob', is_admin: false }

const config: AppConfig = {
  version: 3,
  groups: [
    {
      id: 'ai',
      label: 'AI model',
      help: '',
      fields: [
        { key: 'llm_model', label: 'Model', help: 'Model name', type: 'string', options: [], min: null, max: null, source: 'env', env: 'LLM_MODEL', value: 'agents-a1', default: 'agents-a1' },
        { key: 'llm_api_key', label: 'API key', help: '', type: 'secret', options: [], min: null, max: null, source: 'app', env: 'LLM_API_KEY', value: null, is_set: true, default_is_set: false },
      ],
    },
    {
      id: 'limits',
      label: 'Limits & retention',
      help: '',
      fields: [
        { key: 'modeling_max_scene_objects', label: 'Max objects', help: '', type: 'int', options: [], min: 1, max: 5000, source: 'app', env: 'MODELING_MAX_SCENE_OBJECTS', value: 40, default: 500 },
      ],
    },
    {
      id: 'accounts',
      label: 'Accounts',
      help: '',
      fields: [
        { key: 'registration_open', label: 'Allow sign-ups', help: '', type: 'bool', options: [], min: null, max: null, source: 'default', env: 'REGISTRATION_OPEN', value: true, default: true },
      ],
    },
  ],
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<div>dashboard-page</div>} />
      <Route path="/configuration" element={<RequireAdmin><ConfigurationPage /></RequireAdmin>} />
    </Routes>,
    { route: '/configuration' },
  )
}

describe('ConfigurationPage', () => {
  beforeEach(() => useAuthStore.setState({ status: 'authenticated', user: admin, accessToken: 't' }))

  it('is for administrators only', async () => {
    useAuthStore.setState({ user: member })
    renderPage()
    expect(await screen.findByText('dashboard-page')).toBeInTheDocument()
  })

  it('shows where each value comes from and saves only the changes', async () => {
    let body: unknown = null
    mockFetch({
      'GET /api/v1/admin/config': () => json(200, config),
      'PUT /api/v1/admin/config': (_u, init) => {
        body = JSON.parse(String(init.body))
        return json(200, config)
      },
    })
    renderPage()
    const model = await screen.findByLabelText('Model')
    expect(model).toHaveValue('agents-a1')
    expect(screen.getByTitle('From the server environment (.env)')).toBeInTheDocument()
    expect(screen.getByLabelText('API key')).toHaveAttribute('placeholder', expect.stringContaining('set'))

    await userEvent.clear(model)
    await userEvent.type(model, 'qwen-vl')
    expect(screen.getByText('1 unsaved change')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /Limits/ }))
    await userEvent.click(screen.getByLabelText('Reset Max objects'))
    expect(screen.getByText('Will reset')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(body).toEqual({ values: { llm_model: 'qwen-vl', modeling_max_scene_objects: null } }))
    expect(await screen.findByText(/Saved\./)).toBeInTheDocument()
  })

  it('tests the LLM connection with unsaved values', async () => {
    let body: unknown = null
    mockFetch({
      'GET /api/v1/admin/config': () => json(200, config),
      'POST /api/v1/admin/config/test-llm': (_u, init) => {
        body = JSON.parse(String(init.body))
        return json(200, {
          ok: false,
          provider: 'vllm',
          model: 'qwen-vl',
          checks: [
            { name: 'server', label: 'Server & model', ok: true, detail: 'Reachable; 1 model(s) served.', ms: 12 },
            { name: 'vision', label: 'Photo input (image-to-3D)', ok: false, detail: 'Answered “blue”', ms: 300 },
          ],
        })
      },
    })
    renderPage()
    const model = await screen.findByLabelText('Model')
    await userEvent.clear(model)
    await userEvent.type(model, 'qwen-vl')
    await userEvent.click(screen.getByRole('button', { name: /Test connection/ }))
    expect(await screen.findByText('Connection has problems')).toBeInTheDocument()
    expect(screen.getByText('Reachable; 1 model(s) served.')).toBeInTheDocument()
    expect(body).toEqual({ values: { llm_model: 'qwen-vl' } })
  })

  it('manages users from the Accounts tab', async () => {
    let patch: unknown = null
    mockFetch({
      'GET /api/v1/admin/config': () => json(200, config),
      'GET /api/v1/admin/users': () =>
        json(200, {
          items: [
            { ...admin, projects: 2 },
            { ...member, projects: 0 },
          ],
          total: 2,
        }),
      'PATCH /api/v1/admin/users/u2': (_u, init) => {
        patch = JSON.parse(String(init.body))
        return json(200, { ...member, is_active: false, projects: 0 })
      },
    })
    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: 'Accounts' }))
    const row = (await screen.findByText('bob@example.com')).closest('tr')!
    expect(screen.getByLabelText('ada@example.com active')).toBeDisabled() // not yourself
    await userEvent.click(within(row).getByLabelText('bob@example.com active'))
    await waitFor(() => expect(patch).toEqual({ is_active: false }))
  })
})

describe('Configuration entry points', () => {
  it('shows the Configuration link to admins only', async () => {
    useAuthStore.setState({ status: 'authenticated', user: member, accessToken: 't' })
    const { unmount } = renderWithProviders(<UserMenu />)
    await userEvent.click(screen.getByTitle('Bob'))
    expect(screen.queryByText('Configuration')).not.toBeInTheDocument()
    unmount()
    useAuthStore.setState({ user: admin })
    renderWithProviders(<UserMenu />)
    await userEvent.click(screen.getByTitle('Ada'))
    expect(screen.getByRole('menuitem', { name: /Configuration/ })).toBeInTheDocument()
  })

  it('hides sign-up when an admin closed it', async () => {
    useAuthStore.setState({ status: 'anonymous', user: null, accessToken: null })
    mockFetch({ 'GET /api/v1/config': () => json(200, { registration_open: false }) })
    const { unmount } = renderWithProviders(<LoginPage />)
    expect(await screen.findByText('New accounts are created by your administrator.')).toBeInTheDocument()
    unmount()
    renderWithProviders(<RegisterPage />)
    expect(await screen.findByText(/Sign-ups are closed/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create account' })).not.toBeInTheDocument()
  })
})
