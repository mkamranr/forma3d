/** "850 ms", "4.2 s", "38 s", "2 min 5 s". */
export function duration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return ''
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`
}
