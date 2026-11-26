import { useClientConfig } from '../admin/hooks'
import { Cuboid } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { ApiError } from '../../services/api'
import { login, register } from '../../services/auth'

function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle: string; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center bg-surface px-4 font-sans text-ink-900">
      <div className="w-full max-w-sm">
        <div className="brand mb-8 justify-center">
          <div className="brand-mark"><Cuboid size={20} strokeWidth={2.4} /></div>
          <span>forma<span className="brand-light">3D</span></span>
        </div>
        <div className="rounded-2xl border border-line bg-white p-7 shadow-[0_10px_30px_#2026330d]">
          <h1 className="font-display text-[1.1875rem] font-extrabold tracking-tight">{title}</h1>
          <p className="mb-6 mt-1 text-[0.9062rem] text-ink-400">{subtitle}</p>
          {children}
        </div>
        <p className="mt-5 text-center text-[0.8438rem] text-ink-400">{footer}</p>
      </div>
    </div>
  )
}

const errorMessage = (err: unknown) =>
  err instanceof ApiError ? err.message : 'Something went wrong. Please try again.'

/** After login, return to the page that required authentication (only same-app paths). */
function useReturnTo() {
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from
  return from && from.startsWith('/') && !from.startsWith('//') ? from : '/'
}

export function LoginPage() {
  const { registration_open: registrationOpen } = useClientConfig()
  const navigate = useNavigate()
  const returnTo = useReturnTo()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setPending(true)
    try {
      await login(email, password)
      navigate(returnTo, { replace: true })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to continue to your 3D studio."
      footer={
        registrationOpen ? (
          <>New to forma3D? <Link to="/register" className="font-semibold text-brand-500">Create an account</Link></>
        ) : (
          <>New accounts are created by your administrator.</>
        )
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Input label="Email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <Input label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="text-[0.8438rem] text-danger-600" role="alert">{error}</p>}
        <Button type="submit" disabled={pending} className="mt-1 h-10">{pending ? 'Signing in…' : 'Sign in'}</Button>
      </form>
    </AuthLayout>
  )
}

export function RegisterPage() {
  const { registration_open: registrationOpen, loaded } = useClientConfig()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setPending(true)
    try {
      await register(email, password, name.trim())
      navigate('/', { replace: true })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Start building 3D models through conversation."
      footer={<>Already have an account? <Link to="/login" className="font-semibold text-brand-500">Sign in</Link></>}
    >
      {loaded && !registrationOpen ? (
        <p className="rounded-lg bg-surface-2 p-3 text-[0.9062rem] text-ink-600" role="status">
          Sign-ups are closed on this server. Ask your administrator for an account.
        </p>
      ) : (
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Input label="Name" autoComplete="name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required />
        <Input label="Email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <Input label="Password" type="password" autoComplete="new-password" value={password} minLength={8} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="text-[0.8438rem] text-danger-600" role="alert">{error}</p>}
        <Button type="submit" disabled={pending} className="mt-1 h-10">{pending ? 'Creating account…' : 'Create account'}</Button>
      </form>
      )}
    </AuthLayout>
  )
}
