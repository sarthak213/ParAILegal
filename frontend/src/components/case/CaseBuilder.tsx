import { useCallback, useRef, useState } from 'react'
import { AnswerView } from '../answer/AnswerView'
import { SourcesPanel } from '../sources/SourcesPanel'
import { postCase, streamBrief } from '../../hooks/useCaseApi'
import type { StreamStage } from '../../hooks/useStream'
import type { AppState, SourceChunk, Verification } from '../../types'
import type {
  Comparison, ElementsResult, ElementStatus, OffenceCandidate, Precedent, StructuredFacts,
} from '../../types/case'
import { ElementsStep, FactsStep, OffencesStep } from './CaseSteps'
import { PrecedentsStep } from './PrecedentsStep'
import { MAX_OFFENCES, MAX_PRECEDENTS, summarise } from './rules'
import './case.css'

/**
 * The Case Builder (backend: app/api/routes/case.py): a fixed sequence, each step's output shown and
 * editable before the next runs. Facts -> offences -> elements -> Supreme Court precedents -> brief.
 */
const STEPS = ['Facts', 'Offences', 'Elements', 'Precedents', 'Brief'] as const

export function CaseBuilder({ ready }: { ready: boolean }) {
  const [step, setStep] = useState(0)
  const [reached, setReached] = useState(0)        // the furthest step with results
  const [busy, setBusy] = useState('')             // what is running, shown on the button
  const [error, setError] = useState('')

  const [facts, setFacts] = useState('')
  const [structured, setStructured] = useState<StructuredFacts | null>(null)
  const [offences, setOffences] = useState<OffenceCandidate[]>([])
  const [chosen, setChosen] = useState<string[]>([])
  const [added, setAdded] = useState<string[]>([])     // sections the lawyer added by number
  const [elements, setElements] = useState<ElementsResult[]>([])
  const [checking, setChecking] = useState<string[]>([])
  const [precedents, setPrecedents] = useState<Precedent[]>([])
  const [kept, setKept] = useState<string[]>([])
  const [comparisons, setComparisons] = useState<Record<string, Comparison>>({})
  const [comparing, setComparing] = useState<string[]>([])

  const [brief, setBrief] = useState('')
  const [briefState, setBriefState] = useState<AppState>('idle')
  const [briefStage, setBriefStage] = useState<StreamStage>('reading')
  const [briefSources, setBriefSources] = useState<SourceChunk[]>([])
  const [verification, setVerification] = useState<Verification | null>(null)
  const [highlighted, setHighlighted] = useState<number | null>(null)
  const [sourcesCollapsed, setSourcesCollapsed] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const factsOk = facts.trim().length >= 20
  const acts = (structured?.acts ?? []).map(a => a.trim()).filter(Boolean)

  const run = useCallback(async (label: string, work: () => Promise<void>) => {
    setBusy(label)
    setError('')
    try {
      await work()
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }, [])

  const go = (n: number) => {
    setStep(n)
    setReached(r => Math.max(r, n))
  }

  // ── Step actions ──────────────────────────────────────────────────

  const structureFacts = () => run('Reading the facts…', async () => {
    const r = await postCase<{ facts: StructuredFacts }>('facts', { facts })
    setStructured(r.facts)
  })

  const findOffences = (include: string[] = added) => run('Finding offences…', async () => {
    const r = await postCase<{ offences: OffenceCandidate[] }>('offences', { facts, acts, include })
    setOffences(r.offences)
    // keep earlier choices that are still candidates
    setChosen(c => c.filter(ref => r.offences.some(o => o.ref === ref)))
    setReached(1)
    go(1)
  })

  // a section the lawyer names: searched for again with it included, and chosen
  const addOffence = (ref: string) => run('Adding…', async () => {
    const include = [...added.filter(x => x !== ref), ref]
    const r = await postCase<{ offences: OffenceCandidate[] }>('offences', { facts, acts, include })
    if (!r.offences.some(o => o.ref === ref)) throw new Error(`${ref} is not in ParAILegal's corpus.`)
    setAdded(include)
    setOffences(r.offences)
    setChosen(c => [...c.filter(x => x !== ref && r.offences.some(o => o.ref === x)), ref].slice(-MAX_OFFENCES))
  })

  const checkElements = () => run('Checking elements…', async () => {
    const refs = offences.filter(o => chosen.includes(o.ref) && o.has_checklist).map(o => o.ref)
    setElements([])
    setReached(2)
    go(2)
    const failed: string[] = []
    // one at a time: the local model answers one request at a time
    for (const ref of refs) {
      setChecking(c => [...c, ref])
      try {
        const r = await postCase<ElementsResult>('elements', { facts, ref })
        setElements(e => [...e, r])
      } catch (e) {
        if ((e as Error).name === 'AbortError') throw e
        failed.push(`${ref}: ${(e as Error).message}`)  // one offence failing does not stop the others
      } finally {
        setChecking(c => c.filter(x => x !== ref))
      }
    }
    if (failed.length) throw new Error(`Could not check ${failed.join('; ')}`)
  })

  const findPrecedents = () => run('Searching the judgments…', async () => {
    const r = await postCase<{ precedents: Precedent[] }>('precedents', { facts, refs: chosen, acts, k: 6 })
    setPrecedents(r.precedents)
    setKept(k => k.filter(id => r.precedents.some(p => p.id === id)))
    setReached(3)
    go(3)
  })

  const compare = async (id: string) => {
    setComparing(c => [...c, id])
    setError('')
    try {
      const r = await postCase<Comparison>('compare', { facts, id })
      setComparisons(c => ({ ...c, [id]: r }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setComparing(c => c.filter(x => x !== id))
    }
  }

  const writeBrief = async () => {
    go(4)
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setBrief('')
    setBriefSources([])
    setVerification(null)
    setBriefState('streaming')
    setBriefStage('reading')
    setError('')
    const checklists = Object.fromEntries(elements.filter(e => chosen.includes(e.ref)).map(e => [e.ref, e.elements]))
    let text = ''
    try {
      await streamBrief(
        { facts, refs: chosen, checklists, precedents: kept.map(id => ({ id, comparison: comparisons[id] ?? null })) },
        {
          onSources: s => setBriefSources(s),
          onToken: t => { text += t; setBriefStage('writing'); setBrief(text.replace(/\n\n⚖.*$/s, '')) },
          onDone: (answer, v) => {
            setBrief((answer || text).replace(/\n\n⚖.*$/s, '').replace(/⚖.*$/s, '').trimEnd())
            setVerification(v)
            setBriefState('done')
          },
        },
        abortRef.current.signal,
      )
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      setError((e as Error).message)
      setBriefState('error')
    }
  }

  const setStatus = (ref: string, n: number, status: ElementStatus) => setElements(rs => rs.map(r => {
    if (r.ref !== ref) return r
    const els = r.elements.map(e => (e.n === n ? { ...e, status } : e))
    return { ...r, elements: els, summary: summarise(els) }
  }))

  const startOver = () => {
    abortRef.current?.abort()
    setStep(0); setReached(0); setError(''); setFacts(''); setStructured(null); setOffences([]); setChosen([]); setAdded([])
    setElements([]); setPrecedents([]); setKept([]); setComparisons({}); setBrief(''); setBriefState('idle')
    setBriefSources([]); setVerification(null)
  }

  // ── The step's main action ────────────────────────────────────────

  const hasChecklists = offences.some(o => chosen.includes(o.ref) && o.has_checklist)
  const NEXT: Record<number, { label: string; disabled: boolean }> = {
    0: { label: 'Find offences', disabled: !factsOk },
    1: { label: hasChecklists ? 'Check the elements' : 'Find precedents', disabled: chosen.length === 0 },
    2: { label: 'Find precedents', disabled: checking.length > 0 },
    3: { label: 'Write the brief', disabled: chosen.length === 0 },
  }
  const next = NEXT[step]
  const runNext = () => {
    if (step === 0) findOffences(added)
    else if (step === 1) (hasChecklists ? checkElements : findPrecedents)()
    else if (step === 2) findPrecedents()
    else if (step === 3) writeBrief()
  }

  return (
    <div className="case-builder">
      <div className="case-main">
        <header className="case-header">
          <div>
            <h1 className="case-header__title">Case Builder</h1>
            <p className="case-header__sub">From the facts to offences, elements, Supreme Court precedents and a brief. Arguments to test, not conclusions.</p>
          </div>
          <button className="btn btn--ghost" onClick={startOver}>Start over</button>
        </header>

        <nav className="stepper" aria-label="Case Builder steps">
          {STEPS.map((s, i) => (
            <button
              key={s}
              className={`stepper__step${i === step ? ' stepper__step--on' : ''}${i <= reached ? ' stepper__step--done' : ''}`}
              disabled={i > reached}
              onClick={() => setStep(i)}
            >
              <span className="stepper__n">{i + 1}</span>{s}
            </button>
          ))}
        </nav>

        {!ready && <div className="error-box">The backend is not ready yet; the steps will work once it is.</div>}
        {error && <div className="error-box">{error}</div>}

        <div className="case-body">
          {step === 0 && <FactsStep facts={facts} onFacts={setFacts} structured={structured} onStructured={setStructured} />}
          {step === 1 && <OffencesStep offences={offences} chosen={chosen}
            onToggle={ref => setChosen(c => (c.includes(ref) ? c.filter(x => x !== ref) : c.length < MAX_OFFENCES ? [...c, ref] : c))}
            onAdd={addOffence} adding={busy === 'Adding…'} />}
          {step === 2 && <ElementsStep results={elements} loading={checking} onStatus={setStatus} />}
          {step === 3 && <PrecedentsStep precedents={precedents} kept={kept} comparisons={comparisons} comparing={comparing}
            onCompare={compare}
            onKeep={id => setKept(k => (k.includes(id) ? k.filter(x => x !== id) : k.length < MAX_PRECEDENTS ? [...k, id] : k))} />}
          {step === 4 && (
            <AnswerView
              query="Case brief"
              answer={brief}
              sources={briefSources}
              domain=""
              mode="default"
              appState={briefState}
              stage={briefStage}
              error=""
              verification={verification}
              onCitationClick={i => { setHighlighted(i); setSourcesCollapsed(false) }}
            />
          )}
        </div>

        <footer className="case-actions">
          {step === 0 && (
            <button className="btn btn--ghost" disabled={!factsOk || !!busy || !ready} onClick={structureFacts}
              title="The local model lists parties, events and what each accused did; you can edit them">
              {busy === 'Reading the facts…' ? busy : structured ? 'Read the facts again' : 'Read the facts (optional)'}
            </button>
          )}
          {step === 4 && briefState !== 'streaming' && (
            <button className="btn btn--ghost" onClick={writeBrief}>Write the brief again</button>
          )}
          {next && (
            <button className="btn btn--primary" disabled={next.disabled || !!busy || !ready} onClick={runNext}>
              {busy && busy !== 'Reading the facts…' ? busy : next.label}
            </button>
          )}
        </footer>
      </div>

      {step === 4 && (
        <SourcesPanel
          sources={briefSources}
          collapsed={sourcesCollapsed}
          onToggle={() => setSourcesCollapsed(v => !v)}
          highlightedIndex={highlighted}
          onSourceClick={setHighlighted}
          isLoading={briefState === 'streaming'}
        />
      )}
    </div>
  )
}
