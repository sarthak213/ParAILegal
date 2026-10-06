import type { SourceChunk, Verification } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

/** POST to a Case Builder endpoint; the backend's error detail becomes the thrown message. */
export async function postCase<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${API_BASE}/api/v1/case/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try {
      const data = await r.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export interface BriefHandlers {
  onSources: (sources: SourceChunk[]) => void
  onToken: (text: string) => void
  onDone: (answer: string, verification: Verification | null) => void
}

/** The brief, streamed as server-sent events: sources, then tokens, then done (as /answer/stream). */
export async function streamBrief(body: unknown, handlers: BriefHandlers, signal: AbortSignal): Promise<void> {
  const r = await fetch(`${API_BASE}/api/v1/case/brief`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try { detail = (await r.json()).detail ?? detail } catch { /* not JSON */ }
    throw new Error(detail)
  }
  const reader = r.body!.getReader()
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
      let event: Record<string, unknown>
      try { event = JSON.parse(line.slice(5).trim()) } catch { continue }
      if (event.type === 'sources') handlers.onSources((event.sources as SourceChunk[]) ?? [])
      else if (event.type === 'token') handlers.onToken((event.token as string) ?? '')
      else if (event.type === 'done') {
        handlers.onDone((event.answer as string) ?? '', (event.verification as Verification | undefined) ?? null)
        return
      } else if (event.type === 'error') throw new Error((event.detail as string) ?? 'The brief failed')
    }
  }
  throw new Error('The connection closed before the brief finished. Please try again.')
}
