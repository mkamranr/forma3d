/** Agent trace of one assistant turn ("How this was made"). */
export type StepStatus = 'running' | 'succeeded' | 'failed' | 'skipped' | 'waiting' | 'cancelled' | 'interrupted'

export type TraceCall = {
  id: string
  kind: 'llm' | 'agent'
  agent: string
  model: string | null
  status: StepStatus
  duration_ms: number | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  error: string | null
  created_at: string | null
  /** Administrators only: the exact prompt and raw answer (absent for others, null once purged). */
  debug?: { messages: { role: string; content: string; images: number }[]; response: string | null } | null
}

export type TraceStep = {
  id: string
  node: string
  seq: number
  status: StepStatus
  duration_ms: number | null
  started_at: string | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  error: string | null
  calls: TraceCall[]
}

export type AgentGraph = {
  nodes: { id: string }[]
  edges: { source: string; target: string; conditional: boolean; label: string }[]
}

export type AgentTrace = {
  job: {
    id: string
    project_id: string
    status: string
    created_at: string | null
    started_at: string | null
    completed_at: string | null
    duration_ms: number | null
    error_message: string | null
  }
  message: { id: string; content: string } | null
  graph: AgentGraph | null
  steps: TraceStep[]
  /** Rows recorded before step tracing existed (older turns). */
  calls: TraceCall[]
  totals: { steps: number; llm_calls: number; llm_ms: number; prompt_tokens: number; completion_tokens: number; revisions: number }
  include_debug: boolean
}

export type AgentJobSummary = {
  job_id: string
  project_id: string
  project_name: string
  user_email: string
  message: string
  status: string
  created_at: string | null
  duration_ms: number | null
  llm_calls: number
  tokens: number
  revisions: number
  steps: number
}
