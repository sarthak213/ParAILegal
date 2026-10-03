import { MessageSquare, Plus, Trash2, Clock } from 'lucide-react'
import type { HistoryEntry } from '../../types'
import { formatRelativeTime } from '../../utils/citations'
import type { BackendStatus } from '../../hooks/useBackend'

interface Props {
  entries: HistoryEntry[]
  activeId: string | null
  status: BackendStatus
  onSelect: (entry: HistoryEntry) => void
  onDelete: (id: string) => void
  onClear: () => void
  onNew: () => void
}

const STATUS_LABELS: Record<BackendStatus, string> = {
  checking: 'Connecting…',
  ready:    'Ready',
  starting: 'Starting…',
  offline:  'Offline',
}

export function Sidebar({
  entries, activeId, status,
  onSelect, onDelete, onClear, onNew,
}: Props) {
  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar__header">
        <img src="/logo.png" alt="ParAILegal" style={{ width: '100%', maxWidth: '270px', marginBottom: '0px' , objectFit: 'contain'   }} />
        <button className="sidebar__new-btn" onClick={onNew}>
          <Plus size={14} />
          New Research
        </button>
      </div>

      {/* Status */}
      <div className="sidebar__status">
        <span className={`status-dot status-dot--${status}`} />
        <span className="status-label">{STATUS_LABELS[status]}</span>
      </div>

      {/* History header */}
      <div className="sidebar__history-header">
        <span className="sidebar__history-title">History</span>
        {entries.length > 0 && (
          <button className="sidebar__clear-btn" onClick={onClear} title="Clear all history">
            Clear all
          </button>
        )}
      </div>

      {/* History list */}
      <div className="sidebar__history">
        {entries.length === 0 ? (
          <div className="sidebar__empty">
            <Clock size={20} style={{ color: 'var(--sidebar-muted)', margin: '0 auto 8px' }} />
            Your research history will appear here.
          </div>
        ) : (
          entries.map(entry => (
            <div
              key={entry.id}
              className={`history-item${activeId === entry.id ? ' history-item--active' : ''}`}
              onClick={() => onSelect(entry)}
            >
              <MessageSquare size={12} className="history-item__icon" />
              <div className="history-item__content">
                <div className="history-item__query">{entry.query}</div>
                <div className="history-item__meta">
                  <span>{formatRelativeTime(entry.timestamp)}</span>
                  {entry.domain && (
                    <span className="history-item__domain">{entry.domain}</span>
                  )}
                </div>
              </div>
              <button
                className="history-item__delete"
                onClick={e => { e.stopPropagation(); onDelete(entry.id) }}
                title="Remove from history"
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
