import { ChevronDown, CircleHelp, Cuboid, Download, Settings2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { UserMenu } from './UserMenu'

type Props = {
  version: number
  onExport: () => void
}

export function TopBar({ version, onExport }: Props) {
  return (
    <header className="topbar">
      <Link to="/" className="brand" style={{ color: 'inherit', textDecoration: 'none' }} title="Back to projects">
        <div className="brand-mark"><Cuboid size={20} strokeWidth={2.4} /></div>
        <span>forma<span className="brand-light">3D</span></span>
        <span className="brand-divider"></span>
        <span className="workspace-name">Studio workspace <ChevronDown size={14} /></span>
      </Link>
      <div className="top-center">
        <span className="save-dot"></span>
        <span>All changes saved</span>
        <span className="top-sep">·</span>
        <span>Version {version}.0</span>
      </div>
      <div className="top-actions">
        <button className="icon-btn" title="Help"><CircleHelp size={17} /></button>
        <button className="icon-btn" title="Settings"><Settings2 size={17} /></button>
        <button className="share-btn" onClick={onExport}><Download size={15} /> Export</button>
        <UserMenu />
      </div>
    </header>
  )
}
