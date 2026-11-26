import { Search, Workflow } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { relativeTime } from '../../utils/format'
import { errorMessage } from '../modeling/hooks'
import { duration } from './format'
import { useAgentJobs } from './hooks'

const AgentTraceDialog = lazy(() => import('./AgentTraceDialog'))

const CONTROL =
  'h-9 rounded-lg border border-line bg-white px-3 text-[0.9062rem] text-ink-900 outline-none focus:border-brand-400 focus:ring-3 focus:ring-brand-100'

const STATUS_CLS: Record<string, string> = {
  succeeded: 'bg-[#e6f5ea] text-[#1f6b35]',
  failed: 'bg-[#fdeceb] text-[#a1302c]',
  cancelled: 'bg-[#f0f1f5] text-[#5d6470]',
  running: 'bg-[#eef0ff] text-[#4f46bd]',
  queued: 'bg-[#f0f1f5] text-[#5d6470]',
}

/** Admin: recent assistant turns of all users; open any one's agent trace (with LLM prompts). */
export function AgentActivityPanel() {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [status, setStatus] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const close = useCallback(() => setJobId(null), [])
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])
  const jobs = useAgentJobs(term, status)

  return (
    <section className="rounded-xl border border-line bg-white" aria-label="Agent activity">
      <div className="flex flex-wrap items-center gap-3 border-b border-[#eff0f4] px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-[0.9062rem] font-semibold text-ink-900">Agent activity</h3>
          <p className="m-0 text-[0.8438rem] text-ink-400">
            Every request users sent to the assistant. Open one to see the agent graph, each step and the exact LLM prompts.
          </p>
        </div>
        <div className="relative w-60 max-w-full">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
          <input className={`${CONTROL} w-full pl-8`} placeholder="User or project" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search agent activity" />
        </div>
        <select className={CONTROL} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      {jobs.isError ? (
        <p className="p-4 text-[0.8438rem] text-danger-600">{errorMessage(jobs.error)}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[0.8438rem]">
            <thead>
              <tr className="text-left text-[0.8125rem] uppercase tracking-wide text-ink-300">
                <th className="px-4 py-2 font-semibold">Request</th>
                <th className="px-2 py-2 font-semibold">User · project</th>
                <th className="px-2 py-2 font-semibold">Status</th>
                <th className="px-2 py-2 font-semibold">Steps</th>
                <th className="px-2 py-2 font-semibold">LLM</th>
                <th className="px-2 py-2 font-semibold">Time</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {(jobs.data?.items ?? []).map((j) => (
                <tr key={j.job_id} className="border-t border-[#eff0f4] align-top">
                  <td className="max-w-[320px] px-4 py-2.5">
                    <div className="line-clamp-2 text-ink-900">{j.message || <span className="text-ink-300">(no text)</span>}</div>
                    <div className="text-[0.8125rem] text-ink-300">{j.created_at ? relativeTime(j.created_at) : ''}</div>
                  </td>
                  <td className="px-2 py-2.5">
                    <div className="text-ink-700">{j.user_email}</div>
                    <div className="text-ink-400">{j.project_name}</div>
                  </td>
                  <td className="px-2 py-2.5">
                    <span className={`rounded-full px-2 py-0.5 text-[0.8125rem] font-semibold ${STATUS_CLS[j.status] ?? ''}`}>{j.status}</span>
                  </td>
                  <td className="px-2 py-2.5 tabular-nums">
                    {j.steps}
                    {j.revisions > 0 && <span className="text-ink-400"> · {j.revisions} rev.</span>}
                  </td>
                  <td className="px-2 py-2.5 tabular-nums">
                    {j.llm_calls}
                    {j.tokens > 0 && <span className="text-ink-400"> · {j.tokens.toLocaleString()} tok</span>}
                  </td>
                  <td className="px-2 py-2.5 tabular-nums">{duration(j.duration_ms)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      className="inline-flex cursor-pointer items-center gap-1 rounded-md border-0 bg-[#f3f1ff] px-2 py-1 text-[0.8125rem] font-semibold text-brand-700 hover:bg-[#e9e6ff]"
                      onClick={() => setJobId(j.job_id)}
                      aria-label={`Open trace: ${j.message}`}
                    >
                      <Workflow size={12} /> Trace
                    </button>
                  </td>
                </tr>
              ))}
              {jobs.data && jobs.data.items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-ink-400">No requests yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {jobs.data && jobs.data.total > jobs.data.items.length && (
        <p className="m-0 border-t border-[#eff0f4] px-4 py-2 text-[0.8125rem] text-ink-400">
          Showing the latest {jobs.data.items.length} of {jobs.data.total}. Search to narrow the list.
        </p>
      )}
      {jobId && (
        <Suspense fallback={null}>
          <AgentTraceDialog open onClose={close} projectId={null} jobId={jobId} />
        </Suspense>
      )}
    </section>
  )
}
