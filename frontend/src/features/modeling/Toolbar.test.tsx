import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { chatMessage, conversation, project, sceneState, version } from '../../test/fixtures'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
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
      <Route path="/" element={<div>Dashboard</div>} />
    </Routes>,
    { route: '/projects/p1' },
  )

const base = (extra: Record<string, Parameters<typeof mockFetch>[0][string]> = {}) =>
  mockFetch({
    'GET /api/v1/projects/p1': () => json(200, project()),
    'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
    'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1)),
    'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
    ...extra,
  })

describe('image-to-3D attachments', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null })
    useWorkspaceStore.getState().reset()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() }))
  })

  it('uploads a photo with +, sends it without text and shows what the vision model saw', async () => {
    let sent = false
    const userMsg = chatMessage({
      id: 'm1',
      role: 'user',
      content: 'Build this object from the attached photo.',
      metadata: {
        attachments: [{ id: 'ref1', name: 'chair.png', width: 800, height: 600 }],
        reference_description: 'office chair (minimalist), about 0.65 × 0.65 × 1 m.\nParts:\n- seat: box',
      },
    })
    const fetchMock = base({
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation(sent ? [userMsg] : [])),
      'POST /api/v1/projects/p1/references': () =>
        json(201, { id: 'ref1', name: 'chair.png', format: 'png', width: 800, height: 600, size: 10, url: '/api/v1/projects/p1/references/ref1', created_at: new Date().toISOString() }),
      'GET /api/v1/projects/p1/references/ref1': () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }),
      'POST /api/v1/projects/p1/conversation/messages': () => {
        sent = true
        return json(202, { message: userMsg, job_id: 'a1', status: 'queued' })
      },
      'GET /api/v1/jobs/a1/events': () => new Response('', { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    })
    renderWorkspace()
    await screen.findByText(/Your workspace is ready/)
    const send = screen.getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()

    const photo = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'chair.png', { type: 'image/png' })
    await userEvent.upload(screen.getByTestId('attach-input'), photo)
    expect(await screen.findByRole('img', { name: 'chair.png' })).toBeInTheDocument()
    await waitFor(() => expect(send).toBeEnabled())

    await userEvent.click(send)
    const post = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/conversation/messages') && (init as RequestInit).method === 'POST')!
    expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
      content: '',
      selected_object_id: null,
      selected_object_ids: [],
      attachment_ids: ['ref1'],
    })
    await waitFor(() =>
      expect(screen.getAllByText('Build this object from the attached photo.').some((el) => el.classList.contains('bubble'))).toBe(true),
    )
    expect(await screen.findByRole('img', { name: 'chair.png' })).toBeInTheDocument() // thumbnail in the sent message
    expect(screen.queryByRole('button', { name: /Remove chair.png/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('What I saw in the photo'))
    expect(screen.getByText(/office chair \(minimalist\)/)).toBeVisible()
  })

  it('rejects files that are not PNG or JPEG', async () => {
    base()
    renderWorkspace()
    await screen.findByText(/Your workspace is ready/)
    const pdf = new File(['%PDF'], 'spec.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByTestId('attach-input'), pdf, { applyAccept: false })
    await waitFor(() => expect(useWorkspaceStore.getState().toast).toMatch(/only PNG and JPEG/))
  })
})

describe('top bar and project menu', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null })
    useWorkspaceStore.getState().reset()
  })

  it('opens help and remembers viewport settings', async () => {
    base()
    renderWorkspace()
    await screen.findByText(/Your workspace is ready/)
    await userEvent.click(screen.getByRole('button', { name: 'Help' }))
    expect(screen.getByRole('dialog', { name: 'How forma3D works' })).toBeInTheDocument()
    expect(screen.getByText('Build from a photo')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')

    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const dialog = screen.getByRole('dialog', { name: 'Workspace settings' })
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Wireframe' }))
    await userEvent.click(within(dialog).getByRole('switch', { name: /Axes gizmo/ }))
    expect(useWorkspaceStore.getState()).toMatchObject({ shading: 'wireframe', showAxes: true })
    expect(JSON.parse(localStorage.getItem('forma3d.viewport')!)).toMatchObject({ shading: 'wireframe', showAxes: true })
    useWorkspaceStore.getState().reset() // e.g. opening another project
    expect(useWorkspaceStore.getState()).toMatchObject({ shading: 'wireframe', showAxes: true })
  })

  it('archives the project and switches to another one from the workspace menu', async () => {
    const fetchMock = base({
      'PUT /api/v1/projects/p1': () => json(200, { ...project(), status: 'archived' }),
      'GET /api/v1/projects': () =>
        json(200, { items: [project(), { ...project(), id: 'p2', name: 'Bookshelf' }], total: 2 }),
    })
    renderWorkspace()
    await screen.findByText(/Your workspace is ready/)

    await userEvent.click(screen.getByRole('button', { name: 'Project actions' }))
    await userEvent.click(screen.getByRole('menuitem', { name: /Archive/ }))
    const put = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/projects/p1') && (init as RequestInit).method === 'PUT')!
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ status: 'archived' })
    await waitFor(() => expect(useWorkspaceStore.getState().toast).toMatch(/archived/))

    await userEvent.click(screen.getByRole('button', { name: /Studio workspace/ }))
    const menu = screen.getByRole('menu')
    expect(await within(menu).findByText('Bookshelf')).toBeInTheDocument()
    expect(within(menu).queryByText(project().name)).not.toBeInTheDocument() // the current project is not listed
    await userEvent.click(within(menu).getByRole('menuitem', { name: /All projects/ }))
    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
  })
})
