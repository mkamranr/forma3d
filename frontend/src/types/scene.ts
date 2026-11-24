export type Vec3 = [number, number, number]

export type PrimitiveKind = 'box' | 'sphere' | 'cylinder'

/** Client-side scene object (demo scene until the Blender pipeline is wired in Phase 3). */
export type ModelObject = {
  id: string
  name: string
  kind: PrimitiveKind
  position: Vec3
  scale: Vec3
  color: string
  visible: boolean
}

export type ChatMessage = { role: 'assistant' | 'user'; text: string; time: string }
