import { useAuthStore } from '../stores/authStore'
import type { TokenResponse, User } from '../types/api'
import { apiRequest } from './api'

function storeSession(data: TokenResponse): User {
  useAuthStore.getState().setSession(data.access_token, data.user)
  return data.user
}

export async function login(email: string, password: string): Promise<User> {
  return storeSession(
    await apiRequest<TokenResponse>('/auth/login', { method: 'POST', body: { email, password }, auth: false }),
  )
}

export async function register(email: string, password: string, displayName: string): Promise<User> {
  return storeSession(
    await apiRequest<TokenResponse>('/auth/register', {
      method: 'POST',
      body: { email, password, display_name: displayName },
      auth: false,
    }),
  )
}

export async function logout(): Promise<void> {
  try {
    await apiRequest<void>('/auth/logout', { method: 'POST', auth: false })
  } finally {
    useAuthStore.getState().clear()
  }
}
