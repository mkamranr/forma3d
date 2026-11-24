import { create } from 'zustand'
import { interpretDemoCommand } from '../features/modeling/demoCommands'
import type { ChatMessage, ModelObject, PrimitiveKind } from '../types/scene'
import { clockTime } from '../utils/format'

/**
 * Workspace UI state. In Phase 1 the scene is the local demo scene from the prototype;
 * Phase 3 replaces `objects` with the committed Blender scene inventory from the API.
 */

export const initialObjects: ModelObject[] = [
  { id: 'desk-top', name: 'Tabletop', kind: 'box', position: [0, 1.35, 0], scale: [3.5, 0.22, 1.8], color: '#C99563', visible: true },
  { id: 'leg-1', name: 'Leg · Front left', kind: 'box', position: [-1.48, 0.65, 0.68], scale: [0.18, 1.3, 0.18], color: '#5D6470', visible: true },
  { id: 'leg-2', name: 'Leg · Front right', kind: 'box', position: [1.48, 0.65, 0.68], scale: [0.18, 1.3, 0.18], color: '#5D6470', visible: true },
  { id: 'leg-3', name: 'Leg · Back left', kind: 'box', position: [-1.48, 0.65, -0.68], scale: [0.18, 1.3, 0.18], color: '#5D6470', visible: true },
  { id: 'leg-4', name: 'Leg · Back right', kind: 'box', position: [1.48, 0.65, -0.68], scale: [0.18, 1.3, 0.18], color: '#5D6470', visible: true },
]

const initialMessages = (): ChatMessage[] => [
  { role: 'assistant', text: 'Your workspace is ready. I’ve created a clean, modern desk as a starting point. Tell me what you’d like to change.', time: clockTime() },
  { role: 'assistant', text: 'Try asking me to add a sphere, change the tabletop colour, or make the desk wider.', time: clockTime() },
]

export type InspectorTab = 'properties' | 'materials'
export type Tool = 'select' | 'move' | 'scale'

type WorkspaceState = {
  objects: ModelObject[]
  selectedId: string | null
  messages: ChatMessage[]
  prompt: string
  activeTab: InspectorTab
  activeTool: Tool
  showChat: boolean
  showInspector: boolean
  showGrid: boolean
  toast: string
  version: number
  history: string[]

  reset: () => void
  setPrompt: (prompt: string) => void
  select: (id: string | null) => void
  setActiveTab: (tab: InspectorTab) => void
  setActiveTool: (tool: Tool) => void
  setShowChat: (show: boolean) => void
  setShowInspector: (show: boolean) => void
  toggleGrid: () => void
  notify: (message: string) => void
  updateSelected: (patch: Partial<ModelObject>) => void
  toggleVisibility: (id: string) => void
  addPrimitive: (kind: PrimitiveKind) => void
  saveSnapshot: () => void
  resetScene: () => void
  sendPrompt: () => void
}

const PRIMITIVE_NAMES: Record<PrimitiveKind, string> = { box: 'New cube', sphere: 'New sphere', cylinder: 'New cylinder' }

let toastTimer: ReturnType<typeof setTimeout> | undefined

const freshState = () => ({
  objects: initialObjects,
  selectedId: 'desk-top' as string | null,
  messages: initialMessages(),
  prompt: '',
  activeTab: 'properties' as InspectorTab,
  activeTool: 'select' as Tool,
  showChat: true,
  showInspector: true,
  showGrid: true,
  toast: '',
  version: 3,
  history: ['Desk created', 'Tabletop material refined', 'Leg proportions adjusted'],
})

export const useWorkspaceStore = create<WorkspaceState>((set, get) => {
  const commit = (entry: string) => set((s) => ({ history: [...s.history, entry], version: s.version + 1 }))

  return {
    ...freshState(),

    reset: () => set(freshState()),
    setPrompt: (prompt) => set({ prompt }),
    select: (selectedId) => set({ selectedId }),
    setActiveTab: (activeTab) => set({ activeTab }),
    setActiveTool: (activeTool) => set({ activeTool }),
    setShowChat: (showChat) => set({ showChat }),
    setShowInspector: (showInspector) => set({ showInspector }),
    toggleGrid: () => set((s) => ({ showGrid: !s.showGrid })),

    notify: (message) => {
      set({ toast: message })
      clearTimeout(toastTimer)
      toastTimer = setTimeout(() => set({ toast: '' }), 2200)
    },

    updateSelected: (patch) => {
      const { selectedId } = get()
      if (!selectedId) return
      set((s) => ({ objects: s.objects.map((o) => (o.id === selectedId ? { ...o, ...patch } : o)) }))
    },

    toggleVisibility: (id) =>
      set((s) => ({ objects: s.objects.map((o) => (o.id === id ? { ...o, visible: !o.visible } : o)) })),

    addPrimitive: (kind) => {
      const id = `${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      const name = PRIMITIVE_NAMES[kind]
      set((s) => ({
        objects: [
          ...s.objects,
          { id, name, kind, position: [0, 1.8, 0], scale: kind === 'box' ? [0.8, 0.8, 0.8] : [1, 1, 1], color: '#8B9CFB', visible: true },
        ],
        selectedId: id,
      }))
      commit(`${name} added`)
      get().notify(`${name} added to scene`)
    },

    saveSnapshot: () => {
      commit('Manual snapshot saved')
      get().notify('Version snapshot saved')
    },

    resetScene: () => {
      set({ objects: initialObjects, selectedId: 'desk-top' })
      commit('Scene reset')
      get().notify('Scene restored to starter desk')
    },

    sendPrompt: () => {
      const text = get().prompt.trim()
      if (!text) return
      set((s) => ({ messages: [...s.messages, { role: 'user', text, time: clockTime() }], prompt: '' }))

      const { action, reply } = interpretDemoCommand(text, get().objects, get().selectedId)
      switch (action.type) {
        case 'add':
          get().addPrimitive(action.kind)
          break
        case 'color':
          if (action.targetId) {
            set((s) => ({ objects: s.objects.map((o) => (o.id === action.targetId ? { ...o, color: action.color } : o)) }))
          }
          commit('Tabletop finish updated')
          break
        case 'widen':
          if (action.targetId) {
            set((s) => ({
              objects: s.objects.map((o) =>
                o.id === action.targetId ? { ...o, scale: [o.scale[0] * action.factor, o.scale[1], o.scale[2]] } : o,
              ),
            }))
          }
          commit('Tabletop widened')
          break
        case 'remove':
          set((s) => ({ objects: s.objects.filter((o) => o.id !== action.targetId), selectedId: null }))
          commit('Object removed')
          break
        case 'none':
          break
      }
      setTimeout(() => set((s) => ({ messages: [...s.messages, { role: 'assistant', text: reply, time: clockTime() }] })), 450)
    },
  }
})
