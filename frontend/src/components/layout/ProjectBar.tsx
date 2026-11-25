import { Box, History, MoreHorizontal, Redo2, Undo2 } from 'lucide-react'
import type { Project } from '../../types/api'
import { relativeTime } from '../../utils/format'

type HistoryControl = { undoTo: number | null; redoTo: number | null; disabled: boolean; onUndo: () => void; onRedo: () => void }

type Props = {
  project: Project | undefined
  onOpenVersions: () => void
  history?: HistoryControl
}

export function ProjectBar({ project, onOpenVersions, history }: Props) {
  return (
    <div className="projectbar">
      <div className="project-title">
        <span className="project-icon"><Box size={16} /></span>
        <div>
          <strong>{project?.name ?? 'Loading project…'}</strong>
          <small>
            {project?.description || 'Untitled project'} <span>·</span>{' '}
            {project ? `Edited ${relativeTime(project.updated_at)}` : ''}
          </small>
        </div>
      </div>
      <div className="project-actions">
        {history && (
          <div className="history-buttons">
            <button
              className="icon-btn"
              aria-label="Undo"
              title={history.undoTo ? `Undo — back to v${history.undoTo} (Ctrl+Z)` : 'Nothing to undo'}
              disabled={history.disabled || !history.undoTo}
              onClick={history.onUndo}
            >
              <Undo2 size={16} />
            </button>
            <button
              className="icon-btn"
              aria-label="Redo"
              title={history.redoTo ? `Redo — forward to v${history.redoTo} (Ctrl+Shift+Z)` : 'Nothing to redo'}
              disabled={history.disabled || !history.redoTo}
              onClick={history.onRedo}
            >
              <Redo2 size={16} />
            </button>
          </div>
        )}
        <button className="soft-btn" onClick={onOpenVersions} title="Every change is saved automatically as a version">
          <History size={14} /> Versions{project ? ` · ${project.version_count}` : ''}
        </button>
        <button className="icon-btn"><MoreHorizontal size={18} /></button>
      </div>
    </div>
  )
}
