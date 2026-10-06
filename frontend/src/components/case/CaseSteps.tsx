import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import type { ElementsResult, ElementStatus, OffenceCandidate, StructuredFacts } from '../../types/case'
import { MAX_OFFENCES, normaliseRef } from './rules'


// ── Facts ─────────────────────────────────────────────────────────────

export function FactsStep({ facts, onFacts, structured, onStructured }: {
  facts: string
  onFacts: (v: string) => void
  structured: StructuredFacts | null
  onStructured: (s: StructuredFacts) => void
}) {
  const setActs = (acts: string[]) => structured && onStructured({ ...structured, acts })
  return (
    <div className="case-step">
      <label className="case-label" htmlFor="case-facts">Facts of the case</label>
      <textarea
        id="case-facts"
        className="case-textarea"
        rows={9}
        value={facts}
        onChange={e => onFacts(e.target.value)}
        placeholder="Who did what, to whom, when and where; what was taken, said or written; injuries; documents. Plain language is fine."
      />
      <p className="case-hint">{facts.trim().length < 20 ? 'At least 20 characters.' : `${facts.trim().length} characters`}</p>

      {structured && (
        <div className="case-structured">
          <div className="case-structured__grid">
            <FactList title="Parties" items={structured.parties.map(p => `${p.name}: ${p.role}`)} />
            <FactList title="Events" items={structured.events.map(e => (e.when ? `${e.when}: ${e.what}` : e.what))} />
            <FactList title="Harm" items={structured.harm} />
            <FactList title="Property" items={structured.property} />
            <FactList title="Documents" items={structured.documents} />
          </div>
          <div className="case-acts">
            <div className="case-label">What the accused did <span className="case-label__note">(the offence search reads these; edit them)</span></div>
            {structured.acts.map((act, i) => (
              <div key={i} className="case-acts__row">
                <input
                  className="case-input"
                  value={act}
                  onChange={e => setActs(structured.acts.map((a, j) => (j === i ? e.target.value : a)))}
                />
                <button className="icon-btn" title="Remove" onClick={() => setActs(structured.acts.filter((_, j) => j !== i))}>
                  <X size={13} />
                </button>
              </div>
            ))}
            <button className="link-btn" onClick={() => setActs([...structured.acts, ''])}>
              <Plus size={12} /> Add an act
            </button>
            {(structured.acts_dropped ?? []).length > 0 && (
              <div className="case-dropped">
                <div className="case-dropped__title">Left out: the facts do not say this</div>
                {structured.acts_dropped!.map((act, i) => (
                  <div key={i} className="case-dropped__row">
                    <span>{act}</span>
                    <button className="link-btn" onClick={() => onStructured({
                      ...structured,
                      acts: [...structured.acts, act],
                      acts_dropped: structured.acts_dropped!.filter((_, j) => j !== i),
                    })}>Put back</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function FactList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div className="fact-list">
      <div className="fact-list__title">{title}</div>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  )
}

// ── Offences ──────────────────────────────────────────────────────────

export function OffencesStep({ offences, chosen, onToggle, onAdd, adding }: {
  offences: OffenceCandidate[]
  chosen: string[]
  onToggle: (ref: string) => void
  onAdd: (ref: string) => void   // a section the lawyer names ("BNS 85"), whatever search found
  adding: boolean
}) {
  const [ref, setRef] = useState('')
  const add = () => {
    const r = normaliseRef(ref)
    if (r) { onAdd(r); setRef('') }
  }
  return (
    <div className="case-step">
      <p className="case-intro">
        Candidate offences, best match first. Choose the ones to pursue (up to {MAX_OFFENCES}).
        Procedure is read from the BNSS First Schedule, not written by the model.
      </p>
      <div className="case-add">
        <input
          className="case-input"
          value={ref}
          onChange={e => setRef(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && add()}
          placeholder="Missing an offence? Add it by section, e.g. BNS 85"
          aria-label="Add an offence by section"
        />
        <button className="btn btn--ghost" disabled={!normaliseRef(ref) || adding} onClick={add}>
          {adding ? 'Adding…' : 'Add'}
        </button>
      </div>
      <div className="offence-list">
        {offences.map(o => {
          const on = chosen.includes(o.ref)
          const full = !on && chosen.length >= MAX_OFFENCES
          return (
            <label key={o.ref} className={`offence-card${on ? ' offence-card--on' : ''}${full ? ' offence-card--full' : ''}`}>
              <input type="checkbox" checked={on} disabled={full} onChange={() => onToggle(o.ref)} />
              <div className="offence-card__body">
                <div className="offence-card__head">
                  <span className="offence-card__ref">{o.ref}</span>
                  <span className="offence-card__name">{o.name}</span>
                  {o.added && <span className="tag tag--added">added by you</span>}
                  {!o.has_checklist && <span className="tag tag--muted" title="The brief will still cover it">no elements checklist yet</span>}
                </div>
                {o.classification.map((c, i) => (
                  <div key={i} className="offence-card__proc">
                    {c.sub && <span className="mono">{c.sub} </span>}
                    {c.punishment.replace(/\.$/, '')} · {c.cognizable} · {c.bailable}{c.court ? ` · ${c.court}` : ''}
                  </div>
                ))}
              </div>
            </label>
          )
        })}
      </div>
    </div>
  )
}

// ── Elements ──────────────────────────────────────────────────────────

const STATUS_LABEL: Record<ElementStatus, string> = { shown: 'Shown', not_shown: 'Not shown', unclear: 'Open' }

export function ElementsStep({ results, loading, onStatus }: {
  results: ElementsResult[]
  loading: string[]   // refs still being checked
  onStatus: (ref: string, n: number, status: ElementStatus) => void
}) {
  return (
    <div className="case-step">
      <p className="case-intro">
        Each element of the offence, with the statute's words and the fact that shows it. The model marked
        them; a mark is only accepted with words that are really in the facts. Change any you disagree with.
      </p>
      {loading.map(ref => <div key={ref} className="case-loading">Checking the elements of {ref}…</div>)}
      {results.map(r => (
        <section key={r.ref} className="elements">
          <h3 className="elements__title">
            {r.name} <span className="mono">({r.ref})</span>
            <span className={`summary summary--${r.summary.replace(' ', '-')}`}>{r.summary}</span>
          </h3>
          <table className="elements__table">
            <tbody>
              {r.elements.map(e => (
                <tr key={e.n} className={e.kind === 'any_of' ? 'elements__alt' : ''}>
                  <td className="elements__label">
                    {e.kind === 'any_of' && <span className="tag tag--muted">any one of</span>} {e.label}
                    <blockquote className="elements__law">“{e.law.quote}” <span className="mono">{e.law.source}</span></blockquote>
                  </td>
                  <td className="elements__fact">
                    {e.fact ? <q>{e.fact}</q> : <span className="muted">{e.quote_checked ? 'Not in the facts' : 'The quoted words were not in the facts'}</span>}
                  </td>
                  <td>
                    <select
                      className={`status-select status-select--${e.status}`}
                      value={e.status}
                      onChange={ev => onStatus(r.ref, e.n, ev.target.value as ElementStatus)}
                    >
                      {(Object.keys(STATUS_LABEL) as ElementStatus[]).map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  )
}
