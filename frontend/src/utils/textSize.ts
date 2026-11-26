import { useSyncExternalStore } from 'react'

/** Interface text size, remembered per device. All UI font sizes are in rem, so the root size scales them. */
export type TextSize = 'small' | 'default' | 'large' | 'xlarge'

export const TEXT_SIZES: [TextSize, string][] = [
  ['small', 'S'],
  ['default', 'M'],
  ['large', 'L'],
  ['xlarge', 'XL'],
]

const ROOT: Record<TextSize, string> = { small: '93.75%', default: '100%', large: '112.5%', xlarge: '125%' }
const KEY = 'forma3d.textSize'
const listeners = new Set<() => void>()

function read(): TextSize {
  try {
    const v = localStorage.getItem(KEY) as TextSize | null
    return v && v in ROOT ? v : 'default'
  } catch {
    return 'default'
  }
}

let current: TextSize = read()

export function applyTextSize(size: TextSize = current) {
  document.documentElement.style.fontSize = ROOT[size]
}

export function setTextSize(size: TextSize) {
  current = size
  try {
    localStorage.setItem(KEY, size)
  } catch {
    /* private mode: still applies for this session */
  }
  applyTextSize(size)
  listeners.forEach((l) => l())
}

export function useTextSize(): TextSize {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => current,
  )
}
