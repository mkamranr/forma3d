import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { SceneInventory, SceneMaterial, SceneObject } from '../../types/scene'
import { useScene } from './hooks'

const ProjectIdContext = createContext<string>('')

export function WorkspaceProvider({ projectId, children }: { projectId: string; children: ReactNode }) {
  return <ProjectIdContext.Provider value={projectId}>{children}</ProjectIdContext.Provider>
}

export const useProjectId = () => useContext(ProjectIdContext)

const EMPTY: SceneInventory = { objects: [], materials: [], collections: [] }

/** Derived, read-only view of the committed scene for panels. */
export function useWorkspaceScene() {
  const projectId = useProjectId()
  const scene = useScene(projectId)
  const selectedId = useWorkspaceStore((s) => s.selectedId)
  const inventory = scene.data?.inventory ?? EMPTY

  return useMemo(() => {
    const objectMap = new Map<string, SceneObject>(inventory.objects.map((o) => [o.object_id, o]))
    const materialMap = new Map<string, SceneMaterial>(inventory.materials.map((m) => [m.material_id, m]))
    const materialUsers = new Map<string, number>()
    for (const o of inventory.objects) for (const m of o.material_ids) materialUsers.set(m, (materialUsers.get(m) ?? 0) + 1)
    return {
      projectId,
      query: scene,
      version: scene.data?.version ?? null,
      inventory,
      objectMap,
      materialMap,
      materialUsers,
      selected: selectedId ? objectMap.get(selectedId) ?? null : null,
    }
  }, [projectId, scene, inventory, selectedId])
}
