import { describe, expect, it } from 'vitest'
import { initials, relativeTime } from './format'

describe('relativeTime', () => {
  const now = new Date('2026-11-24T12:00:00Z')
  it.each([
    ['2026-11-24T11:59:50Z', 'just now'],
    ['2026-11-24T11:55:00Z', '5 minutes ago'],
    ['2026-11-24T09:00:00Z', '3 hours ago'],
    ['2026-11-23T12:00:00Z', 'yesterday'],
    ['2026-11-10T12:00:00Z', '2 weeks ago'],
  ])('%s -> %s', (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected)
  })
})

describe('initials', () => {
  it.each([
    ['Muhammad Rafi', 'MR'],
    ['alice', 'AL'],
    ['  Jean  Luc Picard ', 'JP'],
    ['', '?'],
  ])('%s -> %s', (name, expected) => {
    expect(initials(name)).toBe(expected)
  })
})
