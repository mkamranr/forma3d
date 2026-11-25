import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { dispositionName } from '../../services/outputs'
import { useAuthStore } from '../../stores/authStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { chatMessage, conversation, deskInventory, obj, project, sceneState, version } from '../../test/fixtures'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import type { Output, SceneInventory } from '../../types/scene'
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

const output = (extra: Partial<Output> = {}): Output => ({
  id: 'e1',
  project_id: 'p1',
  status: 'succeeded',
  progress: {},
  error_message: null,
  version_id: 'v1',
  version_number: 1,
  settings: { format: 'stl', unit: 'mm' },
  details: {},
  size: 1234,
  url: '/api/v1/exports/e1/download',
  created_at: '2026-11-25T10:00:00Z',
  completed_at: '2026-11-25T10:00:05Z',
  ...extra,
})

const png = () => new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { 'Content-Type': 'image/png' } })

const base = (inv: SceneInventory = deskInventory) => ({
  'GET /api/v1/projects/p1': () => json(200, project()),
  'GET /api/v1/projects/p1/scene': () => json(200, sceneState(1, inv)),
  'GET /api/v1/projects/p1/versions': () => json(200, [version(1, { is_current: true })]),
  'GET /api/v1/projects/p1/textures': () => json(200, []),
  'GET /api/v1/projects/p1/versions/v1/assets/thumbnail': () => png(),
})

const bodyOf = (fetchMock: ReturnType<typeof mockFetch>, method: string, suffix: string) => {
  const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith(suffix) && (init as RequestInit)?.method === method)
  return call ? JSON.parse((call[1] as RequestInit).body as string) : undefined
}

