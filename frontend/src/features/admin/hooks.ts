import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getAppConfig, getClientConfig, listUsers, saveAppConfig, testLlm, updateUser } from '../../services/admin'
import type { ClientConfig, SettingValue } from '../../types/config'

export const configKeys = {
  client: ['config', 'client'] as const,
  app: ['config', 'app'] as const,
  users: (q: string) => ['config', 'users', q] as const,
}

const CLIENT_DEFAULTS: ClientConfig = {
  registration_open: true,
  default_render_quality: 'standard',
  default_render_size: '1280x720',
  default_export_format: 'glb',
  default_export_unit: 'm',
}

/** Public settings (sign-up allowed, dialog defaults); falls back to built-in defaults if unavailable. */
export function useClientConfig() {
  const q = useQuery({ queryKey: configKeys.client, queryFn: getClientConfig, staleTime: 60_000, retry: false })
  return { ...CLIENT_DEFAULTS, ...(q.data ?? {}), loaded: q.isSuccess || q.isError }
}

export function useAppConfig() {
  return useQuery({ queryKey: configKeys.app, queryFn: getAppConfig })
}

export function useSaveAppConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (values: Record<string, SettingValue | null>) => saveAppConfig(values),
    onSuccess: (data) => {
      qc.setQueryData(configKeys.app, data)
      void qc.invalidateQueries({ queryKey: configKeys.client })
    },
  })
}

export function useTestLlm() {
  return useMutation({ mutationFn: (values: Record<string, SettingValue | null>) => testLlm(values) })
}

export function useUsers(q: string) {
  return useQuery({ queryKey: configKeys.users(q), queryFn: () => listUsers(q), placeholderData: keepPreviousData })
}

export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; is_active?: boolean; is_admin?: boolean }) => {
      const { id, ...patch } = v
      return updateUser(id, patch)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['config', 'users'] }),
  })
}
