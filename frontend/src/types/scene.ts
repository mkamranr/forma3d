/** Blender-space vector: meters, Z up. Convert with utils/coords before using in three.js. */
export type Vec3 = [number, number, number]

/** Mirrors forma3d_shared.schemas.scene_object (the committed scene inventory). */
export type SceneObject = {
  object_id: string
  name: string
  type: string
  component_type: string | null
  parent_id: string | null
  collection_id: string | null
  location: Vec3 // world space
  rotation: Vec3 // local XYZ Euler, degrees
  scale: Vec3
  dimensions: Vec3
  visible: boolean
  material_ids: string[]
  modifiers: { name: string; type: string; params: Record<string, unknown> }[]
  mesh: { vertices: number; edges: number; faces: number; triangles: number }
  /** Cameras only. */
  camera?: { lens: number; active: boolean } | null
  /** Lights only. */
  light?: { light_type: LightType; color: string; energy: number; radius: number; spot_angle: number | null } | null
  created_version: number | null
  last_modified_version: number | null
}

export type LightType = 'point' | 'sun' | 'spot' | 'area'

export const TEXTURE_PATTERNS = ['wood', 'marble', 'noise', 'checker', 'brick', 'fabric', 'concrete', 'brushed_metal'] as const
export type TexturePattern = (typeof TEXTURE_PATTERNS)[number]

export type MaterialTexture = {
  pattern: TexturePattern | null
  image_id: string | null
  color_a: string | null
  color_b: string | null
  scale: number
  rotation: number
}

export type SceneMaterial = {
  material_id: string
  name: string
  base_color: string
  metallic: number
  roughness: number
  alpha: number
  emission_color?: string | null
  emission_strength?: number
  transmission?: number
  ior?: number
  texture?: MaterialTexture | null
}

export type SceneCollection = { collection_id: string; name: string; parent_id: string | null }

export type SceneInventory = {
  objects: SceneObject[]
  materials: SceneMaterial[]
  collections: SceneCollection[]
  world?: { color: string; strength: number } | null
}

export type AssetKind = 'glb' | 'thumbnail' | 'blend' | 'inventory' | 'operations'
export type Asset = { kind: AssetKind; size: number; sha256: string; url: string }

export type Version = {
  id: string
  project_id: string
  parent_version_id: string | null
  version_number: number
  summary: string
  status: string
  operation_batch_id: string | null
  message_id: string | null
  is_current: boolean
  object_count: number
  assets: Asset[]
  created_at: string
  /** `chat`: produced by an assistant turn (`prompt` is the user's message); `edit`: a direct UI edit. */
  source?: 'chat' | 'edit'
  prompt?: string | null
}

export type FieldChange = { before: unknown; after: unknown }
export type ObjectChange = { object_id: string; name: string; fields: Record<string, FieldChange> }
export type MaterialChange = { material_id: string; name: string; fields: Record<string, FieldChange> }
export type VersionCompare = {
  base: { id: string; version_number: number; summary: string }
  target: { id: string; version_number: number; summary: string }
  objects: {
    added: { object_id: string; name: string }[]
    removed: { object_id: string; name: string }[]
    changed: ObjectChange[]
    unchanged: number
  }
  materials: {
    added: { material_id: string; name: string }[]
    removed: { material_id: string; name: string }[]
    changed: MaterialChange[]
  }
}

export type HistoryStep = { version_id: string; version_number: number }

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type Job = {
  id: string
  project_id: string
  job_type: string
  status: JobStatus
  progress: { stage?: string; message?: string; [k: string]: unknown }
  error_message: string | null
  error_code: string | null
  failed_operation_id: string | null
  operation_batch_id: string | null
  version_id: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type JobEvent = {
  type: string
  job_id: string
  stage: string
  message: string
  data: Record<string, unknown>
  timestamp?: string
}

export type SceneState = {
  project_id: string
  version: Version | null
  inventory: SceneInventory
  active_job: Job | null
}

export type SubmitResponse = { job_id: string; operation_batch_id: string; status: JobStatus }

export type PrimitiveKind = 'cube' | 'sphere' | 'cylinder' | 'cone' | 'plane' | 'torus' | 'light' | 'camera'

export type ObjectPatch = Partial<{
  name: string
  location: Vec3
  rotation: Vec3
  dimensions: Vec3
  visible: boolean
  material_id: string
  light_energy: number
  light_color: string
  camera_lens: number
  active_camera: boolean
}>

export type MaterialPatch = Partial<
  Pick<SceneMaterial, 'name' | 'base_color' | 'metallic' | 'roughness' | 'alpha' | 'emission_strength' | 'transmission'> & {
    /** procedural pattern, or '' to remove the texture */
    texture: TexturePattern | ''
    texture_image_id: string
    texture_scale: number
  }
>

export type ExportFormat = 'glb' | 'gltf' | 'obj' | 'stl'
export type Unit = 'm' | 'cm' | 'mm'
export type RenderQuality = 'draft' | 'standard' | 'high'
export type RenderEngine = 'cycles' | 'eevee'

/** A render or export job and, once it succeeded, its file (spec §13.8). */
export type Output = {
  id: string
  project_id: string
  status: JobStatus
  progress: { stage?: string; message?: string }
  error_message: string | null
  version_id: string | null
  version_number: number | null
  settings: Record<string, unknown>
  details: Record<string, unknown>
  size: number | null
  url: string | null
  created_at: string
  completed_at: string | null
}

export type RenderRequest = {
  engine: RenderEngine
  quality: RenderQuality
  width: number
  height: number
  camera_object_id?: string | null
  transparent_background?: boolean
  version_id?: string
}

export type ExportRequest = { format: ExportFormat; unit?: Unit; include_hidden?: boolean; version_id?: string }

export type TextureImage = {
  id: string
  name: string
  format: string
  width: number
  height: number
  size: number
  url: string
  created_at: string
}

export type MessageMetadata = {
  version_id?: string
  version_number?: number
  action?: 'export' | 'render'
  export_version_id?: string
  export_version_number?: number
  export_format?: ExportFormat
  render_quality?: RenderQuality
  /** The export / render job the assistant started for this message. */
  output_job_id?: string
  output_error?: string
  error_code?: string
  agent_job_id?: string
  selected_object_id?: string
  selected_object_ids?: string[]
  /** Set when the assistant restored an earlier version (undo / "go back to version N"). */
  restored?: number
  /** Quality Agent verdict for an agent-built version. */
  quality?: { passed: boolean; checks: number; revisions: number; warnings: string[] }
}

export type ConversationMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  intent: 'modeling' | 'question' | 'clarify' | 'export' | 'render' | 'undo' | 'redo' | 'restore' | null
  status: string
  metadata: MessageMetadata
  created_at: string
}

export type Conversation = { id: string; project_id: string; messages: ConversationMessage[] }

export type PostMessageResponse = { message: ConversationMessage; job_id: string; status: string }