describe('exports, renders, lights and textures (Phase 7)', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null })
    useWorkspaceStore.getState().reset()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
  })

  it('exports STL in millimeters and polls until the file is ready', async () => {
    let polls = 0
    const fetchMock = mockFetch({
      ...base(),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'POST /api/v1/projects/p1/exports': () => json(202, output({ status: 'queued', url: null })),
      'GET /api/v1/exports/e1': () => json(200, ++polls < 2 ? output({ status: 'running', url: null }) : output()),
    })
    renderWorkspace()
    const exportBtn = await screen.findByRole('button', { name: /^Export$/ })
    await waitFor(() => expect(exportBtn).toBeEnabled())
    await userEvent.click(exportBtn)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('radio', { name: /STL/ }))
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Millimeters' }))
    await userEvent.click(within(dialog).getByRole('button', { name: /Export STL/ }))

    expect(bodyOf(fetchMock, 'POST', '/projects/p1/exports')).toEqual({ format: 'stl', unit: 'mm', include_hidden: false, version_id: 'v1' })
    expect(await within(dialog).findByRole('button', { name: /Download STL · v1/ }, { timeout: 4000 })).toBeInTheDocument()
  })

  it('shows the export / render the assistant started in the chat', async () => {
    mockFetch({
      ...base(),
      'GET /api/v1/projects/p1/conversation': () =>
        json(200, conversation([
          chatMessage({ id: 'm1', role: 'assistant', content: 'Preparing your OBJ file', metadata: { action: 'export', output_job_id: 'e2', export_format: 'obj' } }),
          chatMessage({ id: 'm2', role: 'assistant', content: 'Rendering your model now.', metadata: { action: 'render', output_job_id: 'r1' } }),
          chatMessage({ id: 'm3', role: 'assistant', content: 'Cannot render', metadata: { action: 'render', output_error: 'Nothing to render yet' } }),
          chatMessage({ id: 'm4', role: 'assistant', content: 'Old STL', metadata: { action: 'export', output_job_id: 'e9' } }),
        ])),
      'GET /api/v1/exports/e9': () => json(200, output({ id: 'e9', url: null })),
      'GET /api/v1/exports/e2': () => json(200, output({ id: 'e2', settings: { format: 'obj' } })),
      'GET /api/v1/renders/r1': () => json(200, output({ id: 'r1', settings: { quality: 'standard' }, url: '/api/v1/renders/r1/image' })),
      'GET /api/v1/renders/r1/image': () => png(),
    })
    renderWorkspace()
    expect(await screen.findByRole('button', { name: /Download OBJ · v1/ })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /Download PNG · v1/ })).toBeInTheDocument()
    expect(await screen.findByAltText('Render of version 1')).toHaveAttribute('src', 'blob:x')
    expect(screen.getByText('Nothing to render yet')).toBeInTheDocument()
    expect(await screen.findByText(/This file has expired/)).toBeInTheDocument() // purged by retention
  })

  it('starts a render with the chosen quality, size and camera', async () => {
    const inv: SceneInventory = {
      ...deskInventory,
      objects: [
        ...deskInventory.objects,
        obj({ object_id: 'hero_cam', name: 'Hero Camera', type: 'CAMERA', material_ids: [], camera: { lens: 50, active: true } }),
      ],
    }
    const fetchMock = mockFetch({
      ...base(inv),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'GET /api/v1/projects/p1/renders': () => json(200, []),
      'POST /api/v1/projects/p1/renders': () => json(202, output({ id: 'r9', status: 'queued', url: null })),
    })
    renderWorkspace()
    const renderBtn = await screen.findByRole('button', { name: /Render/ })
    await waitFor(() => expect(renderBtn).toBeEnabled())
    await userEvent.click(renderBtn)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('radio', { name: /High/ }))
    await userEvent.selectOptions(within(dialog).getByLabelText('Size'), 'Square 1080×1080')
    await userEvent.selectOptions(within(dialog).getByLabelText('Camera'), 'Hero Camera')
    await userEvent.click(within(dialog).getByRole('button', { name: /Render v1/ }))
    await waitFor(() =>
      expect(bodyOf(fetchMock, 'POST', '/projects/p1/renders')).toEqual({
        engine: 'cycles', quality: 'high', width: 1080, height: 1080, camera_object_id: 'hero_cam',
        transparent_background: false, version_id: 'v1',
      }),
    )
  })

  it('edits a light and puts a wood texture on a material', async () => {
    const inv: SceneInventory = {
      ...deskInventory,
      objects: [
        ...deskInventory.objects,
        obj({ object_id: 'key_light', name: 'Key Light', type: 'LIGHT', material_ids: [], dimensions: [0, 0, 0],
          light: { light_type: 'area', color: '#FFD9A8', energy: 400, radius: 0.5, spot_angle: null } }),
      ],
    }
    const fetchMock = mockFetch({
      ...base(inv),
      'GET /api/v1/projects/p1/conversation': () => json(200, conversation()),
      'PATCH /api/v1/projects/p1/scene/objects/key_light': () => json(202, { job_id: 'j1', operation_batch_id: 'b', status: 'queued' }),
      'PATCH /api/v1/projects/p1/scene/materials/oak': () => json(202, { job_id: 'j2', operation_batch_id: 'b', status: 'queued' }),
      'GET /api/v1/jobs/j1/events': () => new Promise<Response>(() => undefined),
      'GET /api/v1/jobs/j2/events': () => new Promise<Response>(() => undefined),
    })
    renderWorkspace()
    await screen.findByText('Key Light')
    useWorkspaceStore.getState().select('key_light')
    expect(await screen.findByText('AREA LIGHT')).toBeInTheDocument()
    expect(screen.queryByLabelText('Dimensions X')).not.toBeInTheDocument() // lights have no size
    const power = screen.getByLabelText('Power')
    await userEvent.clear(power)
    await userEvent.type(power, '800{Enter}')
    await waitFor(() => expect(bodyOf(fetchMock, 'PATCH', '/scene/objects/key_light')).toEqual({ light_energy: 800 }))

    useWorkspaceStore.setState({ activeJob: null })
    useWorkspaceStore.getState().select('desk_top')
    await userEvent.click(screen.getByRole('tab', { name: /Materials/ }))
    await userEvent.selectOptions(await screen.findByLabelText('Texture'), 'wood')
    await waitFor(() => expect(bodyOf(fetchMock, 'PATCH', '/scene/materials/oak')).toEqual({ texture: 'wood' }))
  })

  it('parses download file names', () => {
    expect(dispositionName('attachment; filename="desk-v1-mm.stl"', 'x')).toBe('desk-v1-mm.stl')
    expect(dispositionName("attachment; filename*=UTF-8''desk%20v1.zip", 'x')).toBe('desk v1.zip')
    expect(dispositionName(null, 'fallback.glb')).toBe('fallback.glb')
  })
})
