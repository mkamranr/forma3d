import { Box, History, MoreHorizontal } from 'lucide-react'
import type { Project } from '../../types/api'
import { relativeTime } from '../../utils/format'

type Props = {
  project: Project | undefined
  onOpenVersions: () => void
}

export function ProjectBar({ project, onOpenVersions }: Props) {
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
        <button className="soft-btn" onClick={onOpenVersions} title="Every change is saved automatically as a version">
          <History size={14} /> Versions{project ? ` · ${project.version_count}` : ''}
        </button>
        <button className="icon-btn"><MoreHorizontal size={18} /></button>
      </div>
    </div>
  )
}
