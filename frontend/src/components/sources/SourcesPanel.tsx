import { ChevronRight } from 'lucide-react'
import type { SourceChunk } from '../../types'
import { SourceCard } from './SourceCard'

interface Props {
  sources: SourceChunk[]
  collapsed: boolean
  onToggle: () => void
  highlightedIndex: number | null
  onSourceClick: (index: number) => void
  isLoading: boolean
}

export function SourcesPanel({
  sources,
  collapsed,
  onToggle,
  highlightedIndex,
  onSourceClick,
  isLoading,
}: Props) {
  return (
    <aside className={`sources-panel${collapsed ? ' sources-panel--collapsed' : ''}`}>
      <div className="sources-panel__header">
        <span className="sources-panel__title">Retrieved Sources</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="sources-count">{sources.length}</span>
          <button
            className="sources-toggle-btn"
            onClick={onToggle}
            title="Collapse sources"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div className="sources-panel__list">
        {isLoading && sources.length === 0 ? (
          // Skeleton cards while waiting for sources
          [0, 1, 2].map(i => (
            <div key={i} className="source-skeleton">
              <div className="skeleton-line" style={{ width: '35%', height: '0.6rem' }} />
              <div className="skeleton-line" style={{ width: '90%' }} />
              <div className="skeleton-line" style={{ width: '70%', height: '0.75rem' }} />
              <div className="skeleton-line" style={{ width: '85%', height: '0.75rem' }} />
            </div>
          ))
        ) : sources.length === 0 ? (
          <div className="sources-panel__empty">
            <span style={{ fontSize: '1.8rem', color: 'var(--border-dark)' }}>§</span>
            <p>Sources retrieved from the legal corpus will appear here, ranked by relevance.</p>
          </div>
        ) : (
          sources.map((source, i) => (
            <SourceCard
              key={source.chunk_id ?? i}
              source={source}
              index={i}
              highlighted={highlightedIndex === i}
              onClick={() => onSourceClick(i)}
            />
          ))
        )}
      </div>
    </aside>
  )
}
