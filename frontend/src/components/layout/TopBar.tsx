import { CircleHelp, Cuboid, Download, Settings2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { HelpDialog } from './HelpDialog'
import { SettingsDialog } from './SettingsDialog'
import { UserMenu } from './UserMenu'
import { WorkspaceSwitcher } from './WorkspaceSwitcher'

type Props = {
  version: number | null
  /** Progress message while a Blender job is running. */
  building: string | null
  onExport: () => void
  projectId?: string
}

export function TopBar({ version, building, onExport, projectId }: Props) {
  const [panel, setPanel] = useState<'help' | 'settings' | null>(null)
  return (
    <header className="topbar">
      <div className="brand">
        <Link to="/" className="brand" style={{ color: 'inherit', textDecoration: 'none' }} title="Back to projects">
          <div className="brand-mark"><Cuboid size={20} strokeWidth={2.4} /></div>
          <span>forma<span className="brand-light">3D</span></span>
        </Link>
        <span className="brand-divider"></span>
        <WorkspaceSwitcher currentId={projectId} />
      </div>
      <div className="top-center">
        <span className={`save-dot ${building ? 'busy' : ''}`}></span>
        <span>{building ? `Building · ${building}` : 'All changes saved'}</span>
        <span className="top-sep">·</span>
        <span>{version ? `Version ${version}` : 'No versions yet'}</span>
      </div>
      <div className="top-actions">
        <button className="icon-btn" title="Help & shortcuts" aria-label="Help" onClick={() => setPanel('help')}><CircleHelp size={17} /></button>
        <button className="icon-btn" title="Workspace settings" aria-label="Settings" onClick={() => setPanel('settings')}><Settings2 size={17} /></button>
        <button className="share-btn" onClick={onExport} disabled={!version} title="Export as GLB, glTF, OBJ or STL"><Download size={15} /> Export</button>
        <UserMenu />
      </div>
      <HelpDialog open={panel === 'help'} onClose={() => setPanel(null)} />
      <SettingsDialog open={panel === 'settings'} onClose={() => setPanel(null)} />
    </header>
  )
}
