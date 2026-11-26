import { ArrowLeft, CheckCircle2, Cuboid, Loader2, PlugZap, RotateCcw, Search, ShieldCheck, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { UserMenu } from '../../components/layout/UserMenu'
import { Button } from '../../components/ui/Button'
import { useAuthStore } from '../../stores/authStore'
import type { ConfigField, ConfigGroup, LlmTestResult, SettingValue } from '../../types/config'
import { relativeTime } from '../../utils/format'
import { errorMessage } from '../modeling/hooks'
import { useAppConfig, useSaveAppConfig, useTestLlm, useUpdateUser, useUsers } from './hooks'

/** Pending edits: key -> new value, or null = reset to the .env / built-in default. */
type Draft = Record<string, SettingValue | null>

const SOURCE: Record<ConfigField['source'], { label: string; cls: string; title: string }> = {
  app: { label: 'Set here', cls: 'bg-brand-50 text-brand-700', title: 'Saved in the Configuration page' },
  env: { label: '.env', cls: 'bg-[#eef6ee] text-[#2f7a3b]', title: 'From the server environment (.env)' },
  default: { label: 'Default', cls: 'bg-surface-2 text-ink-400', title: 'Built-in default' },
}

const CONTROL =
  'h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] text-ink-900 outline-none ' +
  'focus:border-brand-400 focus:ring-3 focus:ring-brand-100 disabled:opacity-60'

function show(v: SettingValue | null | undefined) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'On' : 'Off'
  return String(v)
}

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 cursor-pointer rounded-full border-0 transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${checked ? 'bg-brand-500' : 'bg-[#d5d8e0]'}`}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

function FieldControl({ field, value, onChange }: { field: ConfigField; value: SettingValue | null; onChange: (v: SettingValue) => void }) {
  const id = `cfg-${field.key}`
  switch (field.type) {
    case 'bool':
      return <Toggle checked={Boolean(value)} onChange={onChange} label={field.label} />
    case 'enum':
      return (
        <select id={id} className={CONTROL} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )
    case 'int':
    case 'float':
      return (
        <input
          id={id}
          type="number"
          className={CONTROL}
          value={value === null || value === undefined ? '' : String(value)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          step={field.type === 'int' ? 1 : 'any'}
          onChange={(e) => {
            const n = field.type === 'int' ? parseInt(e.target.value, 10) : parseFloat(e.target.value)
            onChange(Number.isNaN(n) ? e.target.value : n)
          }}
        />
      )
    case 'secret':
      return (
        <input
          id={id}
          type="password"
          autoComplete="new-password"
          className={CONTROL}
          value={typeof value === 'string' ? value : ''}
          placeholder={field.is_set ? '•••••••• (set — type to replace)' : 'Not set'}
          onChange={(e) => onChange(e.target.value)}
        />
      )
    default:
      return (
        <input
          id={id}
          type={field.type === 'url' ? 'url' : 'text'}
          className={CONTROL}
          value={String(value ?? '')}
          placeholder={field.type === 'url' ? 'http://host:8000/v1' : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

function FieldRow({ field, draft, setDraft }: { field: ConfigField; draft: Draft; setDraft: (fn: (d: Draft) => Draft) => void }) {
  const edited = field.key in draft
  const pending = draft[field.key]
  const value = edited ? (pending === null ? (field.default ?? null) : pending) : field.value
  const resetPending = edited && pending === null
  const source = resetPending ? { ...SOURCE.default, label: '.env / default' } : SOURCE[field.source]
  const range =
    field.min !== null || field.max !== null ? `${field.min ?? '…'} – ${field.max ?? '…'}` : ''

  const set = (v: SettingValue) => setDraft((d) => ({ ...d, [field.key]: v }))
  const reset = () =>
    setDraft((d) => {
      const next = { ...d }
      if (field.source === 'app') next[field.key] = null
      else delete next[field.key]
      return next
    })
  const canReset = (field.source === 'app' && !resetPending) || (edited && !resetPending)

  return (
    <div className={`grid gap-3 border-b border-[#eff0f4] py-4 last:border-0 md:grid-cols-[minmax(0,1fr)_minmax(0,320px)] ${edited ? 'bg-[#fbfaff]' : ''}`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`cfg-${field.key}`} className="text-[13px] font-semibold text-ink-900">{field.label}</label>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${source.cls}`} title={source.title}>{source.label}</span>
          {edited && <span className="rounded bg-[#fff4e0] px-1.5 py-0.5 text-[10px] font-semibold text-[#9a6200]">{resetPending ? 'Will reset' : 'Unsaved'}</span>}
        </div>
        {field.help && <p className="mt-1 text-[12px] leading-relaxed text-ink-400">{field.help}</p>}
        <p className="mt-1 text-[11px] text-ink-300">
          {field.env && <code className="mr-2">{field.env}</code>}
          {field.type === 'secret'
            ? `Default: ${field.default_is_set ? 'set in .env' : 'not set'}`
            : `Default: ${show(field.default)}`}
          {range && ` · Range ${range}`}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1"><FieldControl field={field} value={value ?? null} onChange={set} /></div>
        {field.type === 'secret' && field.is_set && field.source === 'app' && (
          <button
            type="button"
            className="cursor-pointer border-0 bg-transparent text-[11px] font-semibold text-danger-600 hover:underline"
            onClick={() => set('')}
            title="Remove the saved key"
          >
            Clear
          </button>
        )}
        <button
          type="button"
          className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-md border-0 bg-transparent text-ink-400 hover:bg-surface-2 hover:text-brand-700 disabled:cursor-default disabled:opacity-30"
          onClick={reset}
          disabled={!canReset}
          title={field.source === 'app' ? 'Reset to the .env / default value' : 'Discard this change'}
          aria-label={`Reset ${field.label}`}
        >
          <RotateCcw size={14} />
        </button>
      </div>
    </div>
  )
}

