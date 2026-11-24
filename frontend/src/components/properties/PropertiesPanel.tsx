import { Box } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { Vec3 } from '../../types/scene'

const AXES = ['X', 'Y', 'Z'] as const

export function PropertiesPanel() {
  const selected = useWorkspaceStore((s) => s.objects.find((o) => o.id === s.selectedId) ?? null)
  const updateSelected = useWorkspaceStore((s) => s.updateSelected)

  const setAxis = (key: 'position' | 'scale', i: number, raw: string) => {
    if (!selected) return
    const next = [...selected[key]] as Vec3
    const value = Number(raw)
    if (!Number.isFinite(value)) return
    next[i] = key === 'scale' ? Math.max(0.05, value) : value
    updateSelected({ [key]: next })
  }

  return (
    <div className="inspector-section object-properties">
      <div className="section-heading">OBJECT PROPERTIES <span className="property-pill">{selected ? 'SELECTED' : 'NONE'}</span></div>
      {selected ? (
        <>
          <div className="object-name">
            <div className="object-thumb"><Box size={19} /></div>
            <div><strong>{selected.name}</strong><small>{selected.kind.toUpperCase()} · Object</small></div>
          </div>
          <div className="property-group">
            <div className="property-title">TRANSFORM</div>
            <div className="field-label">Position</div>
            <div className="vector-fields">
              {AXES.map((axis, i) => (
                <label key={axis}>
                  <span>{axis}</span>
                  <input type="number" step=".1" aria-label={`Position ${axis}`} value={selected.position[i]} onChange={(e) => setAxis('position', i, e.target.value)} />
                </label>
              ))}
            </div>
            <div className="field-label">Scale</div>
            <div className="vector-fields">
              {AXES.map((axis, i) => (
                <label key={axis}>
                  <span>{axis}</span>
                  <input type="number" min=".05" step=".1" aria-label={`Scale ${axis}`} value={selected.scale[i]} onChange={(e) => setAxis('scale', i, e.target.value)} />
                </label>
              ))}
            </div>
          </div>
          <div className="property-group">
            <div className="property-title">DISPLAY</div>
            <label className="toggle-row">
              <span>Visible in viewport</span>
              <input type="checkbox" checked={selected.visible} onChange={(e) => updateSelected({ visible: e.target.checked })} />
              <i></i>
            </label>
          </div>
        </>
      ) : (
        <div className="empty-selection">Select an object in the scene to edit its properties.</div>
      )}
    </div>
  )
}
