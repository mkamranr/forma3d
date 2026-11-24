import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import type { Project } from '../../types/api'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import { DashboardPage } from './DashboardPage'

const user = { id: 'u1', email: 'a@example.com', display_name: 'Alice Doe', is_active: true, created_at: '2026-01-01T00:00:00Z' }

const project = (over: Partial<Project> = {}): Project => ({
  id: 'p1',
  name: 'Office Desk',
  description: 'Oak top',
  status: 'active',
  current_version_id: null,
  version_count: 3,
  thumbnail_url: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...over,
})

function renderDashboard() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/projects/:projectId" element={<div>workspace-page</div>} />
    </Routes>,
  )
}

describe('DashboardPage', () => {
  beforeEach(() => useAuthStore.setState({ status: 'authenticated', user, accessToken: 't' }))

  it('shows the first-model empty state', async () => {
    mockFetch({ 'GET /api/v1/projects': () => json(200, { items: [], total: 0 }) })
    renderDashboard()
    expect(await screen.findByText('Start creating your first 3D model.')).toBeInTheDocument()
  })

  it('lists projects with version count', async () => {
    mockFetch({ 'GET /api/v1/projects': () => json(200, { items: [project()], total: 1 }) })
    renderDashboard()
    const card = (await screen.findByText('Office Desk')).closest('article')!
    expect(within(card).getByText('3 versions')).toBeInTheDocument()
    expect(within(card).getByText(/Edited just now/)).toBeInTheDocument()
  })

  it('passes search and sort to the API', async () => {
    const fetchMock = mockFetch({ 'GET /api/v1/projects': () => json(200, { items: [], total: 0 }) })
    renderDashboard()
    await userEvent.type(screen.getByLabelText('Search projects'), 'chair')
    await userEvent.selectOptions(screen.getByLabelText('Sort projects'), 'name_asc')
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]) => String(u))
      expect(urls.some((u) => u.includes('q=chair') && u.includes('sort=name_asc'))).toBe(true)
    })
  })

  it('creates a project and opens its workspace', async () => {
    let body: unknown
    mockFetch({
      'GET /api/v1/projects': () => json(200, { items: [], total: 0 }),
      'POST /api/v1/projects': (_u, init) => {
        body = JSON.parse(String(init.body))
        return json(201, project({ id: 'new-id', name: 'Chair', version_count: 0 }))
      },
    })
    renderDashboard()
    await userEvent.click((await screen.findAllByRole('button', { name: /New Project/ }))[0])
    await userEvent.type(screen.getByLabelText('Project name'), '  Chair ')
    await userEvent.click(screen.getByRole('button', { name: 'Create project' }))
    expect(await screen.findByText('workspace-page')).toBeInTheDocument()
    expect(body).toEqual({ name: 'Chair', description: '' })
  })

  it('deletes a project after confirmation', async () => {
    let deleted = false
    mockFetch({
      'GET /api/v1/projects': () => json(200, deleted ? { items: [], total: 0 } : { items: [project()], total: 1 }),
      'DELETE /api/v1/projects/p1': () => {
        deleted = true
        return json(204, null)
      },
    })
    renderDashboard()
    await userEvent.click(await screen.findByRole('button', { name: 'Actions for Office Desk' }))
    await userEvent.click(screen.getByRole('menuitem', { name: /Delete/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Office Desk/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete project' }))
    await waitFor(() => expect(deleted).toBe(true))
    expect(await screen.findByText('Start creating your first 3D model.')).toBeInTheDocument()
  })
})
