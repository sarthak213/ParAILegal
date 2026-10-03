import { useRef, useEffect } from 'react'
import { Send, Square } from 'lucide-react'
import type { QueryMode, Domain } from '../../types'

interface Props {
  value: string
  onChange: (v: string) => void
  mode: QueryMode
  onModeChange: (m: QueryMode) => void
  domain: Domain
  onDomainChange: (d: Domain) => void
  onSubmit: () => void
  onStop: () => void
  isStreaming: boolean
  disabled: boolean
}

const MODES: { value: QueryMode; label: string }[] = [
  { value: 'default',  label: 'Research' },
  { value: 'ADVOCATE', label: 'Advocate' },
  { value: 'SUMMARISE', label: 'Summarise' },
]

const DOMAINS: { value: Domain; label: string }[] = [
  { value: '',             label: 'All' },
  { value: 'constitution', label: 'Constitution' },
  { value: 'statutes',     label: 'Statutes' },
  { value: 'judgements',   label: 'Judgements' },
]

export function QueryInput({
  value, onChange, mode, onModeChange,
  domain, onDomainChange, onSubmit, onStop,
  isStreaming, disabled,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [value])

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isStreaming && value.trim()) onSubmit()
    }
  }

  return (
    <div className="query-area">
      <div className="query-area__top">
        <div className="mode-tabs">
          {MODES.map(m => (
            <button
              key={m.value}
              className={`mode-tab${mode === m.value ? ' mode-tab--active' : ''}`}
              onClick={() => onModeChange(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="domain-pills">
          {DOMAINS.map(d => (
            <button
              key={d.value}
              className={`domain-pill${domain === d.value ? ' domain-pill--active' : ''}`}
              onClick={() => onDomainChange(d.value)}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      <div className="query-area__input-row">
        <textarea
          ref={textareaRef}
          className="query-textarea"
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask a legal question…"
          rows={1}
          disabled={disabled}
        />
        {isStreaming ? (
          <button className="stop-btn" onClick={onStop}>
            <Square size={13} fill="currentColor" />
            Stop
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={onSubmit}
            disabled={!value.trim() || disabled}
          >
            <Send size={13} />
            Ask
          </button>
        )}
      </div>
      <div className="kbd-hint">Enter to send · Shift+Enter for new line</div>
    </div>
  )
}
