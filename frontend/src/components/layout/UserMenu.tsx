import { LogOut, SlidersHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { logout } from '../../services/auth'
import { useAuthStore } from '../../stores/authStore'
import { initials } from '../../utils/format'
import { TEXT_SIZES, setTextSize, useTextSize } from '../../utils/textSize'

export function UserMenu() {
  const user = useAuthStore((s) => s.user)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const textSize = useTextSize()

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const signOut = async () => {
    await logout()
    queryClient.clear()
    navigate('/login', { replace: true })
  }

  return (
    <div ref={ref} className="relative">
      <button
        className="avatar"
        style={{ border: 0, cursor: 'pointer' }}
        onClick={() => setOpen((v) => !v)}
        title={user?.display_name ?? 'Account'}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {initials(user?.display_name ?? '')}
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-10 z-50 w-64 rounded-lg border border-[#e8eaf0] bg-white p-1.5 shadow-lg">
          <div className="px-2.5 py-2">
            <div className="truncate text-[0.8438rem] font-semibold text-[#202633]">{user?.display_name}</div>
            <div className="truncate text-[0.8125rem] text-[#8b92a0]">{user?.email}</div>
          </div>
          <div className="my-1 h-px bg-[#eff0f4]" />
          <div className="flex items-center justify-between gap-2 px-2.5 py-1.5">
            <span className="text-[0.8125rem] text-[#626b7c]">Text size</span>
            <div className="flex rounded-md bg-[#f0f1f6] p-0.5" role="radiogroup" aria-label="Text size">
              {TEXT_SIZES.map(([v, label]) => (
                <button
                  key={v}
                  role="radio"
                  aria-checked={textSize === v}
                  title={`Text size: ${v === 'xlarge' ? 'extra large' : v}`}
                  onClick={() => setTextSize(v)}
                  className={`h-6 min-w-7 cursor-pointer rounded border-0 px-1.5 text-[0.75rem] font-semibold ${
                    textSize === v ? 'bg-white text-[#4f46bd] shadow-sm' : 'bg-transparent text-[#8b92a0] hover:text-[#4f46bd]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="my-1 h-px bg-[#eff0f4]" />
          {user?.is_admin && (
            <button
              role="menuitem"
              onClick={() => navigate('/configuration')}
              className="flex w-full cursor-pointer items-center gap-2 rounded-md border-0 bg-transparent px-2.5 py-2 text-left text-[0.8438rem] text-[#626b7c] hover:bg-[#f0f1f6] hover:text-[#4f46bd]"
            >
              <SlidersHorizontal size={14} /> Configuration
            </button>
          )}
          <button
            role="menuitem"
            onClick={signOut}
            className="flex w-full cursor-pointer items-center gap-2 rounded-md border-0 bg-transparent px-2.5 py-2 text-left text-[0.8438rem] text-[#626b7c] hover:bg-[#f0f1f6] hover:text-[#4f46bd]"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  )
}
