import { Box } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useModelingAction } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { editObject } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { ObjectPatch, SceneObject, Vec3 } from '../../types/scene'
import { vecEqual } from '../../utils/coords'

const AXES = ['X', 'Y', 'Z'] as const
const fmt = (n: number) => String(Number(n.toFixed(4)))

type VectorProps = {
  label: string
  value: Vec3
  step: number
  min?: number
  disabled: boolean
  onCommit: (v: Vec3) => void
}

/** Draft-then-commit vector input: typing is local; Enter or blur submits one edit (one version). */
export function VectorField({ label, value, step, min, disabled, onCommit }: VectorProps) {
  const [draft, setDraft] = useState(() => value.map(fmt))
  const key = value.join(',')
  useEffect(() => {
    setDraft(value.map(fmt))
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  const commit = () => {
    const next = draft.map(Number) as Vec3
    if (next.some((n) => !Number.isFinite(n) || (min !== undefined && n < min))) {
      setDraft(value.map(fmt))
      return
    }
    if (!vecEqual(next, value)) onCommit(next)
  }

  return (
    <>
      <div className="field-label">{label}</div>
      <div className="vector-fields">
        {AXES.map((axis, i) => (
          <label key={axis}>
            <span>{axis}</span>
            <input
              type="number"
              step={step}
              min={min}
              disabled={disabled}
              aria-label={`${label} ${axis}`}
              value={draft[i]}
              onChange={(e) => setDraft((d) => d.map((x, j) => (j === i ? e.target.value : x)))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commit()
                if (e.key === 'Escape') setDraft(value.map(fmt))
              }}
              onBlur={commit}
            />
          </label>
        ))}
      </div>
    </>
  )
}

function describe(obj: SceneObject) {
  const kind = obj.component_type ? obj.component_type.replace(/_/g, ' ') : obj.type.toLowerCase()
  return `${kind.toUpperCase()} · ${obj.mesh.vertices.toLocaleString()} verts`
}

export function PropertiesPanel() {
  const { projectId, selected, objectMap } = useWorkspaceScene()
  const busy = useWorkspaceStore((s) => !!s.activeJob)
  const run = useModelingAction()

  const submit = (label: string, patch: ObjectPatch) =>
    selected && void run(`${label} ${selected.name}`, () => editObject(projectId, selected.object_id, patch))

  return (
    <div className="inspector-section object-properties">
      <div className="section-heading">OBJECT PROPERTIES <span className="property-pill">{selected ? 'SELECTED' : 'NONE'}</span></div>
      {selected ? (
        <>
          <div className="object-name">
            <div className="object-thumb"><Box size={19} /></div>
            <div>
              <strong>{selected.name}</strong>
              <small>{describe(selected)}</small>
            </div>
          </div>
          <div className="property-group">
            <div className="property-title">TRANSFORM <span className="unit-hint">meters · degrees · Z up</span></div>
            <VectorField label="Location" value={selected.location} step={0.01} disabled={busy}
              onCommit={(location) => submit('Moved', { location })} />
            <VectorField label="Rotation" value={selected.rotation} step={1} disabled={busy}
              onCommit={(rotation) => submit('Rotated', { rotation })} />
            <VectorField label="Dimensions" value={selected.dimensions} step={0.01} min={0.001} disabled={busy}
              onCommit={(dimensions) => submit('Resized', { dimensions })} />
          </div>
          <div className="property-group">
            <div className="property-title">DISPLAY</div>
            <label className="toggle-row">
              <span>Visible in viewport</span>
              <input
                type="checkbox"
                disabled={busy}
                checked={selected.visible}
                onChange={(e) => submit(e.target.checked ? 'Showed' : 'Hid', { visible: e.target.checked })}
              />
              <i></i>
            </label>
          </div>
          <div className="property-group">
            <div className="property-title">DETAILS</div>
            <dl className="details-list">
              <dt>ID</dt><dd><code>{selected.object_id}</code></dd>
              {selected.parent_id && (<><dt>Parent</dt><dd>{objectMap.get(selected.parent_id)?.name ?? selected.parent_id}</dd></>)}
              {selected.modifiers.length > 0 && (<><dt>Modifiers</dt><dd>{selected.modifiers.map((m) => m.type.toLowerCase()).join(', ')}</dd></>)}
              {selected.last_modified_version != null && (<><dt>Last changed</dt><dd>v{selected.last_modified_version}</dd></>)}
            </dl>
          </div>
        </>
      ) : (
        <div className="empty-selection">Select an object in the viewport or the hierarchy to edit its properties.</div>
      )}
    </div>
  )
}
