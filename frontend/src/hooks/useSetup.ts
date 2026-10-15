import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../api'


/** One first-run download (backend: app/setup/download.py). */
export interface SetupItem {
  id: 'answer-model' | 'judgments' | 'case-model' | string
  title: string
  unlocks: string
  required: boolean
  size: number
  installed: boolean
  available: boolean  // false until hosting is decided (no URL in the manifest)
  progress: {
    state: 'idle' | 'downloading' | 'verifying' | 'done' | 'failed' | 'cancelled'
    done: number
    total: number
    file: string
    error: string
  }
}

export interface SetupStatus {
  items: SetupItem[]
  case_builder_locked: boolean
  answers: boolean
  judgments: boolean
}

const BUSY = new Set(['downloading', 'verifying'])

/** What is installed, and the downloads: polled every second while one runs, else every 15 s. */
export function useSetup(enabled: boolean) {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [error, setError] = useState('')
  const [nudge, setNudge] = useState(0)  // bumped to read the status now (after a click)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let timer: number | undefined
    async function check() {
      try {
        const r = await fetch(`${API_BASE}/api/v1/setup`, { signal: AbortSignal.timeout(4000) })
        if (!r.ok) throw new Error(`setup ${r.status}`)
        const s: SetupStatus = await r.json()
        if (cancelled) return
        setStatus(s)
        const busy = s.items.some(i => BUSY.has(i.progress.state))
        timer = window.setTimeout(check, busy ? 1000 : 15000)
      } catch {
        if (!cancelled) timer = window.setTimeout(check, 5000)
      }
    }
    check()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [enabled, nudge])

  const refresh = useCallback(() => setNudge(n => n + 1), [])

  const post = useCallback(async (path: string, body?: object) => {
    setError('')
    const r = await fetch(`${API_BASE}/api/v1/setup/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!r.ok) {
      const data = await r.json().catch(() => ({}))
      setError(data.detail || `Request failed (${r.status})`)
    }
    refresh()
  }, [refresh])

  const download = useCallback((item: string) => post('download', { item }), [post])
  const cancel = useCallback(() => post('cancel'), [post])

  return { status, error, download, cancel, refresh }
}

export function gigabytes(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`
}
