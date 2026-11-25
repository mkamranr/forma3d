import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChatPanel } from '../../components/chat/ChatPanel'
import { InspectorPanel } from '../../components/layout/InspectorPanel'
import { ProjectBar } from '../../components/layout/ProjectBar'
import { Toast } from '../../components/layout/Toast'
import { TopBar } from '../../components/layout/TopBar'
import { ViewportPanel } from '../../components/viewport/ViewportPanel'
import { ApiError } from '../../services/api'
import { downloadAsset } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { useProject } from '../projects/hooks'
import { glbUrl, useJobTracker, useScene } from './hooks'
import { WorkspaceProvider } from './WorkspaceContext'

function Workspace({ projectId }: { projectId: string }) {
  const project = useProject(projectId)
  const scene = useScene(projectId)
  const showChat = useWorkspaceStore((s) => s.showChat)
  const showInspector = useWorkspaceStore((s) => s.showInspector)
  const activeJob = useWorkspaceStore((s) => s.activeJob)
  const toast = useWorkspaceStore((s) => s.toast)
  const { notify, setVersionsOpen } = useWorkspaceStore.getState()
  useJobTracker(projectId)

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
  const exportGlb = () => {
    const url = glbUrl(version)
    if (!url || !version) return notify('Nothing to export yet — create a model first')
    const base = (project.data?.name ?? 'model').replace(/[^\w.-]+/g, '_').slice(0, 60) || 'model'
    downloadAsset(url, `${base}-v${version.version_number}.glb`).catch(() => notify('Export failed'))
  }

  return (
    <div className="app-shell">
      <TopBar version={version?.version_number ?? null} building={activeJob?.message ?? null} onExport={exportGlb} />
      <ProjectBar project={project.data} onOpenVersions={() => setVersionsOpen(true)} />
      <main className="workspace">
        {showChat && <ChatPanel projectName={project.data?.name ?? 'Loading…'} />}
        <ViewportPanel />
        {showInspector && <InspectorPanel />}
      </main>
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
