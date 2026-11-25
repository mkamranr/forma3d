import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { ApiError } from '../../services/api'
import { getConversation, postMessage } from '../../services/conversation'
import { followJobEvents, TERMINAL_EVENT_TYPES } from '../../services/jobEvents'
import { compareVersions, fetchAsset, getJob, getScene, listVersions, restoreVersion, stepHistory } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { Conversation, SubmitResponse, Version } from '../../types/scene'
import { projectKeys } from '../projects/hooks'

export const sceneKeys = {
  scene: (projectId: string) => ['scene', projectId] as const,
  versions: (projectId: string) => ['versions', projectId] as const,
  model: (versionId: string) => ['model', versionId] as const,
  conversation: (projectId: string) => ['conversation', projectId] as const,
}

export function useScene(projectId: string) {
  return useQuery({
    queryKey: sceneKeys.scene(projectId),
    queryFn: () => getScene(projectId),
    enabled: !!projectId,
    retry: false,
  })
}

export function useVersions(projectId: string) {
  return useQuery({ queryKey: sceneKeys.versions(projectId), queryFn: () => listVersions(projectId), enabled: !!projectId })
}

export function useConversation(projectId: string) {
  return useQuery({
    queryKey: sceneKeys.conversation(projectId),
    queryFn: () => getConversation(projectId),
    enabled: !!projectId,
  })
}

/** Post a chat message; the reply arrives when the agent job finishes (tracked by useJobTracker). */
export function useSendMessage(projectId: string) {
  const qc = useQueryClient()
  return useCallback(
    async (text: string, selectedObjectIds: string[]): Promise<boolean> => {
      const { activeJob, startJob, notify } = useWorkspaceStore.getState()
      if (activeJob) {
        notify('Still working on the previous request — try again in a moment.')
        return false
      }
      try {
        const res = await postMessage(projectId, text, selectedObjectIds)
        qc.setQueryData(sceneKeys.conversation(projectId), (old: Conversation | undefined) =>
          old ? { ...old, messages: [...old.messages, res.message] } : old,
        )
        startJob(res.job_id, text, 'agent')
        return true
      } catch (err) {
        notify(errorMessage(err))
        return false
      }
    },
    [projectId, qc],
  )
}

export const glbUrl = (version: Version | null | undefined) => version?.assets.find((a) => a.kind === 'glb')?.url ?? null

async function loadGltf(url: string, signal?: AbortSignal): Promise<GLTF> {
  const buffer = await fetchAsset(url, signal)
  return new GLTFLoader().parseAsync(buffer, '')
}

/** Versions are immutable, so a version's GLB is cached forever by version id. */
export function useModel(version: Version | null | undefined) {
  const url = glbUrl(version)
  return useQuery({
    queryKey: sceneKeys.model(version?.id ?? 'none'),
    queryFn: ({ signal }) => loadGltf(url as string, signal),
    enabled: !!url,
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
    structuralSharing: false,
    retry: 1,
  })
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'project_busy') return 'Blender is still building the previous change — try again in a moment.'
    return err.message
  }
  return err instanceof Error ? err.message : 'Something went wrong'
}

/**
 * Run a modeling action (any call that returns 202 + job id) and start tracking its job.
 * Returns a function that resolves to true when the job was queued.
 */
export function useModelingAction() {
  const { startJob, notify } = useWorkspaceStore.getState()
  return useCallback(
    async (label: string, submit: () => Promise<SubmitResponse>): Promise<boolean> => {
      if (useWorkspaceStore.getState().activeJob) {
        notify('Blender is still building the previous change — try again in a moment.')
        return false
      }
      try {
        const res = await submit()
        startJob(res.job_id, label)
        return true
      } catch (err) {
        notify(errorMessage(err))
        return false
      }
    },
    [startJob, notify],
  )
}

