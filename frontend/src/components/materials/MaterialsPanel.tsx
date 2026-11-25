import { Copy, Loader2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { errorMessage, useModelingAction, useTextures, useUploadTexture } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { editMaterial, editObject, giveObjectMaterial } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { TEXTURE_PATTERNS, type MaterialPatch, type SceneMaterial, type TextureImage } from '../../types/scene'
import { useAssetObjectUrl } from '../../hooks/useAssetObjectUrl'

const PATTERN_LABELS: Record<string, string> = { brushed_metal: 'Brushed metal' }
const patternLabel = (p: string) => PATTERN_LABELS[p] ?? p[0].toUpperCase() + p.slice(1)

const SWATCHES = ['#C99563', '#5C3A21', '#424953', '#111111', '#8B9CFB', '#6E9C82', '#C96E68', '#E8E9E6']
const PRESETS: [string, MaterialPatch][] = [
  ['Oak', { base_color: '#C99563', metallic: 0, roughness: 0.6 }],
  ['Walnut', { base_color: '#5C3A21', metallic: 0, roughness: 0.55 }],
  ['Black metal', { base_color: '#111111', metallic: 1, roughness: 0.35 }],
  ['Brushed steel', { base_color: '#B8BCC2', metallic: 1, roughness: 0.3 }],
  ['Porcelain', { base_color: '#E8E9E6', metallic: 0, roughness: 0.2 }],
  ['Sage', { base_color: '#6E9C82', metallic: 0, roughness: 0.7 }],
]

const HEX = /^#[0-9A-Fa-f]{6}$/

function SliderRow({ label, value, disabled, onCommit, max = 1, step = 0.01 }: { label: string; value: number; disabled: boolean; onCommit: (v: number) => void; max?: number; step?: number }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => {
    setDraft(value)
  }, [value])
  const commit = () => Math.abs(draft - value) > 0.005 && onCommit(Number(draft.toFixed(3)))
  return (
    <label className="slider-row">
      <span>{label}</span>
      <input
        type="range"
        min={0}
        max={max}
        step={step}
        aria-label={label}
        disabled={disabled}
        value={draft}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <output>{draft.toFixed(max > 1 ? 1 : 2)}</output>
    </label>
  )
}

function TextureThumb({ image, selected, disabled, onPick }: { image: TextureImage; selected: boolean; disabled: boolean; onPick: () => void }) {
  const { objectUrl } = useAssetObjectUrl(image.url, image.format === 'jpeg' ? 'image/jpeg' : 'image/png')
  return (
    <button className={selected ? 'selected' : ''} disabled={disabled} onClick={onPick} title={`${image.name} · ${image.width}×${image.height}`}>
      {objectUrl && <img src={objectUrl} alt={image.name} />}
    </button>
  )
}

/** Procedural pattern or uploaded image, plus its scale (box-projected, in meters). */
function TextureGroup({ material, disabled, apply }: { material: SceneMaterial | null; disabled: boolean; apply: (label: string, p: MaterialPatch) => void }) {
  const { projectId } = useWorkspaceScene()
  const notify = useWorkspaceStore((s) => s.notify)
  const textures = useTextures(projectId)
  const upload = useUploadTexture(projectId)
  const fileInput = useRef<HTMLInputElement>(null)
  const tex = material?.texture ?? null
  const current = tex?.image_id ? 'image' : tex?.pattern ?? ''

  const onFile = (file: File | undefined) => {
    if (!file) return
    upload.mutate(file, {
      onSuccess: (img) => apply('Image texture', { texture_image_id: img.id }),
      onError: (err) => notify(errorMessage(err)),
    })
  }

  return (
    <div className="property-group">
      <div className="property-title">TEXTURE</div>
      <div className="texture-row">
        <select
          aria-label="Texture"
          disabled={disabled}
          value={current}
          onChange={(e) => {
            const v = e.target.value
            if (v === 'image') return fileInput.current?.click()
            apply(v ? `${patternLabel(v)} texture` : 'Removed texture', { texture: v as MaterialPatch['texture'] })
          }}
        >
          <option value="">None</option>
          {TEXTURE_PATTERNS.map((p) => (
            <option key={p} value={p}>{patternLabel(p)}</option>
          ))}
          <option value="image">{tex?.image_id ? 'Uploaded image' : 'Upload image…'}</option>
        </select>
        <button className="soft-btn small" disabled={disabled || upload.isPending} onClick={() => fileInput.current?.click()} title="Upload a PNG or JPEG">
          {upload.isPending ? <Loader2 size={12} className="spin" /> : <Upload size={12} />}
        </button>
        <input ref={fileInput} type="file" accept="image/png,image/jpeg" hidden aria-label="Upload texture image"
          onChange={(e) => (onFile(e.target.files?.[0]), (e.target.value = ''))} />
      </div>
      {(textures.data?.length ?? 0) > 0 && (
        <div className="texture-thumbs">
          {textures.data!.map((img) => (
            <TextureThumb key={img.id} image={img} disabled={disabled} selected={tex?.image_id === img.id}
              onPick={() => apply('Image texture', { texture_image_id: img.id })} />
          ))}
        </div>
      )}
      {tex && (
        <SliderRow label="Scale" value={tex.scale} max={10} step={0.1} disabled={disabled}
          onCommit={(s) => apply('Texture scale', { texture_scale: Math.max(0.05, s) })} />
      )}
    </div>
  )
}

