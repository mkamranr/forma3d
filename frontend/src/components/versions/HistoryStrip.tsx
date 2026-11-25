import { ArrowRight, ChevronRight, Clock3, GitBranch, GitCompareArrows, History, ImageOff, MessageSquare, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { errorMessage, useCompare, useRestoreVersion, useVersions } from '../../features/modeling/hooks'
import { useProjectId } from '../../features/modeling/WorkspaceContext'
import { fetchAsset } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { FieldChange, Version, VersionCompare } from '../../types/scene'
import { relativeTime } from '../../utils/format'
import { Dialog } from '../ui/Dialog'

/** Asset routes need the bearer token, so images are fetched and shown via object URLs. */
export function useAssetObjectUrl(url: string | null | undefined, type = 'image/png') {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    let created: string | null = null
    fetchAsset(url, controller.signal)
      .then((buf) => {
        created = URL.createObjectURL(new Blob([buf], { type }))
        setObjectUrl(created)
      })
      .catch(() => !controller.signal.aborted && setFailed(true))
    return () => {
      controller.abort()
      if (created) URL.revokeObjectURL(created)
    }
  }, [url, type])
  return { objectUrl, failed }
}

function Thumb({ version }: { version: Version }) {
  const url = version.assets.find((a) => a.kind === 'thumbnail')?.url
  const { objectUrl } = useAssetObjectUrl(url)
  return (
    <div className="version-thumb">
      {objectUrl ? <img src={objectUrl} alt={`Version ${version.version_number}`} /> : <ImageOff size={16} />}
    </div>
  )
}

const FIELD_LABELS: Record<string, string> = {
  name: 'name',
  location: 'position',
  rotation: 'rotation',
  dimensions: 'size',
  material_ids: 'material',
  parent_id: 'parent',
  visible: 'visibility',
  modifiers: 'modifiers',
  component_type: 'component',
  base_color: 'colour',
  metallic: 'metallic',
  roughness: 'roughness',
  alpha: 'opacity',
  vertices: 'vertices',
  camera: 'camera',
  light: 'light',
  emission_color: 'glow colour',
  emission_strength: 'glow',
  transmission: 'glass',
  ior: 'IOR',
  texture: 'texture',
}

function fmt(v: unknown): string {
  if (Array.isArray(v)) return v.every((x) => typeof x === 'number') ? v.map((x) => +(x as number).toFixed(3)).join(' × ') : v.join(', ') || '—'
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') return String(+v.toFixed(3))
  if (typeof v === 'object')
    return Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== null && x !== undefined)
      .map(([k, x]) => `${k.replace(/_/g, ' ')} ${fmt(x)}`)
      .join(', ') || '—'
  return String(v)
}

function Change({ field, change }: { field: string; change: FieldChange }) {
  return (
    <li>
      <span className="diff-field">{FIELD_LABELS[field] ?? field}</span> {fmt(change.before)} <ArrowRight size={10} /> {fmt(change.after)}
    </li>
  )
}

export function CompareView({ diff }: { diff: VersionCompare }) {
  const o = diff.objects
  const m = diff.materials
  const nothing = !o.added.length && !o.removed.length && !o.changed.length && !m.added.length && !m.removed.length && !m.changed.length
  return (
    <div className="version-compare" aria-label="Version comparison">
      <div className="compare-title">
        v{diff.base.version_number} <ArrowRight size={12} /> v{diff.target.version_number}
        <span>{o.unchanged} object{o.unchanged === 1 ? '' : 's'} unchanged</span>
      </div>
      {nothing && <div className="tree-empty">No differences</div>}
      {o.added.map((x) => <div key={`a-${x.object_id}`} className="diff-row added">+ {x.name} <code>{x.object_id}</code></div>)}
      {o.removed.map((x) => <div key={`r-${x.object_id}`} className="diff-row removed">− {x.name} <code>{x.object_id}</code></div>)}
      {o.changed.map((x) => (
        <div key={`c-${x.object_id}`} className="diff-row changed">
          ~ {x.name} <code>{x.object_id}</code>
          <ul>{Object.entries(x.fields).map(([f, c]) => <Change key={f} field={f} change={c} />)}</ul>
        </div>
      ))}
      {m.added.map((x) => <div key={`ma-${x.material_id}`} className="diff-row added">+ material {x.name}</div>)}
      {m.removed.map((x) => <div key={`mr-${x.material_id}`} className="diff-row removed">− material {x.name}</div>)}
      {m.changed.map((x) => (
        <div key={`mc-${x.material_id}`} className="diff-row changed">
          ~ material {x.name}
          <ul>{Object.entries(x.fields).map(([f, c]) => <Change key={f} field={f} change={c} />)}</ul>
        </div>
      ))}
    </div>
  )
}