/** Follows the active job (SSE, polling fallback) and refreshes scene data when it finishes. */
export function useJobTracker(projectId: string) {
  const qc = useQueryClient()
  const activeJobId = useWorkspaceStore((s) => s.activeJob?.id)
  const scene = useScene(projectId)

  // Resume tracking a job that was already running when the page (re)loaded.
  const serverJob = scene.data?.active_job
  useEffect(() => {
    if (serverJob && !useWorkspaceStore.getState().activeJob) {
      useWorkspaceStore.getState().startJob(serverJob.id, 'Building', serverJob.job_type === 'agent' ? 'agent' : 'edit')
    }
  }, [serverJob])

  useEffect(() => {
    if (!activeJobId) return
    const controller = new AbortController()
    const { jobEvent, endJob, notify } = useWorkspaceStore.getState()
    let finished = false

    const finish = async (status: 'succeeded' | 'failed' | 'cancelled', message: string, versionNumber?: unknown) => {
      if (finished) return
      finished = true
      const job = useWorkspaceStore.getState().activeJob
      const label = job?.label ?? 'Change'
      await Promise.all([
        qc.invalidateQueries({ queryKey: sceneKeys.scene(projectId) }),
        qc.invalidateQueries({ queryKey: sceneKeys.versions(projectId) }),
        qc.invalidateQueries({ queryKey: sceneKeys.conversation(projectId) }),
        qc.invalidateQueries({ queryKey: projectKeys.all }),
      ])
      endJob(activeJobId)
      if (job?.kind === 'agent') {
        // The assistant's reply (success or failure) is in the conversation; only flag new versions.
        if (status === 'succeeded' && versionNumber) notify(`Saved as version ${versionNumber}`)
      } else if (status === 'succeeded') {
        notify(versionNumber ? `${label} — saved as version ${versionNumber}` : `${label} — saved`)
      } else if (status === 'cancelled') {
        notify('Change cancelled — the scene is unchanged')
      } else {
        notify(`Change failed: ${message} The previous version is unchanged.`)
      }
    }

    const poll = async () => {
      while (!finished && !controller.signal.aborted) {
        try {
          const job = await getJob(activeJobId)
          if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
            await finish(job.status, job.error_message ?? 'unknown error')
            return
          }
        } catch (err) {
          if (err instanceof ApiError && err.status === 404) {
            endJob(activeJobId)
            return
          }
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }

    void followJobEvents(activeJobId, {
      signal: controller.signal,
      onEvent: (event) => {
        jobEvent(event)
        if (TERMINAL_EVENT_TYPES.has(event.type)) {
          const status = event.type === 'job.completed' ? 'succeeded' : event.type === 'job.cancelled' ? 'cancelled' : 'failed'
          void finish(status, event.message, event.data?.version_number)
        }
      },
    }).then(() => {
      if (!finished && !controller.signal.aborted) void poll()
    })

    return () => controller.abort()
  }, [activeJobId, projectId, qc])
}

export function useRestoreVersion(projectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (versionId: string) => restoreVersion(projectId, versionId),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: sceneKeys.scene(projectId) }),
        qc.invalidateQueries({ queryKey: sceneKeys.versions(projectId) }),
        qc.invalidateQueries({ queryKey: projectKeys.all }),
      ])
    },
  })
}

/** Undo/redo targets computed from the version tree (same rule as the backend). */
export function historyTargets(versions: Version[] | undefined) {
  const list = versions ?? []
  const head = list.find((v) => v.is_current)
  const undo = head?.parent_version_id ? list.find((v) => v.id === head.parent_version_id) ?? null : null
  const children = head ? list.filter((v) => v.parent_version_id === head.id) : []
  const redo = children.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.version_number - a.version_number)[0] ?? null
  return { head: head ?? null, undo, redo }
}

export function useHistoryStep(projectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (direction: 'undo' | 'redo') => stepHistory(projectId, direction),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: sceneKeys.scene(projectId) }),
        qc.invalidateQueries({ queryKey: sceneKeys.versions(projectId) }),
        qc.invalidateQueries({ queryKey: projectKeys.all }),
      ])
    },
  })
}

export function useCompare(projectId: string, baseId: string | null, targetId: string | null) {
  return useQuery({
    queryKey: ['compare', projectId, baseId, targetId],
    queryFn: () => compareVersions(projectId, baseId as string, targetId as string),
    enabled: !!baseId && !!targetId && baseId !== targetId,
    staleTime: Infinity, // versions are immutable
  })
}
