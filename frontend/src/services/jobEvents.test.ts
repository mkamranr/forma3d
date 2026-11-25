import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from '../stores/authStore'
import { sseResponse } from '../test/fixtures'
import { mockFetch } from '../test/utils'
import type { JobEvent } from '../types/scene'
import { createSseParser, followJobEvents } from './jobEvents'

describe('createSseParser', () => {
  it('parses frames across chunk boundaries and ignores comments', () => {
    const out: { id?: string; event?: string; data: string }[] = []
    const feed = createSseParser((m) => out.push(m))
    feed(': ping\n\nid: 1-0\nevent: job.started\nda')
    feed('ta: {"a":1}\n\nid: 2-0\ndata: line1\r\ndata: line2\n\n')
    expect(out).toEqual([
      { id: '1-0', event: 'job.started', data: '{"a":1}' },
      { id: '2-0', data: 'line1\nline2' },
    ])
  })
})

describe('followJobEvents', () => {
  beforeEach(() => useAuthStore.setState({ status: 'authenticated', accessToken: 'tok', user: null }))

  it('streams events with the bearer token and stops at the terminal event', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/jobs/j1/events': () =>
        sseResponse([
          { id: '1-0', type: 'job.started', data: { job_id: 'j1', stage: 'started', message: 'go' } },
          { id: '2-0', type: 'job.completed', data: { job_id: 'j1', stage: 'done', message: 'Done', data: { version_number: 2 } } },
          { id: '3-0', type: 'agent.progress', data: { job_id: 'j1', stage: 'x', message: 'never delivered' } },
        ]),
    })
    const events: JobEvent[] = []
    await followJobEvents('j1', { signal: new AbortController().signal, onEvent: (e) => events.push(e) })
    expect(events.map((e) => e.type)).toEqual(['job.started', 'job.completed'])
    expect(events[1].data).toEqual({ version_number: 2 })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('reconnects with Last-Event-ID after a dropped stream', async () => {
    let call = 0
    const fetchMock = mockFetch({
      'GET /api/v1/jobs/j1/events': () => {
        call += 1
        return call === 1
          ? sseResponse([{ id: '5-0', type: 'job.started', data: { job_id: 'j1', stage: 's', message: '' } }])
          : sseResponse([{ id: '6-0', type: 'job.failed', data: { job_id: 'j1', stage: 'failed', message: 'boom' } }])
      },
    })
    const types: string[] = []
    await followJobEvents('j1', { signal: new AbortController().signal, onEvent: (e) => types.push(e.type) })
    expect(types).toEqual(['job.started', 'job.failed'])
    const second = fetchMock.mock.calls[1][1] as RequestInit
    expect((second.headers as Record<string, string>)['Last-Event-ID']).toBe('5-0')
  })
})
