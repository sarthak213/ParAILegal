import { useState } from 'react'
import { Copy, ChevronDown, ChevronUp, Check } from 'lucide-react'
import type { SourceChunk } from '../../types'
import { getSourceMeta, formatLegalCitation } from '../../utils/citations'
import { copyCitation } from '../../utils/export'

interface Props {
  source: SourceChunk
  index: number
  highlighted: boolean
  onClick: () => void
}

export function SourceCard({ source, highlighted, onClick }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const { label, colorClass } = getSourceMeta(source.source_type)
  const citation = formatLegalCitation(source)
  const score = source.score?.toFixed(4) ?? source.rrf_score?.toFixed(5) ?? '—'

  async function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    const ok = await copyCitation(source)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  function handleExpand(e: React.MouseEvent) {
    e.stopPropagation()
    setExpanded(v => !v)
  }

  return (
    <div
      className={`source-card${highlighted ? ' source-card--highlighted' : ''}`}
      onClick={onClick}
    >
      <span className={`source-tag ${colorClass}`}>{label}</span>

      <div className="source-card__citation">
        {citation.split('\n')[0]}
      </div>

      {source.text && (
        <div className="source-card__preview">
          {source.text.slice(0, 240)}
          {source.text.length > 240 ? '…' : ''}
        </div>
      )}

      <div className="source-card__footer">
        <span className="source-card__score">score {score}</span>
        <div className="source-card__actions">
          <button
            className="source-card__action-btn"
            onClick={handleCopy}
            title="Copy citation"
          >
            {copied ? <Check size={11} /> : <Copy size={11} />}
            {copied ? 'Copied' : 'Cite'}
          </button>
          {source.text && (
            <button
              className="source-card__action-btn"
              onClick={handleExpand}
              title={expanded ? 'Collapse' : 'Expand'}
            >
              {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
              {expanded ? 'Less' : 'More'}
            </button>
          )}
        </div>
      </div>

      {source.text && (
        <div className={`source-card__full-text${expanded ? ' source-card__full-text--open' : ''}`}>
          {source.text}
        </div>
      )}
    </div>
  )
}
