import { Check, Command, Cuboid, Download, GitCommitHorizontal, History, Loader2, MousePointer2, PanelLeftClose, Plus, Send, ShieldAlert, ShieldCheck, Sparkles, WandSparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { glbUrl, useConversation, useSendMessage, useVersions } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { downloadAsset } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { ConversationMessage } from '../../types/scene'
import { clockTime } from '../../utils/format'

const WELCOME =
  'Your workspace is ready. Describe what you want to build — for example “create a modern wooden desk” — and I will plan it, build it in Blender and save it as a version.'

const SUGGESTIONS: [string, string][] = [
  ['Wooden desk', 'Create a modern wooden desk'],
  ['Black metal legs', 'Make the legs black metal'],
  ['Two drawers', 'Add two drawers'],
  ['Walnut top', 'Change the top to walnut'],
  ['Export GLB', 'Export it as GLB'],
]

function safeFilename(name: string) {
  return name.replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '') || 'model'
}

function ExportButton({ message, projectName }: { message: ConversationMessage; projectName: string }) {
  const { projectId, version: head } = useWorkspaceScene()
  const versions = useVersions(projectId)
  const [busy, setBusy] = useState(false)
  const notify = useWorkspaceStore((s) => s.notify)
  const pinned = message.metadata.export_version_id
  const version = (pinned && versions.data?.find((v) => v.id === pinned)) || (!pinned ? head : null)
  const url = glbUrl(version)
  if (!url || !version) return <div className="chat-chip muted">Nothing to export yet</div>

  const download = async () => {
    setBusy(true)
    try {
      await downloadAsset(url, `${safeFilename(projectName)}-v${version.version_number}.glb`)
    } catch {
      notify('Download failed — please try again')
    } finally {
      setBusy(false)
    }
  }
  return (
    <button className="chat-action" onClick={() => void download()} disabled={busy}>
      {busy ? <Loader2 size={13} className="spin" /> : <Download size={13} />} Download GLB · v{version.version_number}
    </button>
  )
}

function QualityChip({ quality }: { quality: NonNullable<ConversationMessage['metadata']['quality']> }) {
  const revised = quality.revisions ? ` · ${quality.revisions} revision${quality.revisions > 1 ? 's' : ''}` : ''
  const title = quality.warnings.length ? quality.warnings.join(' · ') : 'All quality checks passed'
  return (
    <div className={`chat-chip quality${quality.passed ? '' : ' warn'}`} title={title}>
      {quality.passed ? <ShieldCheck size={12} /> : <ShieldAlert size={12} />}
      {quality.passed ? 'QA passed' : 'QA warnings'} · {quality.checks} checks{revised}
    </div>
  )
}

function MessageView({ m, projectName, highlighted }: { m: ConversationMessage; projectName: string; highlighted: boolean }) {
  const setVersionsOpen = useWorkspaceStore((s) => s.setVersionsOpen)
  const failed = m.role === 'assistant' && (m.status === 'failed' || m.status === 'cancelled')
  return (
    <div id={`msg-${m.id}`} className={`message ${m.role}${highlighted ? ' highlighted' : ''}`}>
      {m.role === 'assistant' && <div className="assistant-avatar"><Sparkles size={13} /></div>}
      <div className="message-body">
        <div className="message-meta">
          {m.role === 'assistant' ? 'Forma Assistant' : 'You'} <span>{clockTime(new Date(m.created_at))}</span>
        </div>
        <div className={`bubble${failed ? ' failed' : ''}`}>{m.content}</div>
        {m.metadata.restored ? (
          <div className="chat-chip"><History size={12} /> Restored v{m.metadata.restored}</div>
        ) : m.metadata.version_number ? (
          <button
            className="chat-chip"
            title="Open this version in the history"
            onClick={() => setVersionsOpen(true, m.metadata.version_id ?? null)}
          >
            <GitCommitHorizontal size={12} /> Saved as v{m.metadata.version_number}
          </button>
        ) : null}
        {m.metadata.quality && <QualityChip quality={m.metadata.quality} />}
        {m.metadata.action === 'export' && <ExportButton message={m} projectName={projectName} />}
      </div>
    </div>
  )
}

