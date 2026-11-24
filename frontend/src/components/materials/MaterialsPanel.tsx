import { Plus } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'

const SWATCHES = ['#C99563', '#5D6470', '#8B9CFB', '#6E9C82', '#C96E68', '#E8E9E6']
const PRESETS: [string, string][] = [
  ['Oak', '#C99563'],
  ['Graphite', '#424953'],
  ['Sage', '#6E9C82'],
  ['Porcelain', '#E8E9E6'],
]

export function MaterialsPanel() {
  const selected = useWorkspaceStore((s) => s.objects.find((o) => o.id === s.selectedId) ?? null)
  const updateSelected = useWorkspaceStore((s) => s.updateSelected)
  const notify = useWorkspaceStore((s) => s.notify)

  return (
    <div className="materials-content">
      <div className="section-heading">
        MATERIAL LIBRARY <button onClick={() => notify('Material library coming soon')}><Plus size={14} /></button>
      </div>
      {selected ? (
        <>
          <div className="material-preview" style={{ background: selected.color }}><span>ACTIVE MATERIAL</span></div>
          <div className="material-name"><strong>{selected.name} · Finish</strong><small>Principled material</small></div>
          <div className="property-group">
            <div className="property-title">BASE COLOR</div>
            <div className="color-editor">
              <input type="color" aria-label="Base color" value={selected.color} onChange={(e) => updateSelected({ color: e.target.value })} />
              <input
                className="hex-input"
                aria-label="Hex color"
                value={selected.color.toUpperCase()}
                onChange={(e) => /^#[0-9A-Fa-f]{6}$/.test(e.target.value) && updateSelected({ color: e.target.value })}
              />
            </div>
            <div className="swatches">
              {SWATCHES.map((c) => (
                <button key={c} style={{ background: c }} title={c} onClick={() => updateSelected({ color: c })} />
              ))}
            </div>
          </div>
          <div className="property-group">
            <div className="property-title">SURFACE PRESETS</div>
            <div className="preset-grid">
              {PRESETS.map(([label, color]) => (
                <button key={label} onClick={() => updateSelected({ color })}>
                  <span style={{ background: color }}></span>
                  {label}
                </button>
              ))}
            </div>
          </div>
        </>
      ) : (
        <div className="empty-selection">Select an object to edit its material.</div>
      )}
    </div>
  )
}
