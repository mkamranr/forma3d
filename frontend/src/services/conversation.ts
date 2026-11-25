import type { Conversation, PostMessageResponse, TextureImage } from '../types/scene'
import { apiFetch, apiRequest } from './api'

const c = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/conversation`

export const getConversation = (projectId: string) => apiRequest<Conversation>(c(projectId))

/** `selectedObjectIds`: the whole selection, primary object last. `attachmentIds`: uploaded reference photos. */
export const postMessage = (projectId: string, content: string, selectedObjectIds: string[], attachmentIds: string[] = []) =>
  apiRequest<PostMessageResponse>(`${c(projectId)}/messages`, {
    method: 'POST',
    body: {
      content,
      selected_object_id: selectedObjectIds[selectedObjectIds.length - 1] ?? null,
      selected_object_ids: selectedObjectIds,
      ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
    },
  })

/** Upload a photo of an object to attach to a message (image-to-3D). */
export async function uploadReference(projectId: string, file: File): Promise<TextureImage> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiFetch(`/projects/${encodeURIComponent(projectId)}/references`, { method: 'POST', body: form })
  return (await res.json()) as TextureImage
}

export const referenceUrl = (projectId: string, id: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(id)}`
