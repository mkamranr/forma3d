import { Check, Command, Cuboid, Loader2, PanelLeftClose, Plus, Send, Sparkles, WandSparkles } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { interpretDemoCommand } from '../../features/modeling/demoCommands'
import { useModelingAction } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { addObject, deleteObject, editMaterial, editObject, giveObjectMaterial } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'

export function ChatPanel({ projectName }: { projectName: string }) {
  const { projectId, inventory, materialMap } = useWorkspaceScene()
  const messages = useWorkspaceStore((s) => s.messages)
  const prompt = useWorkspaceStore((s) => s.prompt)
  const activeJob = useWorkspaceStore((s) => s.activeJob)
  const selectedId = useWorkspaceStore((s) => s.selectedId)
  const { setPrompt, setShowChat, say } = useWorkspaceStore.getState()
  const run = useModelingAction()
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.length, activeJob?.message])

  const send = async () => {
    const text = prompt.trim()
    if (!text || activeJob) return
    say('user', text)
    setPrompt('')
    const { action, reply } = interpretDemoCommand(text, inventory.objects, materialMap, selectedId)
    say('assistant', reply)
    switch (action.type) {
      case 'add':
        await run(`Added ${action.kind}`, () => addObject(projectId, action.kind))
        break
      case 'remove':
        await run('Deleted object', () => deleteObject(projectId, action.targetId, true))
        break
      case 'edit':
        await run('Edited object', () => editObject(projectId, action.targetId, action.patch))
        break
      case 'material': {
        const patch = { base_color: action.color, metallic: action.metallic, roughness: action.roughness }
        await run('Material updated', () =>
          action.materialId
            ? editMaterial(projectId, action.materialId, patch)
            : giveObjectMaterial(projectId, action.targetId, patch),
        )
        break
      }
      case 'none':
        break
    }
  }

  return (
    <aside className="chat-panel">
      <div className="panel-heading">
        <div className="heading-icon ai"><Sparkles size={16} /></div>
        <div>
          <strong>AI Assistant</strong>
          <small><span className="online-dot"></span> {activeJob ? 'Building…' : 'Ready · rule-based preview'}</small>
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
      </div>

      <div className="messages">
        <div className="date-divider"><span>Today</span></div>
        {messages.map((m, i) => (
          <div className={`message ${m.role}`} key={i}>
            {m.role === 'assistant' && <div className="assistant-avatar"><Sparkles size={13} /></div>}
            <div className="message-body">
              <div className="message-meta">{m.role === 'assistant' ? 'Forma Assistant' : 'You'} <span>{m.time}</span></div>
              <div className="bubble">{m.text}</div>
            </div>
          </div>
        ))}
        <div className="agent-status" role="status">
          {activeJob ? (
            <>
              <span className="status-check busy"><Loader2 size={11} className="spin" /></span>
              <span>{activeJob.label} · {activeJob.message}</span>
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
        <button onClick={() => setPrompt('Add a sphere')}>Add a sphere</button>
        <button onClick={() => setPrompt('Make the tabletop wider')}>Widen tabletop</button>
        <button onClick={() => setPrompt('Make the legs black metal')}>Black metal legs</button>
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
