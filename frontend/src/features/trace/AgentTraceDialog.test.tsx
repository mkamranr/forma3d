import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/authStore'
import { json, mockFetch, renderWithProviders } from '../../test/utils'
import type { AgentTrace, TraceStep } from '../../types/trace'
import { AgentActivityPanel } from './AgentActivityPanel'
import AgentTraceDialog from './AgentTraceDialog'

// React Flow needs layout APIs jsdom lacks: render nodes/edges as plain elements instead.
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, edges, onNodeClick }: { nodes: { id: string; className: string }[]; edges: { source: string; target: string; label?: string; style: { stroke: string } }[]; onNodeClick: (e: unknown, n: unknown) => void; children: ReactNode }) => (
    <div>
      {nodes.map((n) => (
        <button key={n.id} data-testid={`node-${n.id}`} className={n.className} onClick={(e) => onNodeClick(e, n)}>{n.id}</button>
      ))}
      {edges
        .filter((e) => e.style.stroke === '#6658dc')
        .map((e) => <span key={`${e.source}-${e.target}`} data-testid="taken">{`${e.source}>${e.target}${e.label ?? ''}`}</span>)}
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  MarkerType: { ArrowClosed: 'arrowclosed' },
}))

const step = (seq: number, node: string, over: Partial<TraceStep> = {}): TraceStep => ({
  id: `s${seq}`,
  node,
  seq,
  status: 'succeeded',
  duration_ms: 120,
  started_at: '2026-11-26T10:00:00Z',
  input: {},
  output: {},
  error: null,
  calls: [],
  ...over,
})

const graph = {
  nodes: ['__start__', 'load_context', 'conversation', 'planning', 'modeling', 'execute', 'quality', 'revise', 'commit', 'respond', '__end__'].map((id) => ({ id })),
  edges: [
    ['__start__', 'load_context'], ['load_context', 'conversation'], ['conversation', 'planning'], ['planning', 'modeling'],
    ['modeling', 'execute'], ['execute', 'quality'], ['quality', 'revise'], ['revise', 'modeling'], ['quality', 'commit'],
    ['commit', 'respond'], ['respond', '__end__'],
  ].map(([source, target]) => ({ source, target, conditional: false, label: '' })),
}

const llmCall = {
  id: 'c1', kind: 'llm' as const, agent: 'planning', model: 'agents-a1', status: 'succeeded' as const, duration_ms: 4200,
  input: { attempt: 1, prompt_tokens: 1500 }, output: { completion_tokens: 400, issues: [] }, error: null, created_at: null,
}

function trace(over: Partial<AgentTrace> = {}): AgentTrace {
  return {
    job: { id: 'j1', project_id: 'p1', status: 'succeeded', created_at: null, started_at: null, completed_at: null, duration_ms: 65_000, error_message: null },
    message: { id: 'm1', content: 'Add two drawers' },
    graph,
    steps: [
      step(1, 'load_context', { output: { objects: 5 } }),
      step(2, 'conversation', { output: { intent: 'modify', task: 'add drawers' } }),
      step(3, 'planning', { output: { parts: ['drawer_left', 'drawer_right'], summary: 'Two drawers' }, calls: [llmCall] }),
      step(4, 'modeling', { output: { operations: 12 } }),
      step(5, 'execute', { output: { result: 'staged', objects: 7 } }),
      step(6, 'quality', { status: 'failed', output: { passed: false, checks: 40, issues: ['[fixable] drawer floats'] } }),
      step(7, 'revise', { output: { revision: 1 } }),
      step(8, 'modeling', { output: { operations: 12 } }),
      step(9, 'execute', { output: { result: 'staged', objects: 7 } }),
      step(10, 'quality', { output: { passed: true, checks: 42 } }),
      step(11, 'commit', { output: { version: 3 } }),
      step(12, 'respond', { output: { success: true, reply: 'Added two drawers.' } }),
    ],
    calls: [],
    totals: { steps: 12, llm_calls: 1, llm_ms: 4200, prompt_tokens: 1500, completion_tokens: 400, revisions: 1 },
    include_debug: false,
    ...over,
  }
}

const URL = 'GET /api/v1/projects/p1/agent-jobs/j1/trace'

