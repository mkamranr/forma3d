import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { deskInventory, project, sceneState, sseResponse, version } from '../../test/fixtures'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import { WorkspacePage } from './WorkspacePage'

// No WebGL in jsdom: stub the canvas and GLB loading; everything else is real.
vi.mock('@react-three/fiber', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  Canvas: () => <div data-testid="canvas" />,
}))
vi.mock('./hooks', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useModel: () => ({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() }),
}))

const renderWorkspace = () =>
  renderWithProviders(
    <Routes>
      <Route path="/projects/:projectId" element={<WorkspacePage />} />
    </Routes>,
    { route: '/projects/p1' },
  )

const tree = () => screen.getByRole('tree', { name: 'Scene hierarchy' })

describe('WorkspacePage (live scene)', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null })
    useWorkspaceStore.getState().reset()
  })

  it('renders the committed hierarchy and selects objects by stable id', async () => {
    mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
    })
    renderWorkspace()
    expect(await within(tree()).findByText('Desk Top')).toBeInTheDocument()
    expect(screen.getByText('Version 1')).toBeInTheDocument()
    expect(screen.getByText(/v1 · Create a modern wooden desk/)).toBeInTheDocument()

    await userEvent.click(within(tree()).getByText('Leg Front Left'))
    expect(useWorkspaceStore.getState().selectedId).toBe('leg_fl')
    expect(screen.getByLabelText('Location Z')).toHaveValue(0.36)
    expect(screen.getByText('leg_fl')).toBeInTheDocument()
  })

  it('renames through the pipeline and refreshes when the job completes', async () => {
    let renamed = false
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => {
        if (!renamed) return json(200, sceneState(1))
        const inv = { ...deskInventory, objects: deskInventory.objects.map((o) => (o.object_id === 'desk_top' ? { ...o, name: 'Walnut Top' } : o)) }
        return json(200, sceneState(2, inv))
      },
      'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
      'PATCH /api/v1/projects/p1/scene/objects/desk_top': () => json(202, { job_id: 'j1', operation_batch_id: 'b2', status: 'queued' }),
      'GET /api/v1/jobs/j1/events': () => {
        renamed = true
        return sseResponse([
          { id: '1-0', type: 'job.started', data: { job_id: 'j1', stage: 'started', message: 'Building in Blender' } },
          { id: '2-0', type: 'job.completed', data: { job_id: 'j1', stage: 'done', message: 'Done', data: { version_number: 2 } } },
        ])
      },
    })
    renderWorkspace()
    await userEvent.dblClick(await within(tree()).findByText('Desk Top'))
    const input = screen.getByLabelText('Object name')
    await userEvent.clear(input)
    await userEvent.type(input, 'Walnut Top{Enter}')

    const patch = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PATCH')!
    expect(JSON.parse((patch[1] as RequestInit).body as string)).toEqual({ name: 'Walnut Top' })
    expect(await within(tree()).findByText('Walnut Top')).toBeInTheDocument()
    await waitFor(() => expect(useWorkspaceStore.getState().activeJob).toBeNull())
    expect(useWorkspaceStore.getState().toast).toMatch(/saved as version 2/)
    expect(screen.getByText('Version 2')).toBeInTheDocument()
  })

  it('commits a typed location on Enter as one edit', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, []),
      'PATCH /api/v1/projects/p1/scene/objects/desk_top': () => json(202, { job_id: 'j9', operation_batch_id: 'b', status: 'queued' }),
      'GET /api/v1/jobs/j9/events': () => new Promise<Response>(() => undefined), // stays open
    })
    renderWorkspace()
    await userEvent.click(await within(tree()).findByText('Desk Top'))
    const z = screen.getByLabelText('Location Z')
    await userEvent.clear(z)
    await userEvent.type(z, '0.8{Enter}')
    const patches = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === 'PATCH')
    expect(patches).toHaveLength(1)
    expect(JSON.parse((patches[0][1] as RequestInit).body as string)).toEqual({ location: [0, 0, 0.8] })
    expect(useWorkspaceStore.getState().activeJob?.id).toBe('j9')
    expect(screen.getByLabelText('Location X')).toBeDisabled() // edits locked while building
  })

  it('confirms before deleting an object with children (recursive)', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, []),
      'DELETE /api/v1/projects/p1/scene/objects/desk_top': () => json(202, { job_id: 'j2', operation_batch_id: 'b', status: 'queued' }),
      'GET /api/v1/jobs/j2/events': () => new Promise<Response>(() => undefined),
    })
    renderWorkspace()
    const row = (await within(tree()).findByText('Desk Top')).closest('[role="treeitem"]') as HTMLElement
    await userEvent.click(within(row).getByTitle('Delete'))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('also deletes 2 child objects')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    const del = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'DELETE')!
    expect(String(del[0])).toContain('recursive=true')
  })

  it('shows the empty state and builds the sample desk', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project({ current_version_id: null, version_count: 0 })),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(null)),
      'GET /api/v1/projects/p1/versions': () => json(200, []),
      'POST /api/v1/projects/p1/scene/starter': () => json(202, { job_id: 'j3', operation_batch_id: 'b', status: 'queued' }),
      'GET /api/v1/jobs/j3/events': () => new Promise<Response>(() => undefined),
    })
    renderWorkspace()
    expect(await screen.findByText('Start creating your first 3D model.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start with a sample desk' }))
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/scene/starter') && (init as RequestInit).method === 'POST')).toBe(true)
    expect((await screen.findAllByText(/Waiting for Blender/)).length).toBeGreaterThan(0)
  })

  it('explains a busy project instead of failing silently', async () => {
    mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, []),
      'PATCH /api/v1/projects/p1/scene/objects/leg_fl': () =>
        json(409, { detail: 'Another change to this project is still being built', code: 'project_busy' }),
    })
    renderWorkspace()
    const row = (await within(tree()).findByText('Leg Front Left')).closest('[role="treeitem"]') as HTMLElement
    await act(async () => userEvent.click(within(row).getByTitle('Hide')))
    await waitFor(() => expect(useWorkspaceStore.getState().toast).toMatch(/still building/))
    expect(useWorkspaceStore.getState().activeJob).toBeNull()
  })
})
