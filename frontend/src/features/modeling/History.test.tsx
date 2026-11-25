import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { chatMessage, conversation, project, sceneState, version } from '../../test/fixtures'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import { historyTargets } from './hooks'
import { WorkspacePage } from './WorkspacePage'

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
const calls = (fetchMock: ReturnType<typeof mockFetch>, suffix: string) =>
  fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith(suffix) && (init as RequestInit)?.method === 'POST')

// v1 <- v2 <- v4 (newest branch), and v1 <- v3: head is v2 after an undo.
const branchedVersions = (current: number) => [
  version(4, { parent_version_id: 'v2', created_at: '2026-11-24T10:04:00Z', is_current: current === 4 }),
  version(3, { parent_version_id: 'v1', created_at: '2026-11-24T10:03:00Z', is_current: current === 3,
    summary: 'Add two drawers', prompt: 'Add two drawers', source: 'chat', message_id: 'm1' }),
  version(2, { created_at: '2026-11-24T10:02:00Z', is_current: current === 2 }),
  version(1, { created_at: '2026-11-24T10:01:00Z', is_current: current === 1 }),
]

describe('version history (Phase 6)', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null })
    useWorkspaceStore.getState().reset()
  })

  it('computes undo/redo targets from the version tree', () => {
    const t = historyTargets(branchedVersions(2))
    expect(t.undo?.version_number).toBe(1)
    expect(t.redo?.version_number).toBe(4) // most recent child
    expect(historyTargets(branchedVersions(1)).redo?.version_number).toBe(3) // v3 is newer than v2
    expect(historyTargets(branchedVersions(1)).undo).toBeNull()
  })

  it('undoes and redoes from the toolbar and with keyboard shortcuts', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(2)),
      'GET /api/v1/projects/p1/versions': () => json(200, branchedVersions(2)),
      'POST /api/v1/projects/p1/modeling/undo': () => json(200, { version_id: 'v1', version_number: 1, project: project() }),
      'POST /api/v1/projects/p1/modeling/redo': () => json(200, { version_id: 'v4', version_number: 4, project: project() }),
    })
    renderWorkspace()
    const undo = await screen.findByRole('button', { name: 'Undo' })
    await waitFor(() => expect(undo).toBeEnabled())
    expect(undo).toHaveAttribute('title', expect.stringContaining('back to v1'))
    const redo = screen.getByRole('button', { name: 'Redo' })
    expect(redo).toHaveAttribute('title', expect.stringContaining('forward to v4'))

    await userEvent.click(undo)
    await waitFor(() => expect(calls(fetchMock, '/modeling/undo')).toHaveLength(1))
    await waitFor(() => expect(useWorkspaceStore.getState().toast).toBe('Undone — now at version 1'))

    await userEvent.keyboard('{Control>}{Shift>}z{/Shift}{/Control}')
    await waitFor(() => expect(calls(fetchMock, '/modeling/redo')).toHaveLength(1))
    await userEvent.keyboard('{Control>}z{/Control}')
    await waitFor(() => expect(calls(fetchMock, '/modeling/undo')).toHaveLength(2))

    // typing in the prompt never triggers history shortcuts
    await userEvent.click(screen.getByLabelText('Prompt'))
    await userEvent.keyboard('{Control>}z{/Control}')
    expect(calls(fetchMock, '/modeling/undo')).toHaveLength(2)
  })

  it('disables undo/redo when there is nowhere to go', async () => {
    mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
    })
    renderWorkspace()
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Redo' })).toBeDisabled()
  })

  it('shows prompts and branches, compares two versions and jumps to the conversation', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/conversation': () =>
        json(200, conversation([chatMessage({ id: 'm1', role: 'user', content: 'Add two drawers' })])),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(4)),
      'GET /api/v1/projects/p1/versions': () => json(200, branchedVersions(4)),
      'POST /api/v1/projects/p1/versions/compare': () =>
        json(200, {
          base: { id: 'v1', version_number: 1, summary: 'Create' },
          target: { id: 'v3', version_number: 3, summary: 'Add two drawers' },
          objects: {
            added: [{ object_id: 'drawer_left', name: 'Drawer Left' }],
            removed: [],
            changed: [{ object_id: 'desk_top', name: 'Desk Top', fields: { dimensions: { before: [1.6, 0.8, 0.04], after: [1.8, 0.8, 0.04] } } }],
            unchanged: 4,
          },
          materials: { added: [{ material_id: 'walnut', name: 'Walnut' }], removed: [], changed: [] },
        }),
    })
    renderWorkspace()
    await userEvent.click(await screen.findByRole('button', { name: /Versions/ }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('“Add two drawers”')).toBeInTheDocument()
    // v3 was made after restoring v1 (its parent is not the version before it)
    expect(within(dialog).getByText(/from v1/)).toBeInTheDocument()

    await userEvent.click(within(dialog).getByLabelText('Compare version 3'))
    await userEvent.click(within(dialog).getByLabelText('Compare version 1'))
    const req = await waitFor(() => {
      const c = calls(fetchMock, '/versions/compare')
      expect(c).toHaveLength(1)
      return c[0]
    })
    expect(JSON.parse((req[1] as RequestInit).body as string)).toEqual({ base_version_id: 'v1', target_version_id: 'v3' })
    const diff = await within(dialog).findByLabelText('Version comparison')
    expect(within(diff).getByText(/Drawer Left/)).toBeInTheDocument()
    expect(within(diff).getByText(/1\.6 × 0\.8 × 0\.04/)).toBeInTheDocument()
    expect(within(diff).getByText(/material Walnut/)).toBeInTheDocument()
    expect(within(diff).getByText('4 objects unchanged')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: /Chat/ }))
    expect(useWorkspaceStore.getState().versionsOpen).toBe(false)
    expect(useWorkspaceStore.getState().highlightMessageId).toBe('m1')
    await waitFor(() => expect(document.getElementById('msg-m1')).toHaveClass('highlighted'))
  })

  it('multi-selects with Shift-click and sends the whole selection with a message', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/projects/p1': () => json(200, project()),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
      'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
      'POST /api/v1/projects/p1/conversation/messages': () =>
        json(202, { message: chatMessage({ id: 'mx', role: 'user', content: 'Paint them red' }), job_id: 'a1', status: 'queued' }),
      'GET /api/v1/jobs/a1/events': () => new Promise<Response>(() => undefined),
    })
    const user = userEvent.setup()
    renderWorkspace()
    await user.click(await within(tree()).findByText('Leg Front Left'))
    await user.keyboard('{Shift>}')
    await user.click(within(tree()).getByText('Desk Top'))
    await user.keyboard('{/Shift}')
    expect(useWorkspaceStore.getState().selectedIds).toEqual(['leg_fl', 'desk_top'])
    expect(useWorkspaceStore.getState().selectedId).toBe('desk_top') // primary = last
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    expect(within(tree()).getAllByRole('treeitem', { selected: true })).toHaveLength(2)

    await user.type(screen.getByLabelText('Prompt'), 'Paint them red{Enter}')
    const post = await waitFor(() => {
      const c = calls(fetchMock, '/conversation/messages')
      expect(c).toHaveLength(1)
      return c[0]
    })
    expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
      content: 'Paint them red',
      selected_object_id: 'desk_top',
      selected_object_ids: ['leg_fl', 'desk_top'],
    })

    // Shift-click again removes an object from the selection
    await user.keyboard('{Shift>}')
    await user.click(within(tree()).getByText('Desk Top'))
    await user.keyboard('{/Shift}')
    expect(useWorkspaceStore.getState().selectedIds).toEqual(['leg_fl'])
  })
})
