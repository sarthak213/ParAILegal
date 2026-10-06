// ── API Types ─────────────────────────────────────────────────────────

export interface SourceChunk {
  citation?: string
  source_type?: string
  section?: string
  label?: string
  hierarchy?: string
  chunk_id?: string
  text?: string
  document_title?: string
  chunk_type?: string
  status?: string
  score?: number
  rrf_score?: number
  rank?: number
}

export interface SearchResponse {
  query: string
  domain: string
  results: SourceChunk[]
}

export interface AnswerResponse {
  query: string
  domain: string
  answer: string
  sources: SourceChunk[]
}

/** The backend's check of a generated answer against its sources (app/answer/verify.py). */
export interface Verification {
  invalid_ids: number[]
  unsupported_provisions: string[]
  unsupported_figures: string[]
  misattributed: string[]
  uncited: string[]
  claims: number
  cited_sources: number[]
  warning: string
}

// ── SSE Event Types ───────────────────────────────────────────────────

export type SSEEvent =
  | { type: 'sources'; domain: string; sources: SourceChunk[] }
  | { type: 'gate'; outcome: 'answer' | 'caveat' | 'sources_only'; reason: string; unknown: string[]; mode: string }
  | { type: 'token'; token: string }
  | { type: 'status'; stage: 'loading' | 'thinking' }
  | { type: 'done'; answer: string; verification?: Verification }
  | { type: 'error'; detail: string }

// ── History Types ─────────────────────────────────────────────────────

export interface HistoryEntry {
  id: string
  query: string
  rawQuery: string
  answer: string
  sources: SourceChunk[]
  domain: string
  mode: QueryMode
  timestamp: number
}

// ── UI Types ──────────────────────────────────────────────────────────

export type QueryMode = 'default' | 'ADVOCATE' | 'SUMMARISE'
export type Domain = '' | 'constitution' | 'statutes' | 'judgements'
export type AppState = 'idle' | 'streaming' | 'done' | 'error'

export interface QueryState {
  query: string
  mode: QueryMode
  domain: Domain
}
