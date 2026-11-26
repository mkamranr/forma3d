import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo } from 'react'
import type { AgentGraph, StepStatus, TraceStep } from '../../types/trace'
import { LLM_NODES, nodeLabel } from './labels'

/** Hand-tuned positions for the modeling graph (top -> bottom); unknown nodes go in an extra column. */
const POS: Record<string, [number, number]> = {
  __start__: [0, 0],
  load_context: [0, 58],
  inspect_scene: [0, 116],
  vision: [0, 174],
  conversation: [0, 232],
  answer: [-215, 320],
  version_manager: [-215, 400],
  planning: [0, 290],
  ask: [215, 290],
  wait_answer: [215, 348],
  modeling: [0, 348],
  execute: [0, 406],
  quality: [0, 464],
  revise: [215, 435],
  commit: [0, 522],
  fail: [215, 522],
  respond: [0, 580],
  __end__: [0, 638],
}

const COLORS: Record<StepStatus, { bg: string; border: string; text: string }> = {
  succeeded: { bg: '#ecf8ef', border: '#3fa45a', text: '#1f5f31' },
  failed: { bg: '#fdeceb', border: '#d9534f', text: '#8a2522' },
  running: { bg: '#eef0ff', border: '#6658dc', text: '#3d33a3' },
  waiting: { bg: '#fff6e3', border: '#d99a1e', text: '#7a5310' },
  skipped: { bg: '#f5f6f8', border: '#c4c8d0', text: '#8a91a0' },
  cancelled: { bg: '#f5f6f8', border: '#9aa0ab', text: '#5d6470' },
  interrupted: { bg: '#f5f6f8', border: '#9aa0ab', text: '#5d6470' },
}

/** Graph from the worker if published; otherwise nodes/edges reconstructed from the path itself. */
function graphOf(graph: AgentGraph | null, steps: TraceStep[]): AgentGraph {
  if (graph && graph.nodes.length) return graph
  const ids = ['__start__', ...new Set(steps.map((s) => s.node)), '__end__']
  const edges = new Map<string, AgentGraph['edges'][number]>()
  const path = ['__start__', ...steps.map((s) => s.node)]
  for (let i = 1; i < path.length; i++) {
    edges.set(`${path[i - 1]}>${path[i]}`, { source: path[i - 1], target: path[i], conditional: false, label: '' })
  }
  return { nodes: ids.map((id) => ({ id })), edges: [...edges.values()] }
}

export function AgentGraphView({
  graph,
  steps,
  selected,
  onSelect,
  live,
}: {
  graph: AgentGraph | null
  steps: TraceStep[]
  selected: string | null
  onSelect: (node: string) => void
  live: boolean
}) {
  const { nodes, edges } = useMemo(() => {
    const g = graphOf(graph, steps)
    const visits = new Map<string, TraceStep[]>()
    for (const s of steps) visits.set(s.node, [...(visits.get(s.node) ?? []), s])

    // Edges actually taken, in order (START -> first step ... last step -> END once replied).
    const path = ['__start__', ...steps.map((s) => s.node)]
    const last = steps[steps.length - 1]
    if (last && last.node === 'respond' && last.status === 'succeeded') path.push('__end__')
    const taken = new Map<string, number>()
    for (let i = 1; i < path.length; i++) {
      const k = `${path[i - 1]}>${path[i]}`
      taken.set(k, (taken.get(k) ?? 0) + 1)
    }

    let extra = 0
    const nodes: Node[] = g.nodes.map(({ id }) => {
      const runs = visits.get(id) ?? []
      const latest = runs[runs.length - 1]
      const terminal = id === '__start__' || id === '__end__'
      const reached = terminal ? path.includes(id) : runs.length > 0
      const c = latest ? COLORS[latest.status] : null
      const pos = POS[id] ?? [370, 58 * extra++]
      // The AI badge already marks agents, so the diagram drops the "agent" suffix to stay compact.
      const label = terminal ? (id === '__start__' ? 'Start' : 'End') : nodeLabel(id).replace(/ agent$/, '')
      return {
        id,
        position: { x: pos[0], y: pos[1] },
        draggable: false,
        connectable: false,
        selectable: !terminal,
        data: {
          label: (
            <div className="trace-node-inner" title={latest ? `${label}: ${latest.status}` : `${label}: not used in this turn`}>
              <span className="trace-node-label">
                {LLM_NODES.has(id) && <span className="trace-ai">AI</span>}
                {label}
              </span>
              {runs.length > 0 && (
                <span className="trace-node-seq">{runs.map((r) => `#${r.seq}`).join(' ')}</span>
              )}
            </div>
          ),
        },
        className: `trace-node${terminal ? ' terminal' : ''}${reached ? ' reached' : ''}${latest?.status === 'running' ? ' running' : ''}${selected === id ? ' selected' : ''}`,
        style: c
          ? { background: c.bg, borderColor: c.border, color: c.text, borderStyle: latest?.status === 'skipped' ? 'dashed' : 'solid' }
          : reached
            ? { background: '#202633', color: '#fff', borderColor: '#202633' }
            : undefined,
      }
    })

    const edges: Edge[] = g.edges.map((e, i) => {
      const count = taken.get(`${e.source}>${e.target}`) ?? 0
      const back = (POS[e.target]?.[1] ?? 0) < (POS[e.source]?.[1] ?? 0)
      return {
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: back ? 'smoothstep' : 'default',
        animated: count > 0 && live && e.target === last?.node,
        label: count > 1 ? `×${count}` : undefined,
        labelStyle: { fontSize: 12, fontWeight: 700, fill: '#4f46bd' },
        labelBgStyle: { fill: '#eef0ff' },
        style: count
          ? { stroke: '#6658dc', strokeWidth: 2.2 }
          : { stroke: '#c9cdd6', strokeWidth: 1, strokeDasharray: e.conditional ? '4 3' : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color: count ? '#6658dc' : '#c9cdd6', width: 14, height: 14 },
        zIndex: count ? 1 : 0,
      }
    })
    return { nodes, edges }
  }, [graph, steps, selected, live])

  return (
    <div className="trace-graph" data-testid="agent-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.06 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_e, n) => n.id !== '__start__' && n.id !== '__end__' && onSelect(n.id)}
      >
        <Background gap={18} size={1} color="#e6e8ee" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
    </div>
  )
}
