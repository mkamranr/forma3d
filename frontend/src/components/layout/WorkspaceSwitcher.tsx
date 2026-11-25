import { Box, ChevronDown, LayoutGrid } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useProjects } from '../../features/projects/hooks'
import { relativeTime } from '../../utils/format'

/** "Studio workspace ▾": jump to another recent project or back to all projects. */
export function WorkspaceSwitcher({ currentId }: { currentId?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const recent = useProjects({ status: 'active', sort: 'updated_desc', limit: 8 }, open)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const others = (recent.data?.items ?? []).filter((p) => p.id !== currentId).slice(0, 6)
  return (
    <div ref={ref} className="menu-anchor">
      <button className="workspace-name" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} title="Switch project">
        Studio workspace <ChevronDown size={14} />
      </button>
      {open && (
        <div role="menu" className="popover-menu left">
          <div className="menu-label">Recent projects</div>
          {recent.isLoading && <div className="menu-empty">Loading…</div>}
          {recent.isSuccess && others.length === 0 && <div className="menu-empty">No other projects yet</div>}
          {others.map((p) => (
            <button key={p.id} role="menuitem" className="menu-item" onClick={() => { setOpen(false); navigate(`/projects/${p.id}`) }}>
              <Box size={14} />
              <span className="menu-item-main">{p.name}</span>
              <small>{relativeTime(p.updated_at)}</small>
            </button>
          ))}
          <div className="menu-sep" />
          <Link role="menuitem" className="menu-item" to="/" onClick={() => setOpen(false)}>
            <LayoutGrid size={14} /> All projects
          </Link>
        </div>
      )}
    </div>
  )
}
