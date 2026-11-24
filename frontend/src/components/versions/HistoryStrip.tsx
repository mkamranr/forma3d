import { ChevronRight, Clock3 } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'

export function HistoryStrip() {
  const history = useWorkspaceStore((s) => s.history)
  const notify = useWorkspaceStore((s) => s.notify)
  return (
    <div className="history-strip">
      <div className="history-title"><Clock3 size={14} /> RECENT ACTIVITY</div>
      <div className="history-items">
        {history.slice(-3).reverse().map((h, i) => (
          <span className="history-item" key={`${history.length}-${i}`}><span className="history-bullet"></span>{h}</span>
        ))}
      </div>
      <button className="history-all" onClick={() => notify('Version history panel will be connected to project API')}>
        View history <ChevronRight size={13} />
      </button>
    </div>
  )
}
