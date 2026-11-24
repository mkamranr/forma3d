import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChatPanel } from '../../components/chat/ChatPanel'
import { InspectorPanel } from '../../components/layout/InspectorPanel'
import { ProjectBar } from '../../components/layout/ProjectBar'
import { Toast } from '../../components/layout/Toast'
import { TopBar } from '../../components/layout/TopBar'
import { ViewportPanel } from '../../components/viewport/ViewportPanel'
import { ApiError } from '../../services/api'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { useProject } from '../projects/hooks'

export function WorkspacePage() {
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const showChat = useWorkspaceStore((s) => s.showChat)
  const showInspector = useWorkspaceStore((s) => s.showInspector)
  const version = useWorkspaceStore((s) => s.version)
  const toast = useWorkspaceStore((s) => s.toast)
  const { notify, saveSnapshot, reset } = useWorkspaceStore.getState()

  // Each project opens with a fresh workspace.
  useEffect(() => reset(), [projectId, reset])

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

  const projectName = project.data?.name ?? 'Loading…'
  return (
    <div className="app-shell">
      <TopBar version={version} onExport={() => notify('Export is a UI demo — connect the Blender worker to generate GLB')} />
      <ProjectBar project={project.data} onSaveVersion={saveSnapshot} />
      <main className="workspace">
        {showChat && <ChatPanel projectName={projectName} />}
        <ViewportPanel />
        {showInspector && <InspectorPanel />}
      </main>
      <Toast message={toast} />
    </div>
  )
}
