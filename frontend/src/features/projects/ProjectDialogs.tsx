import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { Input, Textarea } from '../../components/ui/Input'
import type { Project } from '../../types/api'

type ProjectFormProps = {
  open: boolean
  mode: 'create' | 'rename'
  project?: Project | null
  pending: boolean
  error?: string
  onClose: () => void
  onSubmit: (data: { name: string; description: string }) => void
}

export function ProjectFormDialog({ open, mode, project, pending, error, onClose, onSubmit }: ProjectFormProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (open) {
      setName(project?.name ?? '')
      setDescription(project?.description ?? '')
    }
  }, [open, project])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (name.trim()) onSubmit({ name: name.trim(), description: description.trim() })
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={mode === 'create' ? 'New project' : 'Edit project'}
      description={mode === 'create' ? 'Give your 3D model a name. You can describe it to the AI next.' : undefined}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Input label="Project name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Modern writing desk" required />
        <Textarea label="Description (optional)" value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} placeholder="Oak top, black steel legs…" />
        {error && <p className="text-[0.8438rem] text-danger-600" role="alert">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={pending || !name.trim()}>
            {pending ? 'Saving…' : mode === 'create' ? 'Create project' : 'Save'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

type ConfirmDeleteProps = {
  project: Project | null
  pending: boolean
  onClose: () => void
  onConfirm: () => void
}

export function ConfirmDeleteDialog({ project, pending, onClose, onConfirm }: ConfirmDeleteProps) {
  return (
    <Dialog
      open={!!project}
      onClose={onClose}
      title="Delete project?"
      description={project ? `“${project.name}” and its version history will be removed.` : undefined}
    >
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} data-autofocus>Cancel</Button>
        <Button variant="danger" onClick={onConfirm} disabled={pending}>{pending ? 'Deleting…' : 'Delete project'}</Button>
      </div>
    </Dialog>
  )
}
