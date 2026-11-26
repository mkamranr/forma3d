import { AlertTriangle, Bot, CheckCircle2, ChevronRight, CircleDashed, Clock, Hourglass, Loader2, Sparkles, XCircle } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Dialog } from '../../components/ui/Dialog'
import type { StepStatus, TraceCall, TraceStep } from '../../types/trace'
import { errorMessage } from '../modeling/hooks'
import { AgentGraphView } from './AgentGraphView'
import { duration } from './format'
import { isLive, useTrace } from './hooks'
import { LLM_NODES, NODE_INFO, nodeLabel } from './labels'

const STATUS_LABEL: Record<StepStatus, string> = {
  running: 'Running',
  succeeded: 'Done',
  failed: 'Problem',
  skipped: 'Skipped',
  waiting: 'Waiting',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
}

function StatusIcon({ status }: { status: StepStatus }) {
  const cls = 'shrink-0'
  switch (status) {
    case 'running':
      return <Loader2 size={14} className={`${cls} spin text-brand-500`} />
    case 'succeeded':
      return <CheckCircle2 size={14} className={`${cls} text-[#2f9a43]`} />
    case 'failed':
      return <XCircle size={14} className={`${cls} text-danger-500`} />
    case 'waiting':
      return <Hourglass size={14} className={`${cls} text-[#c58a12]`} />
    case 'skipped':
      return <CircleDashed size={14} className={`${cls} text-ink-300`} />
    default:
      return <AlertTriangle size={14} className={`${cls} text-ink-400`} />
  }
}

const HUMAN_KEYS: Record<string, string> = {
  message: 'Message',
  objects: 'Objects',
  selected: 'Selected',
  photos: 'Photos',
  summary: 'Summary',
  reason: 'Why',
  description: 'Saw',
  intent: 'Intent',
  task: 'Task',
  targets: 'Targets',
  reply: 'Reply',
  parts: 'Parts',
  materials: 'Materials',
  question: 'Question',
  operations: 'Operations',
  by_type: 'By type',
  result: 'Result',
  issues: 'Issues',
  passed: 'Passed',
  checks: 'Checks',
  revision: 'Revision',
  merged: 'Merged fix',
  version: 'Version',
  success: 'Success',
  error_code: 'Error code',
  error: 'Error',
  note: 'Note',
  attempt: 'Attempt',
  prompt_tokens: 'Prompt tokens',
  completion_tokens: 'Answer tokens',
}

