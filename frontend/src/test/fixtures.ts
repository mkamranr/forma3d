import type { SceneInventory, SceneObject, SceneState, Version } from '../types/scene'

export const obj = (o: Partial<SceneObject> & Pick<SceneObject, 'object_id' | 'name'>): SceneObject => ({
  type: 'MESH',
  component_type: null,
  parent_id: null,
  collection_id: 'desk',
  location: [0, 0, 0],
  rotation: [0, 0, 0],
  scale: [1, 1, 1],
  dimensions: [0.05, 0.05, 0.72],
  visible: true,
  material_ids: ['graphite'],
  modifiers: [],
  mesh: { vertices: 8, edges: 12, faces: 6, triangles: 12 },
  created_version: 1,
  last_modified_version: 1,
  ...o,
})

export const deskInventory: SceneInventory = {
  objects: [
    obj({ object_id: 'desk_top', name: 'Desk Top', component_type: 'tabletop', location: [0, 0, 0.74], dimensions: [1.6, 0.8, 0.04], material_ids: ['oak'] }),
    obj({ object_id: 'leg_fl', name: 'Leg Front Left', component_type: 'leg', parent_id: 'desk_top', location: [-0.75, -0.35, 0.36] }),
    obj({ object_id: 'leg_fr', name: 'Leg Front Right', component_type: 'leg', parent_id: 'desk_top', location: [0.75, -0.35, 0.36] }),
  ],
  materials: [
    { material_id: 'oak', name: 'Oak', base_color: '#C99563', metallic: 0, roughness: 0.6, alpha: 1 },
    { material_id: 'graphite', name: 'Graphite', base_color: '#424953', metallic: 0.6, roughness: 0.4, alpha: 1 },
  ],
  collections: [{ collection_id: 'desk', name: 'Desk', parent_id: null }],
}

export const version = (n: number, extra: Partial<Version> = {}): Version => ({
  id: `v${n}`,
  project_id: 'p1',
  parent_version_id: n > 1 ? `v${n - 1}` : null,
  version_number: n,
  summary: n === 1 ? 'Create a modern wooden desk' : `Change ${n}`,
  status: 'committed',
  operation_batch_id: `b${n}`,
  message_id: null,
  is_current: false,
  object_count: 3,
  assets: [
    { kind: 'glb', size: 10, sha256: 'x', url: `/api/v1/projects/p1/versions/v${n}/assets/glb` },
    { kind: 'thumbnail', size: 10, sha256: 'y', url: `/api/v1/projects/p1/versions/v${n}/assets/thumbnail` },
  ],
  created_at: '2026-11-24T10:00:00Z',
  ...extra,
})

export const sceneState = (n: number | null, inventory = deskInventory): SceneState => ({
  project_id: 'p1',
  version: n ? version(n, { is_current: true }) : null,
  inventory: n ? inventory : { objects: [], materials: [], collections: [] },
  active_job: null,
})

export const project = (extra = {}) => ({
  id: 'p1',
  name: 'Desk',
  description: '',
  status: 'active',
  current_version_id: 'v1',
  version_count: 1,
  thumbnail_url: null,
  created_at: '2026-11-24T10:00:00Z',
  updated_at: '2026-11-24T10:00:00Z',
  ...extra,
})

/** A Response whose body streams the given SSE frames. */
export function sseResponse(frames: { id: string; type: string; data: Record<string, unknown> }[]) {
  const text = frames
    .map((f) => `id: ${f.id}\nevent: ${f.type}\ndata: ${JSON.stringify({ type: f.type, ...f.data })}\n\n`)
    .join('')
  const bytes = new TextEncoder().encode(`retry: 3000\n\n: ping\n\n${text}`)
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      // split mid-frame to exercise the incremental parser
      const mid = Math.floor(bytes.length / 2)
      controller.enqueue(bytes.slice(0, mid))
      controller.enqueue(bytes.slice(mid))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}