export function MaterialsPanel() {
  const { projectId, selected, inventory, materialMap, materialUsers } = useWorkspaceScene()
  const busy = useWorkspaceStore((s) => !!s.activeJob)
  const run = useModelingAction()
  const material: SceneMaterial | null = selected?.material_ids[0] ? materialMap.get(selected.material_ids[0]) ?? null : null
  const users = material ? materialUsers.get(material.material_id) ?? 0 : 0
  const [hex, setHex] = useState(material?.base_color ?? '#CCCCCC')
  useEffect(() => {
    setHex(material?.base_color ?? '#CCCCCC')
  }, [material?.base_color])

  if (!selected) {
    return (
      <div className="materials-content">
        <div className="section-heading">MATERIALS</div>
        <div className="empty-selection">Select an object to edit its material.</div>
      </div>
    )
  }

  /** Shared materials are edited in place (all users change); objects without one get their own. */
  const apply = (label: string, patch: MaterialPatch) => {
    if (material) void run(`${label} · ${material.name}`, () => editMaterial(projectId, material.material_id, patch))
    else if (['texture', 'texture_image_id', 'texture_scale', 'emission_strength', 'transmission'].some((k) => k in patch))
      useWorkspaceStore.getState().notify('Pick a colour first — this object has no material yet')
    else void run(`${label} · ${selected.name}`, () => giveObjectMaterial(projectId, selected.object_id, patch))
  }

  const makeUnique = () =>
    material &&
    void run(`Own material for ${selected.name}`, () =>
      giveObjectMaterial(projectId, selected.object_id, {
        base_color: material.base_color,
        metallic: material.metallic,
        roughness: material.roughness,
        alpha: material.alpha,
      }),
    )

  const commitHex = (value: string) => HEX.test(value) && value.toUpperCase() !== material?.base_color.toUpperCase() && apply('Colour', { base_color: value.toUpperCase() })

  return (
    <div className="materials-content">
      <div className="section-heading">MATERIAL <span className="property-pill">{selected.name}</span></div>
      <div className="material-preview" style={{ background: material?.base_color ?? '#CCCCCC' }}>
        <span>{material ? 'ACTIVE MATERIAL' : 'NO MATERIAL'}</span>
      </div>
      <div className="material-name">
        <strong>{material ? material.name : 'Default grey'}</strong>
        <small>
          {material
            ? users > 1 ? `Shared by ${users} objects — edits apply to all` : 'Principled BSDF'
            : 'Editing creates a material for this object'}
        </small>
        {material && users > 1 && (
          <button className="soft-btn small" disabled={busy} onClick={makeUnique} title="Give this object its own copy">
            <Copy size={12} /> Make unique
          </button>
        )}
      </div>

      <div className="property-group">
        <div className="property-title">BASE COLOR</div>
        <div className="color-editor">
          <input
            type="color"
            aria-label="Base color"
            disabled={busy}
            value={HEX.test(hex) ? hex : '#cccccc'}
            onChange={(e) => setHex(e.target.value)}
            onBlur={() => commitHex(hex)}
          />
          <input
            className="hex-input"
            aria-label="Hex color"
            disabled={busy}
            value={hex.toUpperCase()}
            onChange={(e) => setHex(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && commitHex(hex)}
            onBlur={() => commitHex(hex)}
          />
        </div>
        <div className="swatches">
          {SWATCHES.map((c) => (
            <button key={c} disabled={busy} style={{ background: c }} title={c} onClick={() => apply('Colour', { base_color: c })} />
          ))}
        </div>
      </div>

      <div className="property-group">
        <div className="property-title">SURFACE</div>
        <SliderRow label="Metallic" value={material?.metallic ?? 0} disabled={busy} onCommit={(metallic) => apply('Metallic', { metallic })} />
        <SliderRow label="Roughness" value={material?.roughness ?? 0.5} disabled={busy} onCommit={(roughness) => apply('Roughness', { roughness })} />
        <SliderRow label="Opacity" value={material?.alpha ?? 1} disabled={busy} onCommit={(alpha) => apply('Opacity', { alpha })} />
        <SliderRow label="Glass" value={material?.transmission ?? 0} disabled={busy} onCommit={(transmission) => apply('Glass', { transmission })} />
        <SliderRow label="Glow" value={material?.emission_strength ?? 0} max={20} step={0.5} disabled={busy}
          onCommit={(emission_strength) => apply('Glow', { emission_strength })} />
      </div>

      <TextureGroup material={material} disabled={busy} apply={apply} />

      <div className="property-group">
        <div className="property-title">SURFACE PRESETS</div>
        <div className="preset-grid">
          {PRESETS.map(([label, patch]) => (
            <button key={label} disabled={busy} onClick={() => apply(label, patch)}>
              <span style={{ background: patch.base_color }}></span>
              {label}
            </button>
          ))}
        </div>
      </div>

      {inventory.materials.length > 0 && (
        <div className="property-group">
          <div className="property-title">SCENE MATERIALS</div>
          <div className="preset-grid">
            {inventory.materials.map((m) => (
              <button
                key={m.material_id}
                disabled={busy || m.material_id === material?.material_id}
                className={m.material_id === material?.material_id ? 'current' : ''}
                title={`Assign ${m.name} to ${selected.name}`}
                onClick={() => void run(`Assigned ${m.name}`, () => editObject(projectId, selected.object_id, { material_id: m.material_id }))}
              >
                <span style={{ background: m.base_color }}></span>
                {m.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
