import { useState, useCallback } from 'react'

import { Sidebar } from './components/layout/Sidebar'
import { QueryInput } from './components/query/QueryInput'
import { AnswerView } from './components/answer/AnswerView'
import { SourcesPanel } from './components/sources/SourcesPanel'

import { useHistory } from './hooks/useHistory'
import { useStream } from './hooks/useStream'
import { useBackend } from './hooks/useBackend'

import type { QueryMode, Domain, HistoryEntry } from './types'

const EXAMPLE_QUERIES = [
  'What are the fundamental rights guaranteed under Part III of the Constitution?',
  'What is the punishment for murder under the BNS 2023?',
  'What did the Supreme Court hold in Maneka Gandhi v Union of India?',
  'When can the President impose Article 356 President\'s Rule?',
]

export default function App() {
  // ── Query state ───────────────────────────────────────────────────
  const [query, setQuery]   = useState('')
  const [mode, setMode]     = useState<QueryMode>('default')
  const [domain, setDomain] = useState<Domain>('')

  // ── UI state ──────────────────────────────────────────────────────
  const [activeHistoryId, setActiveHistoryId]   = useState<string | null>(null)
  const [sourcesCollapsed, setSourcesCollapsed] = useState(false)
  const [highlightedSource, setHighlightedSource] = useState<number | null>(null)

  // Currently displayed content (either live or from history)
  const [displayQuery,   setDisplayQuery]   = useState('')
  const [displayAnswer,  setDisplayAnswer]  = useState('')
  const [displaySources, setDisplaySources] = useState<HistoryEntry['sources']>([])
  const [displayDomain,  setDisplayDomain]  = useState('')
  const [displayMode,    setDisplayMode]    = useState<QueryMode>('default')

  // ── Hooks ─────────────────────────────────────────────────────────
  const { entries, addEntry, removeEntry, clearAll } = useHistory()
  const { appState, answer, sources, domain: streamDomain,
          error, stream, cancel, reset } = useStream()
  const backendStatus = useBackend()

  const isStreaming = appState === 'streaming'
  const isIdle      = appState === 'idle' && !displayQuery

  // ── Sync live stream to display ───────────────────────────────────
  // While streaming, show live data; when done, keep it
  const liveMode = !activeHistoryId
  const shownQuery   = liveMode ? (displayQuery || '')  : displayQuery
  const shownAnswer  = liveMode ? answer                : displayAnswer
  const shownSources = liveMode ? sources               : displaySources
  const shownDomain  = liveMode ? streamDomain          : displayDomain
  const shownMode    = liveMode ? mode                  : displayMode
  const shownState   = liveMode ? appState              : 'done'
  const shownError   = liveMode ? error                 : ''

  // ── Submit ────────────────────────────────────────────────────────
  const handleSubmit = useCallback(() => {
    if (!query.trim() || isStreaming) return

    // Reset stream state first — clears previous sources from the hook
    // This is critical when submitting from a history view, where
    // liveMode becomes true but stream.sources still holds old data
    reset()

    // Switch to live mode and immediately clear all display state
    setActiveHistoryId(null)
    setDisplayQuery(query)
    setDisplayAnswer('')
    setDisplaySources([])
    setDisplayDomain('')
    setHighlightedSource(null)

    stream(query, mode, domain, (finalAnswer, finalSources, finalDomain) => {
      addEntry(query, query, finalAnswer, finalSources, finalDomain, mode)
      setQuery('')
    })
  }, [query, mode, domain, isStreaming, stream, reset, addEntry])

  // ── Load history entry ────────────────────────────────────────────
  const handleSelectHistory = useCallback((entry: HistoryEntry) => {
    cancel()
    reset()
    setActiveHistoryId(entry.id)
    setDisplayQuery(entry.query)
    setDisplayAnswer(entry.answer)
    setDisplaySources(entry.sources)
    setDisplayDomain(entry.domain)
    setDisplayMode(entry.mode)
    setHighlightedSource(null)
  }, [cancel, reset])

  // ── New research ──────────────────────────────────────────────────
  const handleNew = useCallback(() => {
    cancel()
    reset()
    setActiveHistoryId(null)
    setDisplayQuery('')
    setDisplayAnswer('')
    setDisplaySources([])
    setQuery('')
    setHighlightedSource(null)
  }, [cancel, reset])

  // ── Example query ─────────────────────────────────────────────────
  const handleExample = useCallback((q: string) => {
    setQuery(q)
    setActiveHistoryId(null)
    reset()
    setDisplayQuery('')
  }, [reset])

  // ── Citation click → highlight source ────────────────────────────
  const handleCitationClick = useCallback((citation: string) => {
    const sources = liveMode ? shownSources : displaySources
    const idx = sources.findIndex(s =>
      (s.citation ?? '').toLowerCase().includes(citation.toLowerCase()) ||
      (s.hierarchy ?? '').toLowerCase().includes(citation.toLowerCase()) ||
      (s.section ?? '').toLowerCase().includes(citation.toLowerCase())
    )
    if (idx >= 0) {
      setHighlightedSource(idx)
      setSourcesCollapsed(false)
    }
  }, [liveMode, shownSources, displaySources])

  return (
    <div className="app">
      {/* ── Sidebar ── */}
      <Sidebar
        entries={entries}
        activeId={activeHistoryId}
        status={backendStatus}
        onSelect={handleSelectHistory}
        onDelete={removeEntry}
        onClear={clearAll}
        onNew={handleNew}
      />

      {/* ── Main ── */}
      <main className="main">
        {/* Content area */}
        <div className="content-area">
          {/* Answer panel */}
          <div className="answer-panel">
            {isIdle && !shownQuery ? (
              /* Welcome state */
              <div className="welcome">
                <div className="welcome__emblem">⚖</div>
                <h1 className="welcome__title">
                  Research Indian Law,<br />Grounded in Primary Sources
                </h1>
                <p className="welcome__sub">
                  Ask questions about the Constitution, BNS, BNSS, BSA,
                  and landmark Supreme Court judgements. Every answer cites its source.
                </p>
                <div className="welcome__examples">
                  {EXAMPLE_QUERIES.map(q => (
                    <button
                      key={q}
                      className="example-btn"
                      onClick={() => handleExample(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              /* Answer view */
              <AnswerView
                query={shownQuery}
                answer={shownAnswer}
                sources={shownSources}
                domain={shownDomain}
                mode={shownMode}
                appState={shownState}
                error={shownError}
                onCitationClick={handleCitationClick}
              />
            )}
          </div>

          {/* Show sources button — top right corner when panel is collapsed */}
          {sourcesCollapsed && shownSources.length > 0 && (
            <button
              className="show-sources-btn"
              onClick={() => setSourcesCollapsed(false)}
            >
              Show sources ({shownSources.length})
            </button>
          )}

          {/* Sources panel */}
          <SourcesPanel
            sources={shownSources}
            collapsed={sourcesCollapsed}
            onToggle={() => setSourcesCollapsed(v => !v)}
            highlightedIndex={highlightedSource}
            onSourceClick={setHighlightedSource}
            isLoading={isStreaming}
          />
        </div>

        {/* Query input — always at bottom */}
        <QueryInput
          value={query}
          onChange={setQuery}
          mode={mode}
          onModeChange={setMode}
          domain={domain}
          onDomainChange={setDomain}
          onSubmit={handleSubmit}
          onStop={cancel}
          isStreaming={isStreaming}
          disabled={backendStatus !== 'ready'}
        />
      </main>
    </div>
  )
}