function LlmTestPanel({ draft, group }: { draft: Draft; group: ConfigGroup }) {
  const test = useTestLlm()
  const keys = new Set(group.fields.map((f) => f.key))
  const values = Object.fromEntries(Object.entries(draft).filter(([k]) => keys.has(k)))
  const result: LlmTestResult | undefined = test.data
  return (
    <div className="mb-5 rounded-xl border border-line bg-white p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-ink-900">Test connection</div>
          <p className="text-[12px] text-ink-400">
            Runs on an assistant worker with the values below, including unsaved changes: model list, a chat reply,
            structured output and a test photo.
          </p>
        </div>
        <Button variant="secondary" onClick={() => test.mutate(values)} disabled={test.isPending}>
          {test.isPending ? <Loader2 size={14} className="animate-spin" /> : <PlugZap size={14} />}
          {test.isPending ? 'Testing…' : 'Test connection'}
        </Button>
      </div>
      {test.isError && <p className="mt-3 text-[12px] text-danger-600" role="alert">{errorMessage(test.error)}</p>}
      {result && (
        <div className="mt-3" aria-live="polite">
          <div className={`mb-2 text-[12px] font-semibold ${result.ok ? 'text-[#2f7a3b]' : 'text-danger-600'}`}>
            {result.ok ? 'Connection works' : 'Connection has problems'}
            {result.model && <span className="font-normal text-ink-400"> · {result.provider} · {result.model}{result.base_url ? ` @ ${result.base_url}` : ''}</span>}
          </div>
          {result.error && <p className="text-[12px] text-danger-600">{result.error}</p>}
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {result.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2 text-[12px]">
                {c.ok ? <CheckCircle2 size={14} className="mt-px shrink-0 text-[#2f9a43]" /> : <XCircle size={14} className="mt-px shrink-0 text-danger-500" />}
                <span className="font-semibold text-ink-700">{c.label}</span>
                <span className="min-w-0 flex-1 text-ink-500">{c.detail}</span>
                <span className="shrink-0 tabular-nums text-ink-300">{c.ms} ms</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function UsersPanel() {
  const me = useAuthStore((s) => s.user)
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])
  const users = useUsers(term)
  const update = useUpdateUser()

  return (
    <section className="mt-6 rounded-xl border border-line bg-white">
      <div className="flex flex-wrap items-center gap-3 border-b border-[#eff0f4] px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-[13px] font-semibold text-ink-900">Users</h3>
          <p className="m-0 text-[12px] text-ink-400">
            Deactivated users are signed out immediately. Administrators can open this page.
          </p>
        </div>
        <div className="relative w-64 max-w-full">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
          <input className={`${CONTROL} pl-8`} placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
        </div>
      </div>
      {update.isError && <p className="m-0 px-4 pt-3 text-[12px] text-danger-600" role="alert">{errorMessage(update.error)}</p>}
      {users.isError ? (
        <p className="p-4 text-[12px] text-danger-600">{errorMessage(users.error)}</p>
      ) : (
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide text-ink-300">
              <th className="px-4 py-2 font-semibold">User</th>
              <th className="px-2 py-2 font-semibold">Projects</th>
              <th className="px-2 py-2 font-semibold">Joined</th>
              <th className="px-2 py-2 font-semibold">Active</th>
              <th className="px-4 py-2 font-semibold">Admin</th>
            </tr>
          </thead>
          <tbody>
            {(users.data?.items ?? []).map((u) => {
              const self = u.id === me?.id
              const busy = update.isPending && update.variables?.id === u.id
              return (
                <tr key={u.id} className={`border-t border-[#eff0f4] ${u.is_active ? '' : 'text-ink-300'}`}>
                  <td className="px-4 py-2.5">
                    <div className="font-semibold text-ink-900">{u.display_name}{self && <span className="font-normal text-ink-300"> (you)</span>}</div>
                    <div className="text-ink-400">{u.email}</div>
                  </td>
                  <td className="px-2 py-2.5 tabular-nums">{u.projects}</td>
                  <td className="px-2 py-2.5">{relativeTime(u.created_at)}</td>
                  <td className="px-2 py-2.5">
                    <Toggle checked={u.is_active} disabled={self || busy} label={`${u.email} active`} onChange={(v) => update.mutate({ id: u.id, is_active: v })} />
                  </td>
                  <td className="px-4 py-2.5">
                    <Toggle checked={u.is_admin} disabled={self || busy || !u.is_active} label={`${u.email} admin`} onChange={(v) => update.mutate({ id: u.id, is_admin: v })} />
                  </td>
                </tr>
              )
            })}
            {users.data && users.data.items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-ink-400">No users match.</td></tr>
            )}
          </tbody>
        </table>
      )}
      {users.data && users.data.total > users.data.items.length && (
        <p className="m-0 border-t border-[#eff0f4] px-4 py-2 text-[11px] text-ink-400">
          Showing {users.data.items.length} of {users.data.total}. Search to narrow the list.
        </p>
      )}
    </section>
  )
}

/** Admin-only: settings that override the server's .env, plus user management. */
export function ConfigurationPage() {
  const config = useAppConfig()
  const save = useSaveAppConfig()
  const [tab, setTab] = useState('ai')
  const [draft, setDraft] = useState<Draft>({})
  const [saved, setSaved] = useState(false)
  const groups = config.data?.groups ?? []
  const group = groups.find((g) => g.id === tab) ?? groups[0]
  const changes = Object.keys(draft).length
  const perTab = useMemo(() => {
    const out: Record<string, number> = {}
    for (const g of groups) out[g.id] = g.fields.filter((f) => f.key in draft).length
    return out
  }, [groups, draft])

  useEffect(() => {
    if (!changes) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [changes])

  const submit = () =>
    save.mutate(draft, {
      onSuccess: () => {
        setDraft({})
        setSaved(true)
        setTimeout(() => setSaved(false), 2500)
      },
    })

  return (
    <div className="min-h-screen bg-surface">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Cuboid size={20} strokeWidth={2.4} /></div>
          <span>forma<span className="brand-light">3D</span></span>
          <span className="brand-divider"></span>
        </div>
        <div className="top-actions"><UserMenu /></div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8 pb-28">
        <Link to="/" className="mb-3 inline-flex items-center gap-1.5 text-[12px] font-semibold text-ink-400 no-underline hover:text-brand-700">
          <ArrowLeft size={14} /> Projects
        </Link>
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-brand-50 text-brand-500"><ShieldCheck size={20} /></div>
          <div>
            <h1 className="m-0 text-[20px] font-bold text-ink-900">Configuration</h1>
            <p className="m-0 text-[12px] text-ink-400">
              Changes apply to all users within a few seconds, without a restart. Anything not set here comes from the server's <code>.env</code>.
            </p>
          </div>
        </div>

        {config.isLoading && <p className="text-[13px] text-ink-400">Loading settings…</p>}
        {config.isError && <p className="text-[13px] text-danger-600" role="alert">{errorMessage(config.error)}</p>}

        {group && (
          <>
            <nav role="tablist" className="mb-5 flex flex-wrap gap-1 border-b border-line">
              {groups.map((g) => (
                <button
                  key={g.id}
                  role="tab"
                  aria-selected={g.id === group.id}
                  onClick={() => setTab(g.id)}
                  className={`-mb-px cursor-pointer border-0 border-b-2 bg-transparent px-3 py-2 text-[13px] font-semibold ${
                    g.id === group.id ? 'border-brand-500 text-brand-700' : 'border-transparent text-ink-400 hover:text-ink-900'
                  }`}
                >
                  {g.label}
                  {perTab[g.id] > 0 && <span className="ml-1.5 rounded-full bg-[#fff4e0] px-1.5 text-[10px] text-[#9a6200]">{perTab[g.id]}</span>}
                </button>
              ))}
            </nav>

            {group.help && <p className="mb-4 text-[12px] text-ink-500">{group.help}</p>}
            {group.id === 'ai' && <LlmTestPanel draft={draft} group={group} />}

            <section role="tabpanel" aria-label={group.label} className="rounded-xl border border-line bg-white px-4">
              {group.fields.map((f) => <FieldRow key={f.key} field={f} draft={draft} setDraft={setDraft} />)}
            </section>

            {group.id === 'accounts' && <UsersPanel />}
          </>
        )}
      </main>

      {(changes > 0 || saved || save.isError) && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-white/95 shadow-[0_-4px_16px_#0000000d] backdrop-blur">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-3">
            <div className="min-w-0 flex-1 text-[12px]">
              {save.isError ? (
                <span className="text-danger-600" role="alert">{errorMessage(save.error)}</span>
              ) : changes > 0 ? (
                <span className="text-ink-600">{changes} unsaved change{changes > 1 ? 's' : ''}</span>
              ) : (
                <span className="text-[#2f7a3b]">Saved. Workers pick up the change before their next job.</span>
              )}
            </div>
            {changes > 0 && (
              <>
                <Button variant="ghost" onClick={() => { setDraft({}); save.reset() }} disabled={save.isPending}>Discard</Button>
                <Button onClick={submit} disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save changes'}</Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
