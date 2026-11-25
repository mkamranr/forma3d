import { AlertTriangle, Camera, Download, FileBox, Image as ImageIcon, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useCreateOutput, useOutput, useRenders, type OutputKind } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { downloadOutput } from '../../services/outputs'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { ExportFormat, Output, RenderQuality, Unit } from '../../types/scene'
import { relativeTime } from '../../utils/format'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { useAssetObjectUrl } from '../../hooks/useAssetObjectUrl'

export const FORMATS: { id: ExportFormat; label: string; hint: string; ext: string }[] = [
  { id: 'glb', label: 'GLB', hint: 'Web, AR and game engines — one file with textures', ext: 'glb' },
  { id: 'gltf', label: 'glTF', hint: 'Separate .gltf, .bin and texture files (zip)', ext: 'zip' },
  { id: 'obj', label: 'OBJ', hint: 'Mesh + .mtl materials for any 3D tool (zip)', ext: 'zip' },
  { id: 'stl', label: 'STL', hint: 'Geometry only — for 3D printing', ext: 'stl' },
]

export const QUALITY_LABELS: Record<RenderQuality, string> = {
  draft: 'Draft · fast preview',
  standard: 'Standard',
  high: 'High · final image',
}

const SIZES = [
  { label: 'HD 1280×720', w: 1280, h: 720 },
  { label: 'Full HD 1920×1080', w: 1920, h: 1080 },
  { label: 'Square 1080×1080', w: 1080, h: 1080 },
  { label: 'Portrait 1080×1350', w: 1080, h: 1350 },
]

const PENDING = new Set(['queued', 'running'])

function fileName(projectName: string, out: Output, kind: OutputKind) {
  const base = projectName.replace(/[^\w.-]+/g, '_').slice(0, 60) || 'model'
  const v = out.version_number ? `-v${out.version_number}` : ''
  if (kind === 'render') return `${base}${v}-render.png`
  const fmt = FORMATS.find((f) => f.id === out.settings.format) ?? FORMATS[0]
  return `${base}${v}.${fmt.ext}`
}

const formatLabel = (out: Output) => FORMATS.find((f) => f.id === out.settings.format)?.label ?? 'file'

function useDownload(projectName: string) {
  const notify = useWorkspaceStore((s) => s.notify)
  const [busy, setBusy] = useState(false)
  const run = async (out: Output, kind: OutputKind) => {
    if (!out.url) return
    setBusy(true)
    try {
      await downloadOutput(out.url, fileName(projectName, out, kind))
    } catch {
      notify('Download failed — please try again')
    } finally {
      setBusy(false)
    }
  }
  return { busy, run }
}

function Progress({ out, kind }: { out: Output; kind: OutputKind }) {
  const label = out.progress?.message || (out.status === 'queued' ? 'Waiting for Blender…' : kind === 'render' ? 'Rendering…' : 'Exporting…')
  return (
    <div className="chat-chip muted">
      <Loader2 size={12} className="spin" /> {label}
    </div>
  )
}

function RenderImage({ out, className }: { out: Output; className?: string }) {
  const { objectUrl, failed } = useAssetObjectUrl(out.url)
  if (failed) return <div className={`render-image empty ${className ?? ''}`}><AlertTriangle size={16} /></div>
  if (!objectUrl) return <div className={`render-image empty ${className ?? ''}`}><Loader2 size={16} className="spin" /></div>
  return <img className={`render-image ${className ?? ''}`} src={objectUrl} alt={`Render of version ${out.version_number ?? ''}`} />
}

