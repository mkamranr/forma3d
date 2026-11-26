/** In-app configuration (admin Configuration page). */
export type FieldType = 'string' | 'url' | 'int' | 'float' | 'bool' | 'enum' | 'secret'
export type SettingValue = string | number | boolean
export type SettingSource = 'app' | 'env' | 'default'

export type ConfigField = {
  key: string
  label: string
  help: string
  type: FieldType
  options: { value: string; label: string }[]
  min: number | null
  max: number | null
  /** Where the effective value comes from: saved in the app, the server's .env, or the built-in default. */
  source: SettingSource
  env: string
  value: SettingValue | null
  default?: SettingValue | null
  /** Secrets only: whether a value is set (it is never sent back). */
  is_set?: boolean
  default_is_set?: boolean
}

export type ConfigGroup = { id: string; label: string; help: string; fields: ConfigField[] }
export type AppConfig = { version: number; groups: ConfigGroup[] }

export type ClientConfig = {
  registration_open: boolean
  default_render_quality: 'draft' | 'standard' | 'high'
  default_render_size: string
  default_export_format: 'glb' | 'gltf' | 'obj' | 'stl'
  default_export_unit: 'm' | 'cm' | 'mm'
}

export type LlmCheck = { name: string; label: string; ok: boolean; detail: string; ms: number }
export type LlmTestResult = {
  ok: boolean
  provider?: string
  base_url?: string
  model?: string
  vision_model?: string
  models?: string[]
  checks: LlmCheck[]
  worker_id?: string
  error?: string
}

export type AdminUser = {
  id: string
  email: string
  display_name: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  projects: number
}