export function ChatPanel({ projectName }: { projectName: string }) {
  const { projectId, inventory, selected, selection } = useWorkspaceScene()
  const highlightMessageId = useWorkspaceStore((s) => s.highlightMessageId)
  const conversation = useConversation(projectId)
  const prompt = useWorkspaceStore((s) => s.prompt)
  const activeJob = useWorkspaceStore((s) => s.activeJob)
  const { setPrompt, setShowChat } = useWorkspaceStore.getState()
  const sendMessage = useSendMessage(projectId)
  const endRef = useRef<HTMLDivElement>(null)
  const messages = conversation.data?.messages ?? []
  const thinking = activeJob?.kind === 'agent'

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.length, activeJob?.message])

  // "Show in chat" from the version history: scroll to the message, highlight it briefly.
  useEffect(() => {
    if (!highlightMessageId) return
    document.getElementById(`msg-${highlightMessageId}`)?.scrollIntoView?.({ block: 'center' })
    const t = setTimeout(() => useWorkspaceStore.getState().showMessage(null), 2500)
    return () => clearTimeout(t)
  }, [highlightMessageId, messages.length])

  const send = async (text = prompt.trim()) => {
    if (!text || activeJob) return
    if (await sendMessage(text, selection.map((o) => o.object_id))) setPrompt('')
  }

  return (
    <aside className="chat-panel">
      <div className="panel-heading">
        <div className="heading-icon ai"><Sparkles size={16} /></div>
        <div>
          <strong>AI Assistant</strong>
          <small><span className="online-dot"></span> {thinking ? 'Working…' : activeJob ? 'Building…' : 'Ready'}</small>
        </div>
        <button className="panel-collapse" onClick={() => setShowChat(false)} title="Hide AI assistant"><PanelLeftClose size={16} /></button>
      </div>

      <div className="chat-context">
        <div className="context-label">CURRENT PROJECT</div>
        <div className="context-row">
          <Cuboid size={15} />
          <span>{projectName}</span>
          <span className="context-count">{inventory.objects.length} objects</span>
        </div>
        {selected && selection.length <= 1 && (
          <div className="context-row selection" title="The assistant knows which object is selected">
            <MousePointer2 size={13} />
            <span>{selected.name}</span>
            <span className="context-count">selected</span>
          </div>
        )}
        {selection.length > 1 && (
          <div className="context-row selection" title={selection.map((o) => o.name).join(', ')}>
            <MousePointer2 size={13} />
            <span>{selection.map((o) => o.name).join(', ')}</span>
            <span className="context-count">{selection.length} selected</span>
          </div>
        )}
      </div>

      <div className="messages" aria-live="polite">
        <div className="date-divider"><span>Today</span></div>
        {conversation.isSuccess && messages.length === 0 && (
          <div className="message assistant">
            <div className="assistant-avatar"><Sparkles size={13} /></div>
            <div className="message-body">
              <div className="message-meta">Forma Assistant</div>
              <div className="bubble">{WELCOME}</div>
            </div>
          </div>
        )}
        {conversation.isError && <div className="chat-chip muted">Could not load the conversation.</div>}
        {messages.map((m) => <MessageView key={m.id} m={m} projectName={projectName} highlighted={m.id === highlightMessageId} />)}
        <div className="agent-status" role="status">
          {activeJob ? (
            <>
              <span className="status-check busy"><Loader2 size={11} className="spin" /></span>
              <span>{activeJob.message}</span>
            </>
          ) : (
            <>
              <span className="status-check"><Check size={11} /></span>
              <span>Scene context synced</span>
              <span className="status-time">just now</span>
            </>
          )}
        </div>
        <div ref={endRef} />
      </div>

      <div className="suggestions">
        <span>TRY ASKING</span>
        {SUGGESTIONS.map(([label, text]) => (
          <button key={label} onClick={() => setPrompt(text)}>{label}</button>
        ))}
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder="Describe the 3D object you want to create or tell me what to change..."
            aria-label="Prompt"
            maxLength={4000}
            rows={2}
          />
          <div className="composer-bottom">
            <button className="attach-btn" title="Attachments (coming soon)" disabled><Plus size={17} /></button>
            <span className="composer-hint"><Command size={11} /> Enter to send</span>
            <button className="send-btn" onClick={() => void send()} disabled={!prompt.trim() || !!activeJob} title="Send"><Send size={15} /></button>
          </div>
        </div>
        <div className="composer-foot"><WandSparkles size={12} /> Every change is built in Blender and saved as a version</div>
      </div>
    </aside>
  )
}
