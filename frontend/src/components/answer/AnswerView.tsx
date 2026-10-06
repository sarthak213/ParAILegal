import { memo, useMemo, useRef, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, FileText, Share2, Check } from 'lucide-react'
import type { SourceChunk, QueryMode, AppState, Verification } from '../../types'
import type { StreamStage } from '../../hooks/useStream'
import {
  formatLegalCitation, isSourceNumbers, linkCitations, matchSource, parseCitation, shortLabel,
} from '../../utils/citations'
import { copyToClipboard, exportToPDF } from '../../utils/export'

interface Props {
  query: string
  answer: string
  sources: SourceChunk[]
  domain: string
  mode: QueryMode
  appState: AppState
  stage: StreamStage
  error: string
  verification?: Verification | null
  followUp?: string  // the earlier question this one was read as following up
  onCitationClick: (sourceIndex: number) => void
}

/**
 * One citation chip. A citation that matches a retrieved source links to it; one that matches
 * nothing is flagged, because the model cited something it was not given. "[2]" is the second
 * source sent with the answer (v2); a written citation ("[Section 103, BNS]", v1) is matched by
 * its fields.
 */
function CitationChip({ label, sources, onClick }: {
  label: string
  sources: SourceChunk[]
  onClick: (sourceIndex: number) => void
}) {
  const numbered = isSourceNumbers(label)
  const index = numbered ? Number(label) - 1 : matchSource(parseCitation(label), sources)
  const source = index >= 0 ? sources[index] : undefined
  return (
    <button
      type="button"
      className={`citation-chip${source ? '' : ' citation-chip--unverified'}`}
      title={source
        ? `${formatLegalCitation(source)}: show this source`
        : 'Not among the retrieved sources: verify this citation independently'}
      onClick={() => source && onClick(index)}
    >
      {numbered && source ? shortLabel(source) : label}
    </button>
  )
}

/** The verifier's warning under an answer: provisions or figures it could not find in the sources. */
function VerificationNote({ verification }: { verification: Verification }) {
  if (!verification.warning) return null
  return (
    <div className="verify-note" role="note">
      <strong>Check this answer.</strong> {verification.warning}
    </div>
  )
}

const AnswerMarkdown = memo(function AnswerMarkdown({ markdown, sources, onCitationClick }: {
  markdown: string
  sources: SourceChunk[]
  onCitationClick: (sourceIndex: number) => void
}) {
  const components = useMemo<Components>(() => ({
    a({ href, children, ...props }) {
      if (href === '#cite') {
        const label = String(Array.isArray(children) ? children.join('') : children ?? '')
        // "[Section 103, BNS; Section 101, BNS]" and "[1, 2]" become one chip per citation
        const parts = label.split(isSourceNumbers(label) ? /[;,]/ : ';').map(p => p.trim()).filter(Boolean)
        return (
          <span className="citation-group">
            {parts.map((part, i) => (
              <CitationChip key={i} label={part} sources={sources} onClick={onCitationClick} />
            ))}
          </span>
        )
      }
      return <a href={href} target="_blank" rel="noreferrer noopener" {...props}>{children}</a>
    },
    // The query is the page's h1; demote any heading the model emits so the hierarchy stays sane.
    h1: ({ children }) => <h3>{children}</h3>,
    h2: ({ children }) => <h3>{children}</h3>,
  }), [sources, onCitationClick])

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {linkCitations(markdown)}
    </ReactMarkdown>
  )
})

const STAGE_LABEL: Record<StreamStage, string> = {
  searching: 'Searching the legal corpus…',
  reading: 'Reading the retrieved provisions…',
  loading: 'Loading the answer model (first question only)…',
  thinking: 'Reasoning over the provisions…',
  writing: 'Writing the answer…',
}

export function AnswerView({
  query,
  answer,
  sources,
  domain,
  mode,
  appState,
  stage,
  error,
  verification,
  followUp,
  onCitationClick,
}: Props) {
  const [copied, setCopied] = useState(false)
  const [shared, setShared] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const isStreaming = appState === 'streaming'
  const isDone      = appState === 'done'
  const isSearching = isStreaming && !answer

  async function handleCopyAnswer() {
    const ok = await copyToClipboard(answer)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  function handleExportPDF() {
    // The rendered answer (headings, lists, citations) rather than raw markdown
    exportToPDF(query, bodyRef.current?.innerHTML ?? '', sources, domain)
  }

  async function handleShare() {
    const text = `Query: ${query}\n\nAnswer: ${answer.slice(0, 500)}…\n\nResearched with ParAILegal`
    await copyToClipboard(text)
    setShared(true)
    setTimeout(() => setShared(false), 2000)
  }

  return (
    <div className="answer-view">

      {/* Query header */}
      <div className="answer-view__header">
        {followUp && (
          <div className="follow-up-note" title="Searched together with the earlier question. Use New Research to start fresh.">
            Follow-up to: <span>{followUp}</span>
          </div>
        )}
        <h1 className="answer-view__query">{query}</h1>
        <div className="answer-view__meta">
          {domain && <span className="domain-badge">{domain.toUpperCase()}</span>}
          {mode !== 'default' && (
            <span className="mode-badge">{mode}</span>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="error-box">{error}</div>
      )}

      {/* Searching skeleton — shown while waiting for first token */}
      {isSearching && (
        <div className="answer-searching">
          <div className="answer-searching__label">
            <span className="answer-searching__dot" />
            {STAGE_LABEL[stage]}
          </div>
          <div className="answer-searching__skeletons">
            {[100, 88, 94, 72, 90, 60].map((w, i) => (
              <div
                key={i}
                className="skeleton-line"
                style={{
                  width: `${w}%`,
                  height: i === 0 ? '1.1rem' : '0.88rem',
                  marginBottom: i === 2 ? '16px' : '0',
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* Answer body: markdown, rendered as it streams */}
      {answer && (
        <div
          ref={bodyRef}
          className={`answer-view__body answer-md${isStreaming ? ' answer-md--streaming' : ''}`}
        >
          <AnswerMarkdown markdown={answer} sources={sources} onCitationClick={onCitationClick} />
        </div>
      )}

      {isDone && verification && <VerificationNote verification={verification} />}

      {/* Disclaimer */}
      {(isDone || (isStreaming && answer)) && (
        <div className="answer-view__disclaimer">
          ⚖ This is a research tool. Verify all provisions against the official Gazette. This is not legal advice.
        </div>
      )}

      {/* Action buttons — only when done */}
      {isDone && answer && (
        <div className="answer-view__actions">
          <button
            className={`action-btn${copied ? ' action-btn--copied' : ''}`}
            onClick={handleCopyAnswer}
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? 'Copied' : 'Copy Answer'}
          </button>
          <button className="action-btn" onClick={handleExportPDF}>
            <FileText size={13} />
            Export PDF
          </button>
          <button
            className={`action-btn${shared ? ' action-btn--copied' : ''}`}
            onClick={handleShare}
          >
            {shared ? <Check size={13} /> : <Share2 size={13} />}
            {shared ? 'Copied to clipboard' : 'Share'}
          </button>
        </div>
      )}
    </div>
  )
}
