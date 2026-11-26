import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../utils/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-500 text-white hover:bg-brand-600 shadow-[0_3px_8px_#6658dc38] border-transparent',
  secondary: 'bg-white text-ink-600 border-line hover:bg-surface-2 hover:text-brand-700',
  ghost: 'bg-transparent text-ink-500 border-transparent hover:bg-surface-2 hover:text-brand-700',
  danger: 'bg-danger-500 text-white hover:bg-danger-600 border-transparent',
}

export function Button({
  variant = 'primary',
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border px-3.5 text-[0.9062rem] font-semibold transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500',
        'disabled:cursor-not-allowed disabled:opacity-55',
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  )
}