/** The export/render the assistant started for a chat message: progress, then download / preview. */
export function OutputChip({ kind, id, projectName }: { kind: OutputKind; id: string; projectName: string }) {
  const output = useOutput(kind, id)
  const { busy, run } = useDownload(projectName)
  const out = output.data
  if (output.isError) return <div className="chat-chip muted">{errorMessage(output.error)}</div>
  if (!out || PENDING.has(out.status)) return out ? <Progress out={out} kind={kind} /> : <Progress out={{ status: 'queued' } as Output} kind={kind} />
  if (out.status === 'succeeded' && !out.url)
    return (
      <div className="chat-chip muted" title="Old renders and exports are removed after the retention period">
        <AlertTriangle size={12} /> This {kind === 'render' ? 'render' : 'file'} has expired — {kind === 'render' ? 'render' : 'export'} again
      </div>
    )
  if (out.status !== 'succeeded' || !out.url)
    return (
      <div className="chat-chip muted" title={out.error_message ?? ''}>
        <AlertTriangle size={12} /> {kind === 'render' ? 'Render' : 'Export'} failed{out.error_message ? ` — ${out.error_message}` : ''}
      </div>
    )
  const v = out.version_number ? ` · v${out.version_number}` : ''
  if (kind === 'render')
    return (
      <div className="render-result">
        <button className="render-preview" onClick={() => void run(out, kind)} title="Download the full-size PNG">
          <RenderImage out={out} />
        </button>
        <button className="chat-action" onClick={() => void run(out, kind)} disabled={busy}>
          {busy ? <Loader2 size={13} className="spin" /> : <Download size={13} />} Download PNG{v}
        </button>
      </div>
    )
  return (
    <button className="chat-action" onClick={() => void run(out, kind)} disabled={busy}>
      {busy ? <Loader2 size={13} className="spin" /> : <Download size={13} />} Download {formatLabel(out)}{v}
    </button>
  )
}

function Choice<T extends string>({ value, options, onChange, name }: { value: T; options: { id: T; label: string; hint?: string }[]; onChange: (v: T) => void; name: string }) {
  return (
    <div className="output-choices" role="radiogroup" aria-label={name}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={value === o.id}
          className={value === o.id ? 'selected' : ''}
          onClick={() => onChange(o.id)}
          title={o.hint}
        >
          <strong>{o.label}</strong>
          {o.hint && <small>{o.hint}</small>}
        </button>
      ))}
    </div>
  )
}

