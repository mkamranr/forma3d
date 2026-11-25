import type {
  Job,
  MaterialPatch,
  ObjectPatch,
  PrimitiveKind,
  SceneState,
  SubmitResponse,
  Vec3,
  Version,
} from '../types/scene'
import { apiFetch, apiRequest } from './api'

const p = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`
const o = (objectId: string) => encodeURIComponent(objectId)

export const getScene = (projectId: string) => apiRequest<SceneState>(`${p(projectId)}/scene`)
export const listVersions = (projectId: string) => apiRequest<Version[]>(`${p(projectId)}/versions`)
export const getJob = (jobId: string) => apiRequest<Job>(`/jobs/${encodeURIComponent(jobId)}`)
export const cancelJob = (jobId: string) => apiRequest<Job>(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })

export const createStarterScene = (projectId: string) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/starter`, { method: 'POST' })

export const addObject = (projectId: string, kind: PrimitiveKind, location?: Vec3) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/objects`, {
    method: 'POST',
    body: location ? { kind, location } : { kind },
  })

export const editObject = (projectId: string, objectId: string, patch: ObjectPatch) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/objects/${o(objectId)}`, { method: 'PATCH', body: patch })

export const deleteObject = (projectId: string, objectId: string, recursive = false) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/objects/${o(objectId)}${recursive ? '?recursive=true' : ''}`, {
    method: 'DELETE',
  })

export const editMaterial = (projectId: string, materialId: string, patch: MaterialPatch) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/materials/${o(materialId)}`, { method: 'PATCH', body: patch })

export const giveObjectMaterial = (projectId: string, objectId: string, patch: MaterialPatch) =>
  apiRequest<SubmitResponse>(`${p(projectId)}/scene/objects/${o(objectId)}/material`, { method: 'POST', body: patch })

export const restoreVersion = (projectId: string, versionId: string) =>
  apiRequest<unknown>(`${p(projectId)}/versions/${encodeURIComponent(versionId)}/restore`, { method: 'POST' })

/** Authenticated binary download (asset URLs are absolute API paths returned by the backend). */
export async function fetchAsset(url: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const res = await apiFetch(url.replace(/^\/api\/v1/, ''), { signal })
  return res.arrayBuffer()
}

export async function downloadAsset(url: string, filename: string): Promise<void> {
  const res = await apiFetch(url.replace(/^\/api\/v1/, ''))
  const blob = await res.blob()
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
