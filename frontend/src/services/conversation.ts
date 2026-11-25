import type { Conversation, PostMessageResponse } from '../types/scene'
import { apiRequest } from './api'

const c = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/conversation`

export const getConversation = (projectId: string) => apiRequest<Conversation>(c(projectId))

/** `selectedObjectIds`: the whole selection, primary object last. */
export const postMessage = (projectId: string, content: string, selectedObjectIds: string[]) =>
  apiRequest<PostMessageResponse>(`${c(projectId)}/messages`, {
    method: 'POST',
    body: {
      content,
      selected_object_id: selectedObjectIds[selectedObjectIds.length - 1] ?? null,
      selected_object_ids: selectedObjectIds,
    },
  })
