import { Check } from 'lucide-react'

export function Toast({ message }: { message: string }) {
  if (!message) return null
  return (
    <div className="toast" role="status">
      <Check size={15} />
      {message}
    </div>
  )
}