describe('AgentTraceDialog', () => {
  beforeEach(() => useAuthStore.setState({ status: 'authenticated', accessToken: 't', user: null }))

  it('draws the path taken, including the revision loop, and the step timeline', async () => {
    mockFetch({ [URL]: () => json(200, trace()) })
    renderWithProviders(<AgentTraceDialog open onClose={() => {}} projectId="p1" jobId="j1" />)
    expect(await screen.findByText('“Add two drawers”')).toBeInTheDocument()
    expect(screen.getByText('12 steps')).toBeInTheDocument()
    expect(screen.getByText('1 revision')).toBeInTheDocument()

    const taken = screen.getAllByTestId('taken').map((e) => e.textContent)
    expect(taken).toEqual(expect.arrayContaining(['quality>revise', 'revise>modeling', 'modeling>execute×2', 'respond>__end__']))
    expect(screen.getByTestId('node-quality').className).toContain('reached')

    expect(screen.getByText('Intent: modify — add drawers')).toBeInTheDocument()
    expect(screen.getByText('1 issue(s) in 40 checks')).toBeInTheDocument()
    expect(screen.getByText('Saved as v3')).toBeInTheDocument()
  })

  it('clicking a graph node filters the timeline and opens its LLM calls (no prompts for users)', async () => {
    mockFetch({ [URL]: () => json(200, trace()) })
    renderWithProviders(<AgentTraceDialog open onClose={() => {}} projectId="p1" jobId="j1" />)
    await userEvent.click(await screen.findByTestId('node-planning'))
    expect(screen.getByText('Showing:')).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText(/LLM call · planning/)).toBeInTheDocument()
    expect(within(rows[0]).getByText('1500 → 400 tokens')).toBeInTheDocument()
    expect(screen.queryByText(/Prompt & response/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(12)
  })

  it('shows prompts to admins', async () => {
    const withDebug = trace({
      include_debug: true,
      steps: [step(1, 'planning', { calls: [{ ...llmCall, debug: { messages: [{ role: 'system', content: 'You are the planner', images: 0 }], response: '{"parts":[]}' } }] })],
    })
    mockFetch({ 'GET /api/v1/admin/agent-jobs/j1/trace': () => json(200, withDebug) })
    renderWithProviders(<AgentTraceDialog open onClose={() => {}} projectId={null} jobId="j1" />)
    await userEvent.click(await screen.findByRole('button', { name: /Planning agent/ }))
    await userEvent.click(screen.getByText(/Prompt & response/))
    expect(screen.getByText('You are the planner')).toBeInTheDocument()
    expect(screen.getByText('{"parts":[]}')).toBeInTheDocument()
  })

  it('follows a running turn live until it ends', async () => {
    let calls = 0
    const running = trace({
      job: { ...trace().job, status: 'running', duration_ms: null },
      steps: [step(1, 'load_context'), step(2, 'planning', { status: 'running', duration_ms: 0 })],
    })
    mockFetch({ [URL]: () => json(200, ++calls === 1 ? running : trace()) })
    renderWithProviders(<AgentTraceDialog open onClose={() => {}} projectId="p1" jobId="j1" />)
    expect(await screen.findByText('Live')).toBeInTheDocument()
    expect(screen.getByText('running…')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Live')).not.toBeInTheDocument(), { timeout: 4000 })
    expect(screen.getByText('succeeded')).toBeInTheDocument()
  })
})

describe('AgentActivityPanel', () => {
  it('lists turns of all users and opens a trace', async () => {
    mockFetch({
      'GET /api/v1/admin/agent-jobs': () =>
        json(200, {
          items: [{ job_id: 'j1', project_id: 'p1', project_name: 'Lamp', user_email: 'bob@example.com', message: 'Make a lamp', status: 'failed', created_at: null, duration_ms: 3000, llm_calls: 3, tokens: 2000, revisions: 2, steps: 14 }],
          total: 1,
        }),
      'GET /api/v1/admin/agent-jobs/j1/trace': () => json(200, trace({ message: { id: 'm', content: 'Make a lamp' } })),
    })
    renderWithProviders(<AgentActivityPanel />)
    expect(await screen.findByText('bob@example.com')).toBeInTheDocument()
    expect(screen.getByText('· 2 rev.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open trace: Make a lamp' }))
    expect(await screen.findByText('“Make a lamp”')).toBeInTheDocument()
  })
})
