export type User = {
  id: string
  email: string
  display_name: string
  is_active: boolean
  created_at: string
}

export type TokenResponse = {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export type ProjectStatus = 'active' | 'archived'
export type ProjectStatusFilter = ProjectStatus | 'all'
export type ProjectSort = 'updated_desc' | 'updated_asc' | 'name_asc' | 'name_desc' | 'created_desc'

export type Project = {
  id: string
  name: string
  description: string
  status: ProjectStatus
  current_version_id: string | null
  version_count: number
  thumbnail_url: string | null
  created_at: string
  updated_at: string
}

export type ProjectList = { items: Project[]; total: number }

export type ProjectListParams = {
  q?: string
  status?: ProjectStatusFilter
  sort?: ProjectSort
  limit?: number
  offset?: number
}
