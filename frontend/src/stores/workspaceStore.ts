import { create } from 'zustand'
import type { ChatMessage, JobEvent } from '../types/scene'
import { clockTime } from '../utils/format'

/**
 * Workspace UI state only. Scene data (inventory, versions, GLB) is server state in TanStack Query
 * (features/modeling/hooks.ts); this store never holds a copy of the scene.
 */

export type InspectorTab = 'properties' | 'materials'
export type Tool = 'select' | 'move' | 'scale'
export type Shading = 'material' | 'solid' | 'wireframe'
export type Projection = 'perspective' | 'orthographic'
export type CameraPreset = 'iso' | 'front' | 'back' | 'left' | 'right' | 'top'

export type ActiveJob = {
  id: string
  label: string
  stage: string
  message: string
}

type ViewRequest = { kind: 'fit' | CameraPreset | 'zoom_in' | 'zoom_out'; nonce: number }

type WorkspaceState = {
  selectedId: string | null
  messages: ChatMessage[]
  prompt: string
  activeTab: InspectorTab
  activeTool: Tool
  showChat: boolean
  showInspector: boolean
  showGrid: boolean
  showAxes: boolean
  shading: Shading
  projection: Projection
  viewRequest: ViewRequest
  versionsOpen: boolean
  activeJob: ActiveJob | null
  toast: string

  reset: () => void
  setPrompt: (prompt: string) => void
  select: (id: string | null) => void
  setActiveTab: (tab: InspectorTab) => void
  setActiveTool: (tool: Tool) => void
  setShowChat: (show: boolean) => void
  setShowInspector: (show: boolean) => void
  toggleGrid: () => void
  toggleAxes: () => void
  setShading: (shading: Shading) => void
  toggleProjection: () => void
  requestView: (kind: ViewRequest['kind']) => void
  setVersionsOpen: (open: boolean) => void
  notify: (message: string) => void
  say: (role: ChatMessage['role'], text: string) => void
  startJob: (id: string, label: string) => void
  jobEvent: (event: JobEvent) => void
  endJob: (id: string) => void
}

let toastTimer: ReturnType<typeof setTimeout> | undefined

const welcome = (): ChatMessage[] => [
  {
    role: 'assistant',
    text: 'Your workspace is ready. Every change is built by Blender and saved as a new version.',
    time: clockTime(),
  },
  {
    role: 'assistant',
    text: 'The conversational AI arrives with the LLM integration. For now I understand simple commands like “add a sphere”, “make the tabletop wider” or “make it blue”.',
    time: clockTime(),
  },
]

const freshState = () => ({
  selectedId: null as string | null,
  messages: welcome(),
  prompt: '',
  activeTab: 'properties' as InspectorTab,
  activeTool: 'select' as Tool,
  showChat: true,
  showInspector: true,
  showGrid: true,
  showAxes: false,
  shading: 'material' as Shading,
  projection: 'perspective' as Projection,
  viewRequest: { kind: 'fit', nonce: 0 } as ViewRequest,
  versionsOpen: false,
  activeJob: null as ActiveJob | null,
  toast: '',
})

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  ...freshState(),

  reset: () => set(freshState()),
  setPrompt: (prompt) => set({ prompt }),
  select: (selectedId) => set({ selectedId }),
  setActiveTab: (activeTab) => set({ activeTab }),
  setActiveTool: (activeTool) => set({ activeTool }),
  setShowChat: (showChat) => set({ showChat }),
  setShowInspector: (showInspector) => set({ showInspector }),
  toggleGrid: () => set((s) => ({ showGrid: !s.showGrid })),
  toggleAxes: () => set((s) => ({ showAxes: !s.showAxes })),
  setShading: (shading) => set({ shading }),
  toggleProjection: () =>
    set((s) => ({ projection: s.projection === 'perspective' ? 'orthographic' : 'perspective' })),
  requestView: (kind) => set((s) => ({ viewRequest: { kind, nonce: s.viewRequest.nonce + 1 } })),
  setVersionsOpen: (versionsOpen) => set({ versionsOpen }),

  notify: (message) => {
    set({ toast: message })
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => set({ toast: '' }), 2600)
  },

  say: (role, text) => set((s) => ({ messages: [...s.messages, { role, text, time: clockTime() }] })),

  startJob: (id, label) => set({ activeJob: { id, label, stage: 'queued', message: 'Waiting for Blender…' } }),

  jobEvent: (event) => {
    const job = get().activeJob
    if (!job || job.id !== event.job_id) return
    set({ activeJob: { ...job, stage: event.stage || job.stage, message: event.message || job.message } })
  },

  endJob: (id) => {
    if (get().activeJob?.id === id) set({ activeJob: null })
  },
}))