function VersionsDialog() {
  const projectId = useProjectId()
  const open = useWorkspaceStore((s) => s.versionsOpen)
  const focusVersionId = useWorkspaceStore((s) => s.focusVersionId)
  const busy = useWorkspaceStore((s) => !!s.activeJob)
  const { setVersionsOpen, notify, showMessage, setShowChat } = useWorkspaceStore.getState()
  const versions = useVersions(projectId)
  const restore = useRestoreVersion(projectId)
  const [picked, setPicked] = useState<string[]>([])
  const byId = useMemo(() => new Map((versions.data ?? []).map((v) => [v.id, v])), [versions.data])

  // Compare older -> newer, whatever order they were picked in.
  const [baseId, targetId] = useMemo(() => {
    if (picked.length !== 2) return [null, null]
    const [a, b] = picked.map((id) => byId.get(id))
    if (!a || !b) return [null, null]
    return a.version_number < b.version_number ? [a.id, b.id] : [b.id, a.id]
  }, [picked, byId])
  const compare = useCompare(projectId, baseId, targetId)

  useEffect(() => {
    if (!open) setPicked([])
  }, [open])
  useEffect(() => {
    if (open && focusVersionId) document.getElementById(`version-${focusVersionId}`)?.scrollIntoView?.({ block: 'center' })
  }, [open, focusVersionId, versions.data])

  const togglePick = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]))

  const doRestore = (v: Version) =>
    restore.mutate(v.id, {
      onSuccess: () => notify(`Restored version ${v.version_number} — new changes branch from here`),
      onError: (err) => notify(errorMessage(err)),
    })

  const goToMessage = (v: Version) => {
    if (!v.message_id) return
    setVersionsOpen(false)
    setShowChat(true)
    showMessage(v.message_id)
  }

  return (
    <Dialog
      open={open}
      title="Version history"
      description="Every change is an immutable version. Restoring never deletes anything; a change made after restoring starts a new branch."
      onClose={() => setVersionsOpen(false)}
    >
      <div className="versions-hint">
        <GitCompareArrows size={12} /> Tick two versions to compare them.
        {picked.length > 0 && <button className="link-btn" onClick={() => setPicked([])}><X size={11} /> Clear</button>}
      </div>
      {compare.data && <CompareView diff={compare.data} />}
      {compare.isError && <div className="chat-chip muted">Could not compare these versions.</div>}
      <div className="versions-list" role="list">
        {versions.isLoading && <div className="tree-empty">Loading…</div>}
        {versions.data?.length === 0 && <div className="tree-empty">No versions yet</div>}
        {versions.data?.map((v, i) => {
          const parent = v.parent_version_id ? byId.get(v.parent_version_id) : undefined
          const previous = versions.data?.[i + 1]
          const branched = !!parent && !!previous && parent.id !== previous.id
          return (
            <div
              role="listitem"
              id={`version-${v.id}`}
              key={v.id}
              className={`version-row ${v.is_current ? 'current' : ''} ${focusVersionId === v.id ? 'focused' : ''}`}
            >
              <input
                type="checkbox"
                className="version-pick"
                aria-label={`Compare version ${v.version_number}`}
                checked={picked.includes(v.id)}
                onChange={() => togglePick(v.id)}
              />
              <Thumb version={v} />
              <div className="version-meta">
                <strong>
                  v{v.version_number} {v.is_current && <span className="property-pill">CURRENT</span>}
                  {branched && (
                    <span className="branch-pill" title="Made after restoring an earlier version">
                      <GitBranch size={10} /> from v{parent.version_number}
                    </span>
                  )}
                </strong>
                <span>{v.summary || 'Untitled change'}</span>
                {v.prompt && <em className="version-prompt">“{v.prompt}”</em>}
                <small>{relativeTime(v.created_at)} · {v.object_count} objects · {v.source === 'chat' ? 'assistant' : 'edit'}</small>
              </div>
              <div className="version-actions">
                {v.message_id && (
                  <button className="soft-btn small" onClick={() => goToMessage(v)} title="Show the conversation that made this change">
                    <MessageSquare size={12} /> Chat
                  </button>
                )}
                {!v.is_current && (
                  <button className="soft-btn small" disabled={busy || restore.isPending} onClick={() => doRestore(v)} title={`Restore version ${v.version_number}`}>
                    <RotateCcw size={12} /> Restore
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </Dialog>
  )
}

export function HistoryStrip() {
  const projectId = useProjectId()
  const versions = useVersions(projectId)
  const setVersionsOpen = useWorkspaceStore((s) => s.setVersionsOpen)
  const recent = versions.data?.slice(0, 3) ?? []
  return (
    <div className="history-strip">
      <div className="history-title"><Clock3 size={14} /> RECENT VERSIONS</div>
      <div className="history-items">
        {recent.length === 0 && <span className="history-item muted">No versions yet</span>}
        {recent.map((v) => (
          <button
            className={`history-item ${v.is_current ? 'current' : ''}`}
            key={v.id}
            title={v.prompt ? `“${v.prompt}” · ${relativeTime(v.created_at)}` : relativeTime(v.created_at)}
            onClick={() => setVersionsOpen(true, v.id)}
          >
            <span className="history-bullet"></span>v{v.version_number} · {v.summary || 'Change'}
          </button>
        ))}
      </div>
      <button className="history-all" onClick={() => setVersionsOpen(true)}>
        <History size={13} /> View history <ChevronRight size={13} />
      </button>
      <VersionsDialog />
    </div>
  )
}
