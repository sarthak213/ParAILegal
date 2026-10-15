import { Check, Download, Lock, RotateCcw, X } from 'lucide-react'
import { gigabytes, type SetupItem, type SetupStatus } from '../../hooks/useSetup'
import './setup.css'

const BUSY = new Set(['downloading', 'verifying'])

/** One download: what it unlocks, its size, and its progress. */
export function SetupItemCard({ item, busyElsewhere, onDownload, onCancel }: {
  item: SetupItem
  busyElsewhere: boolean
  onDownload: (id: string) => void
  onCancel: () => void
}) {
  const p = item.progress
  const running = BUSY.has(p.state)
  const share = p.total ? Math.min(100, (100 * p.done) / p.total) : 0
  const started = p.done > 0 && !item.installed
  return (
    <div className={`setup-item${item.installed ? ' setup-item--done' : ''}`}>
      <div className="setup-item__head">
        <div>
          <div className="setup-item__title">
            {item.title}
            {item.required && !item.installed && <span className="setup-item__tag">Recommended</span>}
          </div>
          <div className="setup-item__unlocks">{item.unlocks}</div>
        </div>
        <div className="setup-item__size">{gigabytes(item.size)}</div>
      </div>

      {item.installed ? (
        <div className="setup-item__state setup-item__state--ok"><Check size={14} /> Installed</div>
      ) : running ? (
        <>
          <div className="setup-bar" role="progressbar" aria-valuenow={Math.round(share)} aria-valuemin={0} aria-valuemax={100}>
            <div className="setup-bar__fill" style={{ width: `${share}%` }} />
          </div>
          <div className="setup-item__state">
            {p.state === 'verifying' ? 'Checking the download…' : `${gigabytes(p.done)} of ${gigabytes(p.total)} · ${Math.floor(share)}%`}
            <button className="link-btn" onClick={onCancel}><X size={12} /> Pause</button>
          </div>
        </>
      ) : !item.available ? (
        <div className="setup-item__state setup-item__state--muted">Not available for download yet.</div>
      ) : (
        <div className="setup-item__state">
          {p.state === 'failed' && <span className="setup-item__error">{p.error}</span>}
          <button className="btn btn--primary" disabled={busyElsewhere} onClick={() => onDownload(item.id)}
                  title={busyElsewhere ? 'One download at a time' : undefined}>
            {p.state === 'failed' ? <RotateCcw size={13} /> : <Download size={13} />}
            {p.state === 'failed' ? 'Try again' : started ? `Resume (${Math.floor(share)}% done)` : 'Download'}
          </button>
        </div>
      )}
    </div>
  )
}

/** The Downloads view: the models and the judgments, downloaded once after installing. */
export function SetupView({ status, error, onDownload, onCancel }: {
  status: SetupStatus | null
  error: string
  onDownload: (id: string) => void
  onCancel: () => void
}) {
  if (!status) return <div className="setup"><p className="setup__sub">Connecting…</p></div>
  const busy = status.items.some(i => BUSY.has(i.progress.state))
  return (
    <div className="setup">
      <h1 className="setup__title">Downloads</h1>
      <p className="setup__sub">
        ParAILegal works offline. The law itself came with the app; these come once, after it.
        Downloads resume if they stop, and each file is checked before it is used.
      </p>
      {error && <div className="setup__error">{error}</div>}
      <div className="setup__list">
        {status.items.map(item => (
          <SetupItemCard key={item.id} item={item} busyElsewhere={busy}
                         onDownload={onDownload} onCancel={onCancel} />
        ))}
      </div>
    </div>
  )
}

/** In place of the Case Builder until its model is installed. */
export function CaseBuilderLocked({ item, busyElsewhere, onDownload, onCancel }: {
  item: SetupItem | undefined
  busyElsewhere: boolean
  onDownload: (id: string) => void
  onCancel: () => void
}) {
  return (
    <div className="setup setup--locked">
      <div className="setup__lock"><Lock size={22} /></div>
      <h1 className="setup__title">The Case Builder needs its own model</h1>
      <p className="setup__sub">
        It turns facts into offences, elements, Supreme Court precedents and a brief. That work needs a
        larger model than answers do: it compares facts more carefully. Download it once to unlock the
        Case Builder.
      </p>
      {item && <SetupItemCard item={item} busyElsewhere={busyElsewhere} onDownload={onDownload} onCancel={onCancel} />}
    </div>
  )
}
