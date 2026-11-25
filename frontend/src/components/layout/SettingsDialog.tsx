import { resetViewportPrefs, useWorkspaceStore, type Projection, type Shading } from '../../stores/workspaceStore'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'

function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: () => void }) {
  return (
    <label className="settings-row">
      <span>
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </span>
      <input type="checkbox" role="switch" checked={checked} onChange={onChange} />
    </label>
  )
}

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="settings-row">
      <span><strong>{label}</strong></span>
      <div className="settings-segment" role="radiogroup" aria-label={label}>
        {options.map(([v, text]) => (
          <button key={v} role="radio" aria-checked={value === v} className={value === v ? 'active' : ''} onClick={() => onChange(v)}>
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const s = useWorkspaceStore()
  return (
    <Dialog open={open} onClose={onClose} title="Workspace settings" description="Viewport preferences are remembered on this device.">
      <div className="settings">
        <h3>Viewport</h3>
        <Choice<Shading>
          label="Shading"
          value={s.shading}
          options={[['material', 'Material'], ['solid', 'Solid'], ['wireframe', 'Wireframe']]}
          onChange={s.setShading}
        />
        <Choice<Projection>
          label="Camera"
          value={s.projection}
          options={[['perspective', 'Perspective'], ['orthographic', 'Orthographic']]}
          onChange={(p) => p !== s.projection && s.toggleProjection()}
        />
        <Toggle label="Floor grid" hint="1 m grid on the ground plane" checked={s.showGrid} onChange={s.toggleGrid} />
        <Toggle label="Axes gizmo" hint="X / Y / Z orientation helper" checked={s.showAxes} onChange={s.toggleAxes} />
        <h3>Panels</h3>
        <Toggle label="AI assistant" checked={s.showChat} onChange={() => s.setShowChat(!s.showChat)} />
        <Toggle label="Inspector" hint="Scene tree, properties and materials" checked={s.showInspector} onChange={() => s.setShowInspector(!s.showInspector)} />
        <div className="settings-foot">
          <Button variant="ghost" onClick={resetViewportPrefs}>Reset to defaults</Button>
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </Dialog>
  )
}
