import { create } from 'zustand'
import type { JobEvent } from '../types/scene'

/**
 * Workspace UI state only. Scene data (inventory, versions, GLB) is server state in TanStack Query
 * (features/modeling/hooks.ts), and so is the conversation; this store never holds a copy of either.
 */

export type InspectorTab = 'properties' | 'materials'
export type Tool = 'select' | 'move' | 'scale'
export type Shading = 'material' | 'solid' | 'wireframe'
export type Projection = 'perspective' | 'orthographic'
export type CameraPreset = 'iso' | 'front' | 'back' | 'left' | 'right' | 'top'

/** `agent`: a chat turn (planning + Blender); `edit`: a direct UI edit (Blender only). */
export type JobKind = 'agent' | 'edit'

export type ActiveJob = {
  id: string
  kind: JobKind
  label: string
  stage: string
  message: string
}

type ViewRequest = { kind: 'fit' | CameraPreset | 'zoom_in' | 'zoom_out'; nonce: number }

type WorkspaceState = {
  /** Primary selection (gizmo, properties panel): the most recently selected object. */
  selectedId: string | null
  /** Whole selection, primary last (Shift/Ctrl/Cmd-click adds or removes objects). */
  selectedIds: string[]
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
  /** Version to scroll to / highlight when the history dialog opens. */
  focusVersionId: string | null
  /** Chat message to scroll to / highlight ("show the conversation for this version"). */
  highlightMessageId: string | null
  activeJob: ActiveJob | null
  toast: string

  reset: () => void
  setPrompt: (prompt: string) => void
  select: (id: string | null, additive?: boolean) => void
  pruneSelection: (existing: Set<string>) => void
  setActiveTab: (tab: InspectorTab) => void
  setActiveTool: (tool: Tool) => void
  setShowChat: (show: boolean) => void
  setShowInspector: (show: boolean) => void
  toggleGrid: () => void
  toggleAxes: () => void
  setShading: (shading: Shading) => void
  toggleProjection: () => void
  requestView: (kind: ViewRequest['kind']) => void
  setVersionsOpen: (open: boolean, focusVersionId?: string | null) => void
  showMessage: (messageId: string | null) => void
  notify: (message: string) => void
  startJob: (id: string, label: string, kind?: JobKind) => void
  jobEvent: (event: JobEvent) => void
  endJob: (id: string) => void
}

let toastTimer: ReturnType<typeof setTimeout> | undefined

const freshState = () => ({
  selectedId: null as string | null,
  selectedIds: [] as string[],
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
  focusVersionId: null as string | null,
  highlightMessageId: null as string | null,
  activeJob: null as ActiveJob | null,
  toast: '',
})

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  ...freshState(),

  reset: () => set(freshState()),
  setPrompt: (prompt) => set({ prompt }),
  select: (id, additive = false) =>
    set((s) => {
      if (!additive || !id) return { selectedId: id, selectedIds: id ? [id] : [] }
      const selectedIds = s.selectedIds.includes(id) ? s.selectedIds.filter((x) => x !== id) : [...s.selectedIds, id]
      return { selectedIds, selectedId: selectedIds[selectedIds.length - 1] ?? null }
    }),
  pruneSelection: (existing) =>
    set((s) => {
      const selectedIds = s.selectedIds.filter((id) => existing.has(id))
      if (selectedIds.length === s.selectedIds.length) return s // same object: no update, no re-render
      return { selectedIds, selectedId: selectedIds[selectedIds.length - 1] ?? null }
    }),
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
  setVersionsOpen: (versionsOpen, focusVersionId = null) => set({ versionsOpen, focusVersionId }),
  showMessage: (highlightMessageId) => set({ highlightMessageId }),

  notify: (message) => {
    set({ toast: message })
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => set({ toast: '' }), 2600)
  },

  startJob: (id, label, kind = 'edit') =>
    set({
      activeJob: { id, kind, label, stage: 'queued', message: kind === 'agent' ? 'Reading your request…' : 'Waiting for Blender…' },
    }),

  jobEvent: (event) => {
    const job = get().activeJob
    if (!job || job.id !== event.job_id) return
    set({ activeJob: { ...job, stage: event.stage || job.stage, message: event.message || job.message } })
  },

  endJob: (id) => {
    if (get().activeJob?.id === id) set({ activeJob: null })
  },
}))
