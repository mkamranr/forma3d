import { QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { ConfigurationPage } from '../features/admin/ConfigurationPage'
import { LoginPage, RegisterPage } from '../features/auth/AuthPages'
import { DashboardPage } from '../features/projects/DashboardPage'
import { refreshSession } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import { createQueryClient } from './queryClient'

const queryClient = createQueryClient()

// three.js / R3F is only needed in the workspace: keep it out of the auth/dashboard bundle.
const WorkspacePage = lazy(() =>
  import('../features/modeling/WorkspacePage').then((m) => ({ default: m.WorkspacePage })),
)

function FullScreenStatus() {
  return <div className="grid min-h-screen place-items-center bg-surface text-[0.9062rem] text-ink-400">Loading…</div>
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  const location = useLocation()
  if (status === 'loading') return <FullScreenStatus />
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return <>{children}</>
}

/** Signed-in administrators only; everyone else goes back to their projects. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  return (
    <RequireAuth>
      {user?.is_admin ? children : <Navigate to="/" replace />}
    </RequireAuth>
  )
}

export function GuestOnly({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  if (status === 'loading') return <FullScreenStatus />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <>{children}</>
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
      <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
      <Route path="/" element={<RequireAuth><DashboardPage /></RequireAuth>} />
      <Route path="/configuration" element={<RequireAdmin><ConfigurationPage /></RequireAdmin>} />
      <Route
        path="/projects/:projectId"
        element={<RequireAuth><Suspense fallback={<FullScreenStatus />}><WorkspacePage /></Suspense></RequireAuth>}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  // Restore the session from the httpOnly refresh cookie on first load.
  useEffect(() => {
    void refreshSession()
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
