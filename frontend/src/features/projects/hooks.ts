import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createProject,
  deleteProject,
  duplicateProject,
  getProject,
  listProjects,
  updateProject,
} from '../../services/projects'
import type { Project, ProjectListParams, ProjectStatus } from '../../types/api'

export const projectKeys = {
  all: ['projects'] as const,
  list: (params: ProjectListParams) => ['projects', 'list', params] as const,
  detail: (id: string) => ['projects', 'detail', id] as const,
}

export function useProjects(params: ProjectListParams) {
  return useQuery({
    queryKey: projectKeys.list(params),
    queryFn: () => listProjects(params),
    placeholderData: keepPreviousData,
  })
}

export function useProject(id: string) {
  return useQuery({ queryKey: projectKeys.detail(id), queryFn: () => getProject(id), enabled: !!id, retry: false })
}

function useInvalidateProjects() {
  const qc = useQueryClient()
  return (project?: Project) => {
    void qc.invalidateQueries({ queryKey: projectKeys.all })
    if (project) qc.setQueryData(projectKeys.detail(project.id), project)
  }
}

export function useCreateProject() {
  const invalidate = useInvalidateProjects()
  return useMutation({ mutationFn: createProject, onSuccess: invalidate })
}

export function useUpdateProject() {
  const invalidate = useInvalidateProjects()
  return useMutation({
    mutationFn: (vars: { id: string; name?: string; description?: string; status?: ProjectStatus }) => {
      const { id, ...data } = vars
      return updateProject(id, data)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteProject() {
  const invalidate = useInvalidateProjects()
  return useMutation({ mutationFn: deleteProject, onSuccess: () => invalidate() })
}

export function useDuplicateProject() {
  const invalidate = useInvalidateProjects()
  return useMutation({ mutationFn: duplicateProject, onSuccess: invalidate })
}
