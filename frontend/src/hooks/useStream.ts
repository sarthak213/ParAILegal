import { useState, useRef, useCallback } from 'react'
import type { SourceChunk, AppState, QueryMode } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

interface StreamState {
  appState: AppState
  answer: string
  sources: SourceChunk[]
  domain: string
  error: string
}

export function useStream() {
  const [state, setState] = useState<StreamState>({
    appState: 'idle',
    answer: '',
    sources: [],
    domain: '',
    error: '',
  })

  const abortRef = useRef<AbortController | null>(null)

  const stream = useCallback(async (
    rawQuery: string,
    mode: QueryMode,
    domain: string,
    onDone: (answer: string, sources: SourceChunk[], domain: string) => void,
  ) => {
    // Cancel any in-flight request
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    // Build query with mode prefix
    let query = rawQuery
    if (mode === 'ADVOCATE') query = `ADVOCATE: ${rawQuery}`
    if (mode === 'SUMMARISE') query = `SUMMARISE: ${rawQuery}`

    setState({ appState: 'streaming', answer: '', sources: [], domain: '', error: '' })

    let assembledTokens = ''
    let finalSources: SourceChunk[] = []
    let finalDomain = ''

    try {
      const response = await fetch(`${API_BASE}/api/v1/answer/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, domain: domain || null }),
        signal: abortRef.current.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const reader = response.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw || raw === '[DONE]') continue

          let event: Record<string, unknown>
          try { event = JSON.parse(raw) } catch { continue }

          if (event.type === 'sources') {
            finalSources = (event.sources as SourceChunk[]) ?? []
            finalDomain = (event.domain as string) ?? ''
            setState(s => ({ ...s, sources: finalSources, domain: finalDomain }))
          } else if (event.type === 'token') {
            assembledTokens += (event.token as string) ?? ''
            // Strip disclaimer for streaming display
            const display = assembledTokens.replace(/\n\n⚖.*$/s, '')
            setState(s => ({ ...s, answer: display }))
          } else if (event.type === 'done') {
            const full = (event.answer as string) ?? assembledTokens
            const display = full.replace(/\n\n⚖.*$/s, '').replace(/⚖.*$/s, '').trimEnd()
            setState(s => ({ ...s, appState: 'done', answer: display }))
            onDone(display, finalSources, finalDomain)
            return
          } else if (event.type === 'error') {
            throw new Error((event.detail as string) ?? 'Unknown error')
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      const msg = (err as Error).message ?? 'Connection failed'
      setState(s => ({ ...s, appState: 'error', error: msg }))
    }
  }, [])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    setState(s => ({ ...s, appState: 'idle' }))
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState({ appState: 'idle', answer: '', sources: [], domain: '', error: '' })
  }, [])

  return { ...state, stream, cancel, reset }
}
