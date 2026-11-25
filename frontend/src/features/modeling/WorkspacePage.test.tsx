import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { chatMessage, conversation, deskInventory, project, sceneState, sseResponse, version } from '../../test/fixtures'
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
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

  it('sends a chat message with the selection and shows the reply with its version', async () => {
    let done = false
    const user = chatMessage({ id: 'm1', role: 'user', content: 'Make the legs black metal', metadata: { selected_object_id: 'leg_fl' } })
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(done ? 2 : 1)),
      'GET /api/v1/projects/p1/versions': () => json(200, done ? [version(2, { is_current: true }), version(1)] : [version(1, { is_current: true })]),
      'GET /api/v1/projects/p1/conversation': () =>
        json(200, conversation(done ? [user, chatMessage({ id: 'm2', role: 'assistant', content: 'The legs are now black metal.', intent: 'modeling', metadata: { version_id: 'v2', version_number: 2 } })] : [])),
      'POST /api/v1/projects/p1/conversation/messages': () => json(202, { message: user, job_id: 'a1', status: 'queued' }),
      'GET /api/v1/jobs/a1/events': () => {
        done = true
        return sseResponse([
          { id: '1-0', type: 'agent.started', data: { job_id: 'a1', stage: 'context', message: 'Reading your scene' } },
          { id: '2-0', type: 'plan.created', data: { job_id: 'a1', stage: 'planned', message: 'Planned 4 operation(s)' } },
          { id: '3-0', type: 'blender.progress', data: { job_id: 'a1', stage: 'executing', message: 'Exporting GLB' } },
          { id: '4-0', type: 'job.completed', data: { job_id: 'a1', stage: 'done', message: 'The legs are now black metal.', data: { version_number: 2, intent: 'modeling' } } },
        ])
      },
    })
    renderWorkspace()
    expect(await screen.findByText(/Your workspace is ready/)).toBeInTheDocument()
    await userEvent.click(await within(tree()).findByText('Leg Front Left'))
    await userEvent.type(screen.getByLabelText('Prompt'), 'Make the legs black metal{Enter}')

    const post = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/conversation/messages') && (init as RequestInit).method === 'POST')!
    expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({ content: 'Make the legs black metal', selected_object_id: 'leg_fl' })
    expect(await screen.findByText('The legs are now black metal.')).toBeInTheDocument()
    expect(screen.getByText('Saved as v2')).toBeInTheDocument()
    await waitFor(() => expect(useWorkspaceStore.getState().activeJob).toBeNull())
    expect(useWorkspaceStore.getState().toast).toBe('Saved as version 2')
    expect(screen.getByLabelText('Prompt')).toHaveValue('')
  })

  it('offers a GLB download for export replies, pinned to the exported version', async () => {
    mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(3)),
      'GET /api/v1/projects/p1/versions': () => json(200, [version(3, { is_current: true }), version(2), version(1)]),
      'GET /api/v1/projects/p1/conversation': () =>
        json(200, conversation([
          chatMessage({ id: 'm1', role: 'user', content: 'Export it' }),
          chatMessage({ id: 'm2', role: 'assistant', content: 'Ready.', intent: 'export', metadata: { action: 'export', export_version_id: 'v2', export_version_number: 2 } }),
        ])),
    })
    renderWorkspace()
    expect(await screen.findByRole('button', { name: /Download GLB · v2/ })).toBeInTheDocument()
  })

  it('shows a failed turn as an assistant reply, not as a toast', async () => {
    let done = false
    const user = chatMessage({ id: 'm1', role: 'user', content: 'Add a spaceship' })
    mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
      'GET /api/v1/projects/p1/conversation': () =>
        json(200, conversation(done ? [user, chatMessage({ id: 'm2', role: 'assistant', status: 'failed', content: 'I could not plan that. The scene is unchanged.', metadata: { error_code: 'plan_invalid' } })] : [])),
      'POST /api/v1/projects/p1/conversation/messages': () => json(202, { message: user, job_id: 'a2', status: 'queued' }),
      'GET /api/v1/jobs/a2/events': () => {
        done = true
        return sseResponse([{ id: '1-0', type: 'job.failed', data: { job_id: 'a2', stage: 'failed', message: 'I could not plan that.' } }])
      },
    })
    renderWorkspace()
    await within(tree()).findByText('Desk Top')
    await userEvent.type(screen.getByLabelText('Prompt'), 'Add a spaceship{Enter}')
    const reply = await screen.findByText('I could not plan that. The scene is unchanged.')
    expect(reply).toHaveClass('failed')
    await waitFor(() => expect(useWorkspaceStore.getState().activeJob).toBeNull())
    expect(useWorkspaceStore.getState().toast).toBe('')
  })
})
