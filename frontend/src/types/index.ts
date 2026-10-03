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

// ── SSE Event Types ───────────────────────────────────────────────────

export type SSEEvent =
  | { type: 'sources'; domain: string; sources: SourceChunk[] }
  | { type: 'token'; token: string }
  | { type: 'done'; answer: string }
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
