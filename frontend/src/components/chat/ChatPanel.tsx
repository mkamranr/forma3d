import { Check, Command, Cuboid, PanelLeftClose, Plus, Send, Sparkles, WandSparkles } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'

export function ChatPanel({ projectName }: { projectName: string }) {
  const messages = useWorkspaceStore((s) => s.messages)
  const prompt = useWorkspaceStore((s) => s.prompt)
  const objectCount = useWorkspaceStore((s) => s.objects.length)
  const { setPrompt, sendPrompt, setShowChat } = useWorkspaceStore.getState()

  return (
    <aside className="chat-panel">
      <div className="panel-heading">
        <div className="heading-icon ai"><Sparkles size={16} /></div>
        <div>
          <strong>AI Assistant</strong>
          <small><span className="online-dot"></span> Ready to create</small>
        </div>
        <button className="panel-collapse" onClick={() => setShowChat(false)} title="Hide AI assistant"><PanelLeftClose size={16} /></button>
      </div>

      <div className="chat-context">
        <div className="context-label">CURRENT PROJECT</div>
        <div className="context-row">
          <Cuboid size={15} />
          <span>{projectName}</span>
          <span className="context-count">{objectCount} objects</span>
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
        <div className="agent-status">
          <span className="status-check"><Check size={11} /></span>
          <span>Scene context synced</span>
          <span className="status-time">just now</span>
        </div>
      </div>

      <div className="suggestions">
        <span>TRY ASKING</span>
        <button onClick={() => setPrompt('Add a sphere to the desk')}>Add a sphere</button>
        <button onClick={() => setPrompt('Make the tabletop wider')}>Widen tabletop</button>
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendPrompt()
              }
            }}
            placeholder="Describe the 3D object you want to create or tell me what to change..."
            aria-label="Prompt"
            rows={2}
          />
          <div className="composer-bottom">
            <button className="attach-btn" title="Attachments (coming soon)" disabled><Plus size={17} /></button>
            <span className="composer-hint"><Command size={11} /> Enter to send</span>
            <button className="send-btn" onClick={sendPrompt} disabled={!prompt.trim()} title="Send"><Send size={15} /></button>
          </div>
        </div>
        <div className="composer-foot"><WandSparkles size={12} /> AI-generated changes can be reviewed before applying</div>
      </div>
    </aside>
  )
}
