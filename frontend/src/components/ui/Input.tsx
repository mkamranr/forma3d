import { forwardRef, useId, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { cn } from '../../utils/cn'

const FIELD =
  'w-full rounded-lg border border-line bg-white px-3 text-[0.9062rem] text-ink-900 placeholder:text-ink-300 ' +
  'outline-none transition-shadow focus:border-brand-400 focus:ring-3 focus:ring-brand-100'

type FieldProps = { label?: string; error?: string }

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & FieldProps>(
  function Input({ label, error, className, id, ...props }, ref) {
    const autoId = useId()
    const inputId = id ?? autoId
    return (
      <div className="flex flex-col gap-1.5">
        {label && <label htmlFor={inputId} className="text-[0.8438rem] font-semibold text-ink-600">{label}</label>}
        <input ref={ref} id={inputId} aria-invalid={!!error} className={cn(FIELD, 'h-10', className)} {...props} />
        {error && <span className="text-[0.8125rem] text-danger-600">{error}</span>}
      </div>
    )
  },
)

export function Textarea({ label, className, id, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label htmlFor={inputId} className="text-[0.8438rem] font-semibold text-ink-600">{label}</label>}
      <textarea id={inputId} className={cn(FIELD, 'min-h-20 resize-y py-2.5', className)} {...props} />
    </div>
  )
}
