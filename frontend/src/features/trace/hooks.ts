import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getAdminTrace, getTrace, listAgentJobs } from '../../services/trace'
import type { AgentTrace } from '../../types/trace'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

/** True while the turn (or one of its steps) is still going, so the view keeps following it live. */
export function isLive(trace: AgentTrace | undefined) {
  if (!trace) return true
  return !TERMINAL.has(trace.job.status) || trace.steps.some((s) => s.status === 'running')
}

/** `projectId` null = admin endpoint (any user's turn). Polls every 1.5 s while the turn is running. */
export function useTrace(projectId: string | null, jobId: string | null) {
  return useQuery({
    queryKey: ['trace', projectId ?? 'admin', jobId],
    queryFn: () => (projectId ? getTrace(projectId, jobId!) : getAdminTrace(jobId!)),
    enabled: !!jobId,
    refetchInterval: (q) => (isLive(q.state.data) && !q.state.error ? 1500 : false),
  })
}

export function useAgentJobs(q: string, status: string) {
  return useQuery({
    queryKey: ['trace', 'admin-list', q, status],
    queryFn: () => listAgentJobs(q, status),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  })
}
