import { useState, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export type BackendStatus = 'checking' | 'ready' | 'starting' | 'failed' | 'offline'

export function useBackend() {
  const [status, setStatus] = useState<BackendStatus>('checking')

  useEffect(() => {
    let cancelled = false

    async function check() {
      try {
        const r = await fetch(`${API_BASE}/ready`, {
          signal: AbortSignal.timeout(4000),
        })
        if (cancelled) return
        const data = await r.json()
        if (data.status === 'ready') {
          setStatus('ready')
        } else if (data.status === 'failed') {
          // start-up failed: the reason is in data.error and the backend log; keep checking
          setStatus('failed')
          setTimeout(check, 5000)
        } else {
          setStatus('starting')
          setTimeout(check, 3000)
        }
      } catch {
        if (cancelled) return
        setStatus('offline')
        setTimeout(check, 5000)
      }
    }

    check()
    return () => { cancelled = true }
  }, [])

  return status
}
