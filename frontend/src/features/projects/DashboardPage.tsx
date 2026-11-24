import { Cuboid, Plus, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UserMenu } from '../../components/layout/UserMenu'
import { Button } from '../../components/ui/Button'
import type { Project, ProjectSort, ProjectStatus } from '../../types/api'
import { ConfirmDeleteDialog, ProjectFormDialog } from './ProjectDialogs'
import { ProjectCard } from './ProjectCard'
import { useCreateProject, useDeleteProject, useDuplicateProject, useProjects, useUpdateProject } from './hooks'

const SORTS: { value: ProjectSort; label: string }[] = [
  { value: 'updated_desc', label: 'Last modified' },
  { value: 'created_desc', label: 'Recently created' },
  { value: 'name_asc', label: 'Name A–Z' },
  { value: 'name_desc', label: 'Name Z–A' },
]

function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<ProjectSort>('updated_desc')
  const [status, setStatus] = useState<ProjectStatus>('active')
  const q = useDebounced(search.trim())

  const projects = useProjects({ q: q || undefined, sort, status, limit: 100 })
  const create = useCreateProject()
  const update = useUpdateProject()
  const remove = useDeleteProject()
  const duplicate = useDuplicateProject()

  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState<Project | null>(null)

  const items = projects.data?.items ?? []
  const isEmptyAccount = !projects.isLoading && items.length === 0 && !q && status === 'active'

  return (
    <div className="min-h-screen bg-surface font-sans text-ink-900">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Cuboid size={20} strokeWidth={2.4} /></div>
          <span>forma<span className="brand-light">3D</span></span>
          <span className="brand-divider"></span>
          <span className="workspace-name">Projects</span>
        </div>
        <div className="top-actions"><UserMenu /></div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-[22px] font-extrabold tracking-tight">Your projects</h1>
            <p className="mt-1 text-[13px] text-ink-400">Create a model by describing it, then refine it through conversation.</p>
          </div>
          <Button onClick={() => setCreating(true)}><Plus size={15} /> New Project</Button>
        </div>

        <div className="mb-5 flex flex-wrap items-center gap-3">
          <div className="flex rounded-lg border border-line bg-white p-0.5" role="tablist" aria-label="Project status">
            {(['active', 'archived'] as const).map((s) => (
              <button
                key={s}
                role="tab"
                aria-selected={status === s}
                onClick={() => setStatus(s)}
                className={`cursor-pointer rounded-md border-0 px-3 py-1.5 text-[12px] font-semibold capitalize ${
                  status === s ? 'bg-brand-50 text-brand-700' : 'bg-transparent text-ink-500 hover:text-ink-900'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <div className="relative min-w-56 flex-1">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search projects"
              aria-label="Search projects"
              className="h-9 w-full rounded-lg border border-line bg-white pl-9 pr-3 text-[13px] outline-none focus:border-brand-400 focus:ring-3 focus:ring-brand-100"
            />
          </div>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as ProjectSort)}
            aria-label="Sort projects"
            className="h-9 cursor-pointer rounded-lg border border-line bg-white px-3 text-[13px] text-ink-600 outline-none focus:border-brand-400"
          >
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>

        {projects.isError ? (
          <div className="rounded-xl border border-line bg-white p-8 text-center text-[13px] text-danger-600" role="alert">
            Could not load projects: {projects.error.message}
          </div>
        ) : projects.isLoading ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-5" aria-busy="true">
            {Array.from({ length: 3 }, (_, i) => <div key={i} className="aspect-[16/13] animate-pulse rounded-xl bg-[#eceef3]" />)}
          </div>
        ) : isEmptyAccount ? (
          <div className="flex flex-col items-center rounded-2xl border border-dashed border-[#d9dce4] bg-white px-6 py-16 text-center">
            <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-brand-50 text-brand-500"><Cuboid size={28} /></div>
            <h2 className="font-display text-[17px] font-bold">Start creating your first 3D model.</h2>
            <p className="mt-1.5 max-w-sm text-[13px] text-ink-400">Describe an object, like a desk, a chair or a cabinet, and the AI will build it in Blender.</p>
            <Button className="mt-5" onClick={() => setCreating(true)}><Plus size={15} /> New Project</Button>
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border border-line bg-white p-10 text-center text-[13px] text-ink-400">
            {q ? `No projects match “${q}”.` : 'No archived projects.'}
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-5">
            {items.map((p) => (
              <ProjectCard
                key={p.id}
                project={p}
                onRename={setRenaming}
                onDuplicate={(proj) => duplicate.mutate(proj.id)}
                onToggleArchive={(proj) => update.mutate({ id: proj.id, status: proj.status === 'archived' ? 'active' : 'archived' })}
                onDelete={setDeleting}
              />
            ))}
          </div>
        )}
      </main>

      <ProjectFormDialog
        open={creating}
        mode="create"
        pending={create.isPending}
        error={create.error?.message}
        onClose={() => { setCreating(false); create.reset() }}
        onSubmit={(data) =>
          create.mutate(data, {
            onSuccess: (project) => {
              setCreating(false)
              navigate(`/projects/${project.id}`)
            },
          })
        }
      />
      <ProjectFormDialog
        open={!!renaming}
        mode="rename"
        project={renaming}
        pending={update.isPending}
        error={update.error?.message}
        onClose={() => { setRenaming(null); update.reset() }}
        onSubmit={(data) => renaming && update.mutate({ id: renaming.id, ...data }, { onSuccess: () => setRenaming(null) })}
      />
      <ConfirmDeleteDialog
        project={deleting}
        pending={remove.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
      />
    </div>
  )
}
