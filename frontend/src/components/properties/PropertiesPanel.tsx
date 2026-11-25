import { Box, Lightbulb, Video } from 'lucide-react'
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

/** Single number, same draft-then-commit behaviour as VectorField. */
export function ScalarField({ label, value, step, min, max, unit, disabled, onCommit }: {
  label: string
  value: number
  step: number
  min?: number
  max?: number
  unit?: string
  disabled: boolean
  onCommit: (v: number) => void
}) {
  const [draft, setDraft] = useState(fmt(value))
  useEffect(() => setDraft(fmt(value)), [value])
  const commit = () => {
    const n = Number(draft)
    if (!Number.isFinite(n) || (min !== undefined && n < min) || (max !== undefined && n > max)) return setDraft(fmt(value))
    if (n !== value) onCommit(n)
  }
  return (
    <>
      <div className="field-label">{label}</div>
      <div className="vector-fields">
        <label>
          <input
            type="number"
            step={step}
            min={min}
            max={max}
            disabled={disabled}
            aria-label={label}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit()
              if (e.key === 'Escape') setDraft(fmt(value))
            }}
            onBlur={commit}
          />
          {unit && <span>{unit}</span>}
        </label>
      </div>
    </>
  )
}

function describe(obj: SceneObject) {
  if (obj.type === 'LIGHT') return `${(obj.light?.light_type ?? 'point').toUpperCase()} LIGHT`
  if (obj.type === 'CAMERA') return `CAMERA${obj.camera?.active ? ' · ACTIVE' : ''}`
  const kind = obj.component_type ? obj.component_type.replace(/_/g, ' ') : obj.type.toLowerCase()
  return `${kind.toUpperCase()} · ${obj.mesh.vertices.toLocaleString()} verts`
}

function LightProperties({ obj, disabled, submit }: { obj: SceneObject; disabled: boolean; submit: (label: string, p: ObjectPatch) => void }) {
  const light = obj.light
  if (!light) return null
  return (
    <div className="property-group">
      <div className="property-title">LIGHT</div>
      <ScalarField
        label={light.light_type === 'sun' ? 'Strength' : 'Power'}
        unit={light.light_type === 'sun' ? '' : 'W'}
        value={light.energy}
        step={light.light_type === 'sun' ? 0.5 : 10}
        min={0}
        max={light.light_type === 'sun' ? 1000 : 100000}
        disabled={disabled}
        onCommit={(light_energy) => submit('Changed the power of', { light_energy })}
      />
      <div className="field-label">Color</div>
      <label className="toggle-row">
        <span>{light.color}</span>
        <input
          type="color"
          aria-label="Light color"
          disabled={disabled}
          defaultValue={light.color.toLowerCase()}
          key={light.color}
          onBlur={(e) => e.target.value.toUpperCase() !== light.color && submit('Recolored', { light_color: e.target.value.toUpperCase() })}
        />
      </label>
    </div>
  )
}

function CameraProperties({ obj, disabled, submit }: { obj: SceneObject; disabled: boolean; submit: (label: string, p: ObjectPatch) => void }) {
  const cam = obj.camera
  if (!cam) return null
  return (
    <div className="property-group">
      <div className="property-title">CAMERA</div>
      <ScalarField label="Focal length" unit="mm" value={cam.lens} step={1} min={8} max={300} disabled={disabled}
        onCommit={(camera_lens) => submit('Changed the lens of', { camera_lens })} />
      <label className="toggle-row" style={{ marginTop: 8 }}>
        <span>Active render camera</span>
        <input
          type="checkbox"
          disabled={disabled || cam.active}
          checked={cam.active}
          onChange={() => submit('Made active:', { active_camera: true })}
        />
        <i></i>
      </label>
    </div>
  )
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
            <div className="object-thumb">
              {selected.type === 'LIGHT' ? <Lightbulb size={19} /> : selected.type === 'CAMERA' ? <Video size={19} /> : <Box size={19} />}
            </div>
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
            {selected.type === 'MESH' && (
              <VectorField label="Dimensions" value={selected.dimensions} step={0.01} min={0.001} disabled={busy}
                onCommit={(dimensions) => submit('Resized', { dimensions })} />
            )}
          </div>
          {selected.type === 'LIGHT' && <LightProperties obj={selected} disabled={busy} submit={submit} />}
          {selected.type === 'CAMERA' && <CameraProperties obj={selected} disabled={busy} submit={submit} />}
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
