import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'

type Props = {
  open: boolean
  title: string
  description?: string
  onClose: () => void
  children: ReactNode
}

/** Minimal accessible modal dialog (Escape / backdrop to close, initial focus inside). */
export function Dialog({ open, title, description, onClose, children }: Props) {
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    const first = panel.current?.querySelector<HTMLElement>('input, textarea, button[data-autofocus], button')
    first?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#151a2366] p-4" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className="w-full max-w-md rounded-xl border border-line bg-white p-5 shadow-[0_20px_60px_#151a2333]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="dialog-title" className="font-display text-[15px] font-bold text-ink-900">{title}</h2>
            {description && <p className="mt-1 text-[12px] text-ink-400">{description}</p>}
          </div>
          <button className="cursor-pointer rounded-md border-0 bg-transparent p-1 text-ink-400 hover:bg-surface-2" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
