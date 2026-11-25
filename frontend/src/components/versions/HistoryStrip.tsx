import { ChevronRight, Clock3, History, ImageOff, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { errorMessage, useRestoreVersion, useVersions } from '../../features/modeling/hooks'
import { useProjectId } from '../../features/modeling/WorkspaceContext'
import { fetchAsset } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { Version } from '../../types/scene'
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

function VersionsDialog() {
  const projectId = useProjectId()
  const open = useWorkspaceStore((s) => s.versionsOpen)
  const busy = useWorkspaceStore((s) => !!s.activeJob)
  const { setVersionsOpen, notify } = useWorkspaceStore.getState()
  const versions = useVersions(projectId)
  const restore = useRestoreVersion(projectId)

  const doRestore = (v: Version) =>
    restore.mutate(v.id, {
      onSuccess: () => notify(`Restored version ${v.version_number} — new changes branch from here`),
      onError: (err) => notify(errorMessage(err)),
    })

  return (
    <Dialog open={open} title="Version history" description="Every change is an immutable version. Restoring never deletes anything." onClose={() => setVersionsOpen(false)}>
      <div className="versions-list" role="list">
        {versions.isLoading && <div className="tree-empty">Loading…</div>}
        {versions.data?.length === 0 && <div className="tree-empty">No versions yet</div>}
        {versions.data?.map((v) => (
          <div role="listitem" key={v.id} className={`version-row ${v.is_current ? 'current' : ''}`}>
            <Thumb version={v} />
            <div className="version-meta">
              <strong>v{v.version_number} {v.is_current && <span className="property-pill">CURRENT</span>}</strong>
              <span>{v.summary || 'Untitled change'}</span>
              <small>{relativeTime(v.created_at)} · {v.object_count} objects</small>
            </div>
            {!v.is_current && (
              <button className="soft-btn small" disabled={busy || restore.isPending} onClick={() => doRestore(v)} title={`Restore version ${v.version_number}`}>
                <RotateCcw size={12} /> Restore
              </button>
            )}
          </div>
        ))}
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
          <span className={`history-item ${v.is_current ? 'current' : ''}`} key={v.id} title={relativeTime(v.created_at)}>
            <span className="history-bullet"></span>v{v.version_number} · {v.summary || 'Change'}
          </span>
        ))}
      </div>
      <button className="history-all" onClick={() => setVersionsOpen(true)}>
        <History size={13} /> View history <ChevronRight size={13} />
      </button>
      <VersionsDialog />
    </div>
  )
}
