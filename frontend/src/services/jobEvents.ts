import type { JobEvent } from '../types/scene'
import { apiFetch } from './api'

/** Terminal event types (mirror forma3d_shared.schemas.events.TERMINAL_EVENTS). */
export const TERMINAL_EVENT_TYPES = new Set(['job.completed', 'job.failed', 'job.cancelled'])

type Parsed = { id?: string; event?: string; data: string }

/** Incremental SSE parser (fields per the WHATWG spec; comments/pings ignored). */
export function createSseParser(onMessage: (msg: Parsed) => void) {
  let buffer = ''
  let current: Parsed = { data: '' }
  let hasData = false
  return (chunk: string) => {
    buffer += chunk
    const lines = buffer.split(/\r\n|\r|\n/)
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        if (hasData) onMessage(current)
        current = { data: '' }
        hasData = false
        continue
      }
      if (line.startsWith(':')) continue
      const idx = line.indexOf(':')
      const field = idx === -1 ? line : line.slice(0, idx)
      let value = idx === -1 ? '' : line.slice(idx + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'data') {
        current.data = hasData ? `${current.data}\n${value}` : value
        hasData = true
      } else if (field === 'id') current.id = value
      else if (field === 'event') current.event = value
    }
  }
}

type Options = {
  onEvent: (event: JobEvent) => void
  signal: AbortSignal
  /** Called when the stream drops before a terminal event (caller may poll / reconnect). */
  onDisconnect?: () => void
}

/**
 * Follow a job's event stream. Uses fetch (not EventSource) so the bearer token can be sent;
 * reconnects with Last-Event-ID until a terminal event arrives or the signal aborts.
 */
export async function followJobEvents(jobId: string, { onEvent, signal, onDisconnect }: Options): Promise<void> {
  let lastId: string | undefined
  let attempts = 0
  while (!signal.aborted) {
    let terminal = false
    try {
      const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}/events`, {
        signal,
        headers: { Accept: 'text/event-stream', ...(lastId ? { 'Last-Event-ID': lastId } : {}) },
      })
      if (!res.body) throw new Error('no stream body')
      attempts = 0
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      const feed = createSseParser((msg) => {
        if (terminal) return // nothing after a terminal event, even within the same chunk
        if (msg.id) lastId = msg.id
        try {
          const event = JSON.parse(msg.data) as JobEvent
          onEvent(event)
          if (TERMINAL_EVENT_TYPES.has(event.type)) terminal = true
        } catch {
          /* ignore malformed frame */
        }
      })
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        feed(decoder.decode(value, { stream: true }))
        if (terminal) {
          await reader.cancel().catch(() => undefined)
          break
        }
      }
    } catch (err) {
      if (signal.aborted) return
      if (err instanceof Error && 'status' in err && (err as { status: number }).status === 404) return
    }
    if (terminal || signal.aborted) return
    onDisconnect?.()
    attempts += 1
    if (attempts > 8) return
    await new Promise((r) => setTimeout(r, Math.min(500 * 2 ** attempts, 8000)))
  }
}
