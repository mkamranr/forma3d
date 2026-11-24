import { create } from 'zustand'
import type { User } from '../types/api'

type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

type AuthState = {
  status: AuthStatus
  user: User | null
  /** Short-lived access token. Kept in memory only (never localStorage). */
  accessToken: string | null
  setSession: (accessToken: string, user: User) => void
  clear: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  status: 'loading',
  user: null,
  accessToken: null,
  setSession: (accessToken, user) => set({ accessToken, user, status: 'authenticated' }),
  clear: () => set({ accessToken: null, user: null, status: 'anonymous' }),
}))