/** Export the current version in any allowed format (spec §13.8). */
export function ExportDialog({ open, onClose, projectName }: { open: boolean; onClose: () => void; projectName: string }) {
  const { projectId, version, inventory } = useWorkspaceScene()
  const [format, setFormat] = useState<ExportFormat>('glb')
  const [unit, setUnit] = useState<Unit>('m')
  const [includeHidden, setIncludeHidden] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const create = useCreateOutput(projectId)
  const hasHidden = inventory.objects.some((o) => !o.visible)

  const start = () =>
    create.mutate(
      { kind: 'export', body: { format, unit, include_hidden: includeHidden, version_id: version?.id } },
      { onSuccess: (out) => setJobId(out.id) },
    )
  const close = () => {
    setJobId(null)
    create.reset()
    onClose()
  }

  return (
    <Dialog open={open} title="Export model" description={version ? `Version ${version.version_number}` : 'Nothing to export yet'} onClose={close}>
      <div className="output-form">
        <div className="field-label">Format</div>
        <Choice name="Format" value={format} options={FORMATS} onChange={(f) => (setFormat(f), setJobId(null))} />
        {format !== 'glb' && (
          <>
            <div className="field-label">Units</div>
            <Choice
              name="Units"
              value={unit}
              onChange={(u) => (setUnit(u), setJobId(null))}
              options={[
                { id: 'm', label: 'Meters' },
                { id: 'cm', label: 'Centimeters' },
                { id: 'mm', label: 'Millimeters' },
              ]}
            />
            {hasHidden && (
              <label className="toggle-row">
                Include hidden objects
                <input type="checkbox" checked={includeHidden} onChange={(e) => (setIncludeHidden(e.target.checked), setJobId(null))} />
              </label>
            )}
          </>
        )}
        {create.isError && <p className="output-error">{errorMessage(create.error)}</p>}
        <div className="output-footer">
          {jobId ? (
            <OutputChip kind="export" id={jobId} projectName={projectName} />
          ) : (
            <Button onClick={start} disabled={!version || create.isPending}>
              {create.isPending ? <Loader2 size={14} className="spin" /> : <FileBox size={14} />} Export {FORMATS.find((f) => f.id === format)?.label}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/** High-quality Blender render of the current version, plus the project's recent renders. */
export function RenderDialog({ open, onClose, projectName }: { open: boolean; onClose: () => void; projectName: string }) {
  const { projectId, version, inventory } = useWorkspaceScene()
  const cameras = inventory.objects.filter((o) => o.type === 'CAMERA')
  const [quality, setQuality] = useState<RenderQuality>('standard')
  const [size, setSize] = useState(0)
  const [camera, setCamera] = useState<string>('')
  const [transparent, setTransparent] = useState(false)
  const create = useCreateOutput(projectId)
  const renders = useRenders(open ? projectId : '')
  const { run } = useDownload(projectName)

  const start = () =>
    create.mutate({
      kind: 'render',
      body: {
        engine: 'cycles',
        quality,
        width: SIZES[size].w,
        height: SIZES[size].h,
        camera_object_id: camera || null,
        transparent_background: transparent,
        version_id: version?.id,
      },
    })

  const recent = (renders.data ?? []).slice(0, 6)
  return (
    <Dialog open={open} title="Render image" description="A path-traced image of the model, made in Blender" onClose={onClose}>
      <div className="output-form">
        <div className="field-label">Quality</div>
        <Choice
          name="Quality"
          value={quality}
          onChange={setQuality}
          options={(Object.keys(QUALITY_LABELS) as RenderQuality[]).map((q) => ({ id: q, label: QUALITY_LABELS[q] }))}
        />
        <div className="field-label">Size</div>
        <select className="output-select" value={size} onChange={(e) => setSize(Number(e.target.value))} aria-label="Size">
          {SIZES.map((s, i) => (
            <option key={s.label} value={i}>{s.label}</option>
          ))}
        </select>
        <div className="field-label">Camera</div>
        <select className="output-select" value={camera} onChange={(e) => setCamera(e.target.value)} aria-label="Camera">
          <option value="">{cameras.some((c) => c.camera?.active) ? 'Active scene camera' : 'Automatic framing'}</option>
          {cameras.map((c) => (
            <option key={c.object_id} value={c.object_id}>{c.name}</option>
          ))}
        </select>
        <label className="toggle-row">
          Transparent background
          <input type="checkbox" checked={transparent} onChange={(e) => setTransparent(e.target.checked)} />
        </label>
        {create.isError && <p className="output-error">{errorMessage(create.error)}</p>}
        <div className="output-footer">
          <Button onClick={start} disabled={!version || create.isPending}>
            {create.isPending ? <Loader2 size={14} className="spin" /> : <Camera size={14} />} Render v{version?.version_number ?? '–'}
          </Button>
        </div>
        {recent.length > 0 && (
          <>
            <div className="field-label">Recent renders</div>
            <div className="render-gallery">
              {recent.map((r) => (
                <button
                  key={r.id}
                  className="render-tile"
                  disabled={r.status !== 'succeeded' || !r.url}
                  onClick={() => void run(r, 'render')}
                  title={r.status === 'succeeded' ? 'Download PNG' : r.error_message ?? r.status}
                >
                  {r.status === 'succeeded' && r.url ? (
                    <RenderImage out={r} />
                  ) : PENDING.has(r.status) ? (
                    <div className="render-image empty"><Loader2 size={16} className="spin" /></div>
                  ) : (
                    <div className="render-image empty"><ImageIcon size={16} /></div>
                  )}
                  <small>
                    v{r.version_number} · {String(r.settings.quality ?? '')} · {relativeTime(r.created_at)}
                  </small>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}
