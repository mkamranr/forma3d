import type { Project, ProjectList, ProjectListParams, ProjectStatus } from '../types/api'
import { apiRequest } from './api'

export function listProjects(params: ProjectListParams = {}): Promise<ProjectList> {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const qs = search.toString()
  return apiRequest<ProjectList>(`/projects${qs ? `?${qs}` : ''}`)
}

export const getProject = (id: string) => apiRequest<Project>(`/projects/${encodeURIComponent(id)}`)

export const createProject = (data: { name: string; description?: string }) =>
  apiRequest<Project>('/projects', { method: 'POST', body: data })

export const updateProject = (
  id: string,
  data: { name?: string; description?: string; status?: ProjectStatus },
) => apiRequest<Project>(`/projects/${encodeURIComponent(id)}`, { method: 'PUT', body: data })

export const deleteProject = (id: string) =>
  apiRequest<void>(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })

export const duplicateProject = (id: string) =>
  apiRequest<Project>(`/projects/${encodeURIComponent(id)}/duplicate`, { method: 'POST' })
