import type { AgentJobSummary, AgentTrace } from '../types/trace'
import { apiRequest } from './api'

/** Owner view (LLM prompts included only for administrators). */
export const getTrace = (projectId: string, jobId: string) =>
  apiRequest<AgentTrace>(`/projects/${encodeURIComponent(projectId)}/agent-jobs/${encodeURIComponent(jobId)}/trace`)

/** Admin view of any user's turn. */
export const getAdminTrace = (jobId: string) =>
  apiRequest<AgentTrace>(`/admin/agent-jobs/${encodeURIComponent(jobId)}/trace`)

export const listAgentJobs = (q: string, status: string, offset = 0) => {
  const params = new URLSearchParams({ limit: '50', offset: String(offset) })
  if (q) params.set('q', q)
  if (status) params.set('status', status)
  return apiRequest<{ items: AgentJobSummary[]; total: number }>(`/admin/agent-jobs?${params}`)
}
