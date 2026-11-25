import type { Conversation, PostMessageResponse } from '../types/scene'
import { apiRequest } from './api'

const c = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/conversation`

export const getConversation = (projectId: string) => apiRequest<Conversation>(c(projectId))

export const postMessage = (projectId: string, content: string, selectedObjectId: string | null) =>
  apiRequest<PostMessageResponse>(`${c(projectId)}/messages`, {
    method: 'POST',
    body: { content, selected_object_id: selectedObjectId },
  })
