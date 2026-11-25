import { useEffect, useState } from 'react'
import { fetchAsset } from '../services/scene'

/** Asset routes need the bearer token, so images are fetched and shown via object URLs. */
export function useAssetObjectUrl(url: string | null | undefined, type = 'image/png') {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    let created: string | null = null
    fetchAsset(url, controller.signal)
      .then((buf) => {
        created = URL.createObjectURL(new Blob([buf], { type }))
        setObjectUrl(created)
      })
      .catch(() => !controller.signal.aborted && setFailed(true))
    return () => {
      controller.abort()
      if (created) URL.revokeObjectURL(created)
    }
  }, [url, type])
  return { objectUrl, failed }
}
