import type { AdminUser, AppConfig, ClientConfig, LlmTestResult, SettingValue } from '../types/config'
import { apiRequest } from './api'

export const getClientConfig = () => apiRequest<ClientConfig>('/config', { auth: false })
export const getAppConfig = () => apiRequest<AppConfig>('/admin/config')
/** `null` resets a setting to the server's .env / built-in default. */
export const saveAppConfig = (values: Record<string, SettingValue | null>) =>
  apiRequest<AppConfig>('/admin/config', { method: 'PUT', body: { values } })
export const testLlm = (values: Record<string, SettingValue | null>) =>
  apiRequest<LlmTestResult>('/admin/config/test-llm', { method: 'POST', body: { values } })

export const listUsers = (q: string) =>
  apiRequest<{ items: AdminUser[]; total: number }>(`/admin/users?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`)
export const updateUser = (id: string, patch: { is_active?: boolean; is_admin?: boolean }) =>
  apiRequest<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch })
