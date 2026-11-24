import { Archive, ArchiveRestore, Copy, Cuboid, Layers3, MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Project } from '../../types/api'
import { relativeTime } from '../../utils/format'

type Props = {
  project: Project
  onRename: (p: Project) => void
  onDuplicate: (p: Project) => void
  onToggleArchive: (p: Project) => void
  onDelete: (p: Project) => void
}

export function ProjectCard({ project, onRename, onDuplicate, onToggleArchive, onDelete }: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  const act = (fn: (p: Project) => void) => () => {
    setMenuOpen(false)
    fn(project)
  }
  const archived = project.status === 'archived'

  return (
    <article className="group relative overflow-hidden rounded-xl border border-line bg-white transition-shadow hover:shadow-[0_8px_24px_#20263314]">
      <Link to={`/projects/${project.id}`} className="block text-inherit no-underline" aria-label={`Open ${project.name}`}>
        <div className="relative grid aspect-[16/10] place-items-center bg-[radial-gradient(circle_at_50%_35%,#2c3442,#202630_70%)]">
          {project.thumbnail_url ? (
            <img src={project.thumbnail_url} alt="" className="h-full w-full object-cover" />
          ) : (
            <Cuboid size={42} strokeWidth={1.4} className="text-[#7f8aa0] transition-transform group-hover:scale-105" />
          )}
          {archived && (
            <span className="absolute left-3 top-3 rounded-full bg-white/90 px-2 py-0.5 text-[10px] font-bold tracking-wide text-ink-600">ARCHIVED</span>
          )}
        </div>
        <div className="px-4 pb-4 pt-3">
          <h3 className="truncate pr-8 font-display text-[14px] font-bold text-ink-900">{project.name}</h3>
          <p className="mt-0.5 line-clamp-1 min-h-[18px] text-[12px] text-ink-400">{project.description || 'No description'}</p>
          <div className="mt-3 flex items-center gap-3 text-[11px] text-ink-400">
            <span>Edited {relativeTime(project.updated_at)}</span>
            <span className="text-ink-300">·</span>
            <span className="inline-flex items-center gap-1"><Layers3 size={12} /> {project.version_count} {project.version_count === 1 ? 'version' : 'versions'}</span>
          </div>
        </div>
      </Link>

      <div ref={menuRef} className="absolute bottom-[54px] right-3">
        <button
          className="grid h-7 w-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent text-ink-400 hover:bg-surface-2 hover:text-brand-700"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={`Actions for ${project.name}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <MoreHorizontal size={16} />
        </button>
        {menuOpen && (
          <div role="menu" className="absolute right-0 top-8 z-20 w-44 rounded-lg border border-line bg-white p-1 shadow-lg">
            <MenuItem icon={<Pencil size={13} />} label="Rename" onClick={act(onRename)} />
            <MenuItem icon={<Copy size={13} />} label="Duplicate" onClick={act(onDuplicate)} />
            <MenuItem
              icon={archived ? <ArchiveRestore size={13} /> : <Archive size={13} />}
              label={archived ? 'Unarchive' : 'Archive'}
              onClick={act(onToggleArchive)}
            />
            <div className="my-1 h-px bg-[#eff0f4]" />
            <MenuItem icon={<Trash2 size={13} />} label="Delete" danger onClick={act(onDelete)} />
          </div>
        )}
      </div>
    </article>
  )
}

function MenuItem({ icon, label, danger, onClick }: { icon: React.ReactNode; label: string; danger?: boolean; onClick: () => void }) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={`flex w-full cursor-pointer items-center gap-2 rounded-md border-0 bg-transparent px-2.5 py-1.5 text-left text-[12px] ${
        danger ? 'text-danger-600 hover:bg-[#fbeceb]' : 'text-ink-600 hover:bg-surface-2 hover:text-brand-700'
      }`}
    >
      {icon} {label}
    </button>
  )
}
