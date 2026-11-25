import type { ExportRequest, Output, RenderRequest, TextureImage } from '../types/scene'
import { apiFetch, apiRequest } from './api'

const p = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`
const id = (v: string) => encodeURIComponent(v)

export const createExport = (projectId: string, body: ExportRequest) =>
  apiRequest<Output>(`${p(projectId)}/exports`, { method: 'POST', body })
export const getExport = (exportId: string) => apiRequest<Output>(`/exports/${id(exportId)}`)
export const listExports = (projectId: string) => apiRequest<Output[]>(`${p(projectId)}/exports`)

export const createRender = (projectId: string, body: RenderRequest) =>
  apiRequest<Output>(`${p(projectId)}/renders`, { method: 'POST', body })
export const getRender = (renderId: string) => apiRequest<Output>(`/renders/${id(renderId)}`)
export const listRenders = (projectId: string) => apiRequest<Output[]>(`${p(projectId)}/renders`)

export const listTextures = (projectId: string) => apiRequest<TextureImage[]>(`${p(projectId)}/textures`)

/** Multipart upload; the server checks the image header (PNG/JPEG only) and size. */
export async function uploadTexture(projectId: string, file: File): Promise<TextureImage> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiFetch(`${p(projectId)}/textures`, { method: 'POST', body: form })
  return (await res.json()) as TextureImage
}

/** File name from the Content-Disposition header the API sends with downloads. */
export function dispositionName(header: string | null, fallback: string): string {
  const m = header?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  return m ? decodeURIComponent(m[1]) : fallback
}

export async function downloadOutput(url: string, fallback: string): Promise<void> {
  const res = await apiFetch(url.replace(/^\/api\/v1/, ''))
  const blob = await res.blob()
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = dispositionName(res.headers.get('content-disposition'), fallback)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
