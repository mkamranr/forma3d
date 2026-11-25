import { Archive, ArchiveRestore, Copy, Download, FileBox, MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorMessage } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { ConfirmDeleteDialog, ProjectFormDialog } from '../../features/projects/ProjectDialogs'
import { useDeleteProject, useDuplicateProject, useUpdateProject } from '../../features/projects/hooks'
import { downloadAsset } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { Project } from '../../types/api'

function Item({ icon, children, onClick, disabled, danger }: { icon: ReactNode; children: ReactNode; onClick: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <button role="menuitem" className={`menu-item${danger ? ' danger' : ''}`} onClick={onClick} disabled={disabled}>
      {icon} {children}
    </button>
  )
}

/** The "…" menu of the project bar: project-level actions that are not modeling. */
export function ProjectMenu({ project, onExport }: { project: Project | undefined; onExport: () => void }) {
  const [open, setOpen] = useState(false)
  const [dialog, setDialog] = useState<'edit' | 'delete' | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const { version } = useWorkspaceScene()
  const notify = useWorkspaceStore((s) => s.notify)
  const update = useUpdateProject()
  const duplicate = useDuplicateProject()
  const remove = useDeleteProject()
  const blend = version?.assets.find((a) => a.kind === 'blend')

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const run = (fn: () => void) => () => {
    setOpen(false)
    fn()
  }
  const archived = project?.status === 'archived'

  return (
    <div ref={ref} className="menu-anchor">
      <button className="icon-btn" aria-label="Project actions" aria-haspopup="menu" aria-expanded={open} title="Project actions" disabled={!project} onClick={() => setOpen((v) => !v)}>
        <MoreHorizontal size={18} />
      </button>
      {open && project && (
        <div role="menu" className="popover-menu right">
          <Item icon={<Pencil size={14} />} onClick={run(() => setDialog('edit'))}>Rename & describe…</Item>
          <Item
            icon={<Copy size={14} />}
            disabled={duplicate.isPending}
            onClick={run(() =>
              duplicate.mutate(project.id, {
                onSuccess: (copy) => {
                  notify(`Created “${copy.name}”`)
                  navigate(`/projects/${copy.id}`)
                },
                onError: (err) => notify(errorMessage(err)),
              }),
            )}
          >
            Duplicate
          </Item>
          <div className="menu-sep" />
          <Item icon={<Download size={14} />} disabled={!version} onClick={run(onExport)}>Export…</Item>
          <Item
            icon={<FileBox size={14} />}
            disabled={!blend}
            onClick={run(() =>
              blend &&
              downloadAsset(blend.url, `${project.name.replace(/[^\w.-]+/g, '_') || 'model'}-v${version!.version_number}.blend`).catch(() =>
                notify('Download failed — please try again'),
              ),
            )}
          >
            Download Blender file (.blend)
          </Item>
          <div className="menu-sep" />
          <Item
            icon={archived ? <ArchiveRestore size={14} /> : <Archive size={14} />}
            onClick={run(() =>
              update.mutate(
                { id: project.id, status: archived ? 'active' : 'archived' },
                {
                  onSuccess: () => notify(archived ? 'Project restored — you can edit it again' : 'Project archived — it is now read-only'),
                  onError: (err) => notify(errorMessage(err)),
                },
              ),
            )}
          >
            {archived ? 'Unarchive' : 'Archive'}
          </Item>
          <Item icon={<Trash2 size={14} />} danger onClick={run(() => setDialog('delete'))}>Delete project…</Item>
        </div>
      )}
      <ProjectFormDialog
        open={dialog === 'edit'}
        mode="rename"
        project={project}
        pending={update.isPending}
        error={update.error?.message}
        onClose={() => {
          setDialog(null)
          update.reset()
        }}
        onSubmit={(data) => project && update.mutate({ id: project.id, ...data }, { onSuccess: () => setDialog(null) })}
      />
      <ConfirmDeleteDialog
        project={dialog === 'delete' ? project ?? null : null}
        pending={remove.isPending}
        onClose={() => setDialog(null)}
        onConfirm={() => project && remove.mutate(project.id, { onSuccess: () => navigate('/', { replace: true }) })}
      />
    </div>
  )
}
