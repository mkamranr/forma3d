import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChatPanel } from '../../components/chat/ChatPanel'
import { InspectorPanel } from '../../components/layout/InspectorPanel'
import { ProjectBar } from '../../components/layout/ProjectBar'
import { ExportDialog, RenderDialog } from '../../components/outputs/Outputs'
import { Toast } from '../../components/layout/Toast'
import { TopBar } from '../../components/layout/TopBar'
import { ViewportPanel } from '../../components/viewport/ViewportPanel'
import { ApiError } from '../../services/api'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { useProject } from '../projects/hooks'
import { errorMessage, historyTargets, useHistoryStep, useJobTracker, useScene, useVersions } from './hooks'
import { WorkspaceProvider } from './WorkspaceContext'

function Workspace({ projectId }: { projectId: string }) {
  const project = useProject(projectId)
  const scene = useScene(projectId)
  const showChat = useWorkspaceStore((s) => s.showChat)
  const showInspector = useWorkspaceStore((s) => s.showInspector)
  const activeJob = useWorkspaceStore((s) => s.activeJob)
  const toast = useWorkspaceStore((s) => s.toast)
  const { notify, setVersionsOpen } = useWorkspaceStore.getState()
  const [outputDialog, setOutputDialog] = useState<'export' | 'render' | null>(null)
  useJobTracker(projectId)
  const versions = useVersions(projectId)
  const targets = historyTargets(versions.data)
  const step = useHistoryStep(projectId)
  const historyBusy = !!activeJob || step.isPending

  const move = useCallback(
    (direction: 'undo' | 'redo') => {
      const to = direction === 'undo' ? targets.undo : targets.redo
      if (!to || historyBusy) return
      step.mutate(direction, {
        onSuccess: (res) => notify(`${direction === 'undo' ? 'Undone' : 'Redone'} — now at version ${res.version_number}`),
        onError: (err) => notify(errorMessage(err)),
      })
    },
    [targets.undo, targets.redo, historyBusy, step, notify],
  )

  // Ctrl/Cmd+Z = undo, Ctrl/Cmd+Shift+Z or Ctrl+Y = redo (not while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if (!(e.ctrlKey || e.metaKey)) return
      const key = e.key.toLowerCase()
      if (key === 'z' && !e.shiftKey) move('undo')
      else if ((key === 'z' && e.shiftKey) || key === 'y') move('redo')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [move])

  if (project.error) {
    const notFound = project.error instanceof ApiError && project.error.status === 404
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-[#f5f6f8] text-[#202633]">
        <h1 className="font-[Manrope] text-xl font-bold">{notFound ? 'Project not found' : 'Could not open project'}</h1>
        <p className="text-sm text-[#8b92a0]">
          {notFound ? 'It may have been deleted, or you may not have access.' : project.error.message}
        </p>
        <Link to="/" className="text-sm font-semibold text-[#6759d9]">Back to projects</Link>
      </div>
    )
  }

  const version = scene.data?.version ?? null
  const projectName = project.data?.name ?? 'model'

  return (
    <div className="app-shell">
      <TopBar version={version?.version_number ?? null} building={activeJob?.message ?? null} onExport={() => setOutputDialog('export')} />
      <ProjectBar
        project={project.data}
        onOpenVersions={() => setVersionsOpen(true)}
        onRender={version ? () => setOutputDialog('render') : undefined}
        history={{
          undoTo: targets.undo?.version_number ?? null,
          redoTo: targets.redo?.version_number ?? null,
          disabled: historyBusy,
          onUndo: () => move('undo'),
          onRedo: () => move('redo'),
        }}
      />
      <main className="workspace">
        {showChat && <ChatPanel projectName={project.data?.name ?? 'Loading…'} />}
        <ViewportPanel />
        {showInspector && <InspectorPanel />}
      </main>
      <ExportDialog open={outputDialog === 'export'} onClose={() => setOutputDialog(null)} projectName={projectName} />
      <RenderDialog open={outputDialog === 'render'} onClose={() => setOutputDialog(null)} projectName={projectName} />
      <Toast message={toast} />
    </div>
  )
}

export function WorkspacePage() {
  const { projectId = '' } = useParams()
  const reset = useWorkspaceStore((s) => s.reset)
  // Each project opens with a fresh workspace.
  useEffect(() => {
    reset()
  }, [projectId, reset])
  return (
    <WorkspaceProvider projectId={projectId}>
      <Workspace key={projectId} projectId={projectId} />
    </WorkspaceProvider>
  )
}
