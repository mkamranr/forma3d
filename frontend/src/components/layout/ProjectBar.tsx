import { Box, Camera, History, Redo2, Undo2 } from 'lucide-react'
import { ProjectMenu } from './ProjectMenu'
import type { Project } from '../../types/api'
import { relativeTime } from '../../utils/format'

type HistoryControl = { undoTo: number | null; redoTo: number | null; disabled: boolean; onUndo: () => void; onRedo: () => void }

type Props = {
  project: Project | undefined
  onOpenVersions: () => void
  /** Opens the render dialog; absent while there is nothing to render. */
  onRender?: () => void
  history?: HistoryControl
  /** Opens the export dialog (from the project menu). */
  onExport?: () => void
}

export function ProjectBar({ project, onOpenVersions, onRender, history, onExport }: Props) {
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
        <button className="soft-btn" onClick={onRender} disabled={!onRender} title="Render a high-quality image in Blender">
          <Camera size={14} /> Render
        </button>
        <button className="soft-btn" onClick={onOpenVersions} title="Every change is saved automatically as a version">
          <History size={14} /> Versions{project ? ` · ${project.version_count}` : ''}
        </button>
        <ProjectMenu project={project} onExport={onExport ?? (() => undefined)} />
      </div>
    </div>
  )
}
