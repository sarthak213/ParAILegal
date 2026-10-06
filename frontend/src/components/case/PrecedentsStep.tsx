import { useState } from 'react'
import { AlertTriangle, Scale } from 'lucide-react'
import type { Comparison, ComparisonPoint, Precedent } from '../../types/case'
import { MAX_PRECEDENTS } from './rules'


function Standing({ p }: { p: Precedent }) {
  const s = p.status
  const parts = [
    s.cited_by ? `cited in ${s.cited_by} later judgment${s.cited_by > 1 ? 's' : ''}` : 'not cited in later judgments in this collection',
    s.followed_by ? `followed in ${s.followed_by}` : '',
    s.distinguished_by ? `distinguished in ${s.distinguished_by}` : '',
    s.doubted_by ? `doubted in ${s.doubted_by}` : '',
  ].filter(Boolean)
  return (
    <>
      <div className="precedent__standing">{parts.join(' · ')}</div>
      {[...s.overruled_noted_in, ...s.per_incuriam_noted_in].map(o => (
        <div key={o.id} className="precedent__warning" role="note">
          <AlertTriangle size={13} />
          <span>
            Noted as {s.overruled_noted_in.includes(o) ? 'overruled' : 'per incuriam'} in <strong>{o.title}</strong> ({o.decided}).
            {o.evidence && <> “{o.evidence.slice(0, 220)}{o.evidence.length > 220 ? '…' : ''}”</>}
          </span>
        </div>
      ))}
    </>
  )
}

const CLAMP = 320  // characters of a holding shown before "show more"

/** What the Court held: two holdings, each cut to a few lines, until the reader asks for all. */
function Holdings({ holdings }: { holdings: Precedent['holdings'] }) {
  const [open, setOpen] = useState(false)
  const shown = open ? holdings : holdings.slice(0, 2)
  const long = holdings.length > 2 || holdings.some(h => h.text.length > CLAMP)
  return (
    <>
      <ul className="precedent__held">
        {shown.map((h, i) => (
          <li key={i}>
            {open || h.text.length <= CLAMP ? h.text : `${h.text.slice(0, CLAMP).replace(/\s+\S*$/, '')} …`}
            {h.paras && <span className="mono muted"> [{h.paras}]</span>}
          </li>
        ))}
      </ul>
      {long && (
        <button className="link-btn" onClick={() => setOpen(o => !o)}>
          {open ? 'Show less' : `Show all ${holdings.length > 2 ? `${holdings.length} holdings` : 'of it'}`}
        </button>
      )}
    </>
  )
}

function Points({ title, points }: { title: string; points: ComparisonPoint[] }) {
  if (!points.length) return null
  return (
    <div className="compare__group">
      <div className="compare__title">{title}</div>
      {points.map((p, i) => (
        <div key={i} className="compare__point">
          <div>{p.point}</div>
          {p.our_fact && <div className="compare__quote"><span>Our facts:</span> <q>{p.our_fact}</q></div>}
          {p.their_fact && <div className="compare__quote"><span>Judgment:</span> <q>{p.their_fact}</q></div>}
        </div>
      ))}
    </div>
  )
}

export function PrecedentsStep({ precedents, kept, comparisons, comparing, onCompare, onKeep }: {
  precedents: Precedent[]
  kept: string[]
  comparisons: Record<string, Comparison>
  comparing: string[]
  onCompare: (id: string) => void
  onKeep: (id: string) => void
}) {
  return (
    <div className="case-step">
      <p className="case-intro">
        Supreme Court judgments on the same provisions (old codes included) and the same kind of facts.
        A judgment whose facts differ can still be cited for its principle: compare the facts to see how far it
        carries, and choose up to {MAX_PRECEDENTS} for the brief.
      </p>
      {precedents.length === 0 && <div className="case-empty">No judgments matched. The brief can be written without precedents.</div>}
      {precedents.map(p => {
        const c = comparisons[p.id]
        const on = kept.includes(p.id)
        const full = !on && kept.length >= MAX_PRECEDENTS
        return (
          <article key={p.id} className={`precedent${on ? ' precedent--on' : ''}`}>
            <header className="precedent__head">
              <Scale size={15} className="precedent__icon" />
              <div>
                <div className="precedent__title">{p.title}</div>
                <div className="precedent__meta">
                  <span className="mono">{p.citation || p.id}</span> · decided {p.decided}
                  {p.bench_size && <> · {p.bench_size}</>}
                  {p.matched_refs.length > 0 && <> · on {p.matched_refs.join(', ')}</>}
                </div>
                <Standing p={p} />
              </div>
            </header>

            {p.holdings.length > 0 && (
              <div className="precedent__section">
                <div className="precedent__label">Held</div>
                <Holdings holdings={p.holdings} />
              </div>
            )}
            {p.passages.length > 0 && (
              <div className="precedent__section">
                <div className="precedent__label">From the judgment</div>
                {p.passages.map((x, i) => (
                  <blockquote key={i} className="precedent__passage">
                    <span className="mono muted">{x.page ? `p. ${x.n}` : `para ${x.n}`}</span> {x.text}
                  </blockquote>
                ))}
              </div>
            )}
            {p.law_at_time.length > 0 && (
              <div className="precedent__section">
                <div className="precedent__label">The law at the time</div>
                <ul className="precedent__law">{p.law_at_time.map(l => <li key={l.ref}>{l.note}</li>)}</ul>
              </div>
            )}

            {c && (
              <div className="compare">
                <Points title="Alike" points={c.similar} />
                <Points title="Different" points={c.different} />
                {c.bearing && <div className="compare__bearing"><strong>How far it carries:</strong> {c.bearing}</div>}
                <div className="compare__note">
                  Written by the local model from the judgment's headnote; every quote was checked against both texts
                  {c.dropped > 0 && <> ({c.dropped} point{c.dropped > 1 ? 's' : ''} left out because {c.dropped > 1 ? 'their' : 'its'} quotes were not found)</>}.
                  Read the judgment before relying on it.
                </div>
              </div>
            )}

            <footer className="precedent__actions">
              <button className="btn btn--ghost" disabled={comparing.includes(p.id)} onClick={() => onCompare(p.id)}>
                {comparing.includes(p.id) ? 'Comparing…' : c ? 'Compare again' : 'Compare the facts'}
              </button>
              <label className={`keep${full ? ' keep--full' : ''}`}>
                <input type="checkbox" checked={on} disabled={full} onChange={() => onKeep(p.id)} />
                Use in the brief
              </label>
            </footer>
          </article>
        )
      })}
    </div>
  )
}