function show(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (Array.isArray(v)) return v.map(show).join(', ')
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k} ×${show(x)}`).join(', ')
  return String(v)
}

function Facts({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length))
  if (!entries.length) return null
  return (
    <dl className="trace-facts">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt>{HUMAN_KEYS[k] ?? k.replace(/_/g, ' ')}</dt>
          <dd>{show(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

/** One-line summary of a step for the timeline row. */
export function stepSummary(s: TraceStep): string {
  const o = s.output as Record<string, unknown>
  if (s.error) return s.error
  if (o.error) return String(o.error)
  switch (s.node) {
    case 'load_context':
      return `${o.objects ?? 0} objects in the scene${o.photos ? `, ${o.photos} photo(s)` : ''}${o.selected ? `, ${o.selected} selected` : ''}`
    case 'conversation':
      return o.intent ? `Intent: ${o.intent}${o.task ? ` — ${o.task}` : ''}` : ''
    case 'planning':
      return o.question ? `Needs clarification: ${o.question}` : `${(o.parts as unknown[] | undefined)?.length ?? 0} part(s)${o.summary ? ` — ${o.summary}` : ''}`
    case 'modeling':
      return o.operations ? `${o.operations} operations validated` : ''
    case 'execute':
      return o.result === 'staged' ? `Built ${o.objects ?? ''} objects (staged)` : show(o.result)
    case 'quality':
      return o.passed ? `Passed ${o.checks} checks` : `${(o.issues as unknown[] | undefined)?.length ?? 0} issue(s) in ${o.checks ?? '?'} checks`
    case 'revise':
      return o.revision ? `Revision ${o.revision}` : ''
    case 'commit':
      return o.version ? `Saved as v${o.version}` : 'Saved'
    case 'vision':
      return s.status === 'skipped' ? 'No photo attached' : show(o.description).slice(0, 140)
    case 'ask':
      return show(o.question)
    case 'respond':
      return show(o.reply)
    default:
      return show(o.reply ?? o.summary ?? '')
  }
}

function CallView({ call, admin }: { call: TraceCall; admin: boolean }) {
  const tokens = [call.input.prompt_tokens, call.output.completion_tokens].some((t) => t)
  return (
    <div className={`trace-call${call.status === 'failed' ? ' failed' : ''}`}>
      <div className="trace-call-head">
        {call.kind === 'llm' ? <Sparkles size={12} /> : <Bot size={12} />}
        <span className="font-semibold">{call.kind === 'llm' ? `LLM call · ${call.agent}` : `${call.agent} agent`}</span>
        {call.model && <span className="trace-muted">{call.model}</span>}
        {call.input.attempt ? <span className="trace-muted">attempt {String(call.input.attempt)}</span> : null}
        {tokens && (
          <span className="trace-muted">
            {String(call.input.prompt_tokens ?? '?')} → {String(call.output.completion_tokens ?? '?')} tokens
          </span>
        )}
        <span className="ml-auto trace-muted">{duration(call.duration_ms)}</span>
      </div>
      {call.error && <p className="trace-error">{call.error}</p>}
      {call.kind === 'agent' && <Facts data={call.output} />}
      {Array.isArray(call.output.issues) && call.kind === 'llm' && (call.output.issues as unknown[]).length > 0 && (
        <ul className="trace-issues">{(call.output.issues as string[]).map((i) => <li key={i}>{i}</li>)}</ul>
      )}
      {admin && call.kind === 'llm' && (
        call.debug ? (
          <details className="trace-debug">
            <summary>Prompt &amp; response <span className="trace-badge">admin</span></summary>
            {call.debug.messages.map((m, i) => (
              <div key={i} className="trace-debug-msg">
                <div className="trace-debug-role">{m.role}{m.images ? ` · ${m.images} image(s)` : ''}</div>
                <pre>{m.content}</pre>
              </div>
            ))}
            <div className="trace-debug-msg">
              <div className="trace-debug-role">response</div>
              <pre>{call.debug.response ?? '(no answer — the call failed)'}</pre>
            </div>
          </details>
        ) : (
          <p className="trace-muted text-[0.8125rem]">Prompt not stored (disabled or past retention).</p>
        )
      )}
    </div>
  )
}

function StepRow({ step, open, onToggle, admin }: { step: TraceStep; open: boolean; onToggle: () => void; admin: boolean }) {
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (open) ref.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [open])
  const llm = step.calls.filter((c) => c.kind === 'llm')
  return (
    <li ref={ref} className={`trace-step ${step.status}${open ? ' open' : ''}`}>
      <button className="trace-step-head" onClick={onToggle} aria-expanded={open}>
        <span className="trace-seq">{step.seq}</span>
        <StatusIcon status={step.status} />
        <span className="trace-step-name">
          {LLM_NODES.has(step.node) && <span className="trace-ai">AI</span>}
          {nodeLabel(step.node)}
        </span>
        <span className="trace-step-summary">{stepSummary(step)}</span>
        {llm.length > 0 && <span className="trace-muted shrink-0">{llm.length} LLM</span>}
        <span className="trace-muted shrink-0 tabular-nums">{step.status === 'running' ? 'running…' : duration(step.duration_ms)}</span>
        <ChevronRight size={14} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="trace-step-body">
          <p className="trace-muted">{NODE_INFO[step.node]?.about ?? ''} <span className="trace-status">{STATUS_LABEL[step.status]}</span></p>
          {step.error && <p className="trace-error">{step.error}</p>}
          <Facts data={step.output} />
          {step.calls.map((c) => <CallView key={c.id} call={c} admin={admin} />)}
        </div>
      )}
    </li>
  )
}

/**
 * "How this was made": the LangGraph path one turn took (diagram) and what every step did (timeline).
 * `projectId` null opens the admin endpoint (any user's turn). Follows a running turn live.
 */
export default function AgentTraceDialog({
  open,
  onClose,
  projectId,
  jobId,
}: {
  open: boolean
  onClose: () => void
  projectId: string | null
  jobId: string | null
}) {
  const trace = useTrace(projectId, open ? jobId : null)
  const data = trace.data
  const live = isLive(data)
  const [openStep, setOpenStep] = useState<string | null>(null)
  const [node, setNode] = useState<string | null>(null)
  const steps = useMemo(() => data?.steps ?? [], [data])

  // Follow the newest step while running (unless the user picked one).
  const [pinned, setPinned] = useState(false)
  const lastId = steps[steps.length - 1]?.id
  useEffect(() => {
    if (live && !pinned && lastId) setOpenStep(lastId)
  }, [live, pinned, lastId])

  const pickNode = (n: string) => {
    setPinned(true)
    setNode(n)
    const visits = steps.filter((s) => s.node === n)
    if (visits.length) setOpenStep(visits[visits.length - 1].id)
  }
  const t = data?.totals

  return (
    <Dialog open={open} onClose={onClose} title="How this was made" size="xl"
      description={data?.message ? `“${data.message.content}”` : 'The agents that worked on this request, step by step'}>
      {trace.isLoading && <p className="text-[0.9062rem] text-ink-400">Loading the agent trace…</p>}
      {trace.isError && <p className="text-[0.9062rem] text-danger-600" role="alert">{errorMessage(trace.error)}</p>}
      {data && (
        <>
          <div className="trace-summary">
            {live ? (
              <span className="trace-pill live"><span className="trace-dot" /> Live</span>
            ) : (
              <span className={`trace-pill ${data.job.status}`}>{data.job.status}</span>
            )}
            {data.job.duration_ms !== null && <span><Clock size={12} /> {duration(data.job.duration_ms)}</span>}
            {t && <span>{t.steps} steps</span>}
            {t && <span>{t.llm_calls} LLM calls · {duration(t.llm_ms)}</span>}
            {t && t.prompt_tokens + t.completion_tokens > 0 && (
              <span>{(t.prompt_tokens + t.completion_tokens).toLocaleString()} tokens</span>
            )}
            {t && t.revisions > 0 && <span>{t.revisions} revision{t.revisions > 1 ? 's' : ''}</span>}
            {data.include_debug && <span className="trace-badge">admin view · prompts included</span>}
          </div>

          {steps.length === 0 && data.calls.length === 0 && (
            <p className="py-6 text-center text-[0.9062rem] text-ink-400">
              {live ? 'Waiting for an agent worker to pick up the request…' : 'No agent trace was recorded for this request.'}
            </p>
          )}

          {(steps.length > 0 || data.calls.length > 0) && (
            <div className="trace-layout">
              <div className="trace-graph-wrap">
                <AgentGraphView graph={data.graph} steps={steps} selected={node} onSelect={pickNode} live={live} />
                <div className="trace-legend">
                  <span><i className="ok" /> done</span>
                  <span><i className="bad" /> problem</span>
                  <span><i className="run" /> running</span>
                  <span><i className="skip" /> skipped</span>
                  <span><b className="trace-ai">AI</b> uses the LLM</span>
                </div>
              </div>
              <div className="trace-timeline">
                {node && (
                  <div className="trace-filter">
                    Showing: <b>{nodeLabel(node)}</b>
                    <button onClick={() => setNode(null)}>Show all</button>
                  </div>
                )}
                <ol>
                  {steps
                    .filter((s) => !node || s.node === node)
                    .map((s) => (
                      <StepRow
                        key={s.id}
                        step={s}
                        admin={data.include_debug}
                        open={openStep === s.id}
                        onToggle={() => {
                          setPinned(true)
                          setOpenStep(openStep === s.id ? null : s.id)
                        }}
                      />
                    ))}
                </ol>
                {data.calls.length > 0 && (
                  <div className="trace-step-body">
                    <p className="trace-muted">Agent calls recorded for this request:</p>
                    {data.calls.map((c) => <CallView key={c.id} call={c} admin={data.include_debug} />)}
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </Dialog>
  )
}
