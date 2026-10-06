// ── Case Builder (backend: app/api/routes/case.py) ───────────────────────

export interface StructuredFacts {
  parties: { name: string; role: string }[]
  events: { when: string; what: string }[]
  harm: string[]
  property: string[]
  documents: string[]
  acts: string[]          // one sentence per thing an accused did: the offence search reads these
  acts_dropped?: string[] // acts the model gave that the facts do not bear out (app/case/builder.py grounded)
}

/** A row of the BNSS First Schedule for the offence's section. */
export interface Classification {
  section: string
  sub: string
  offence: string
  punishment: string
  cognizable: string
  bailable: string
  court: string
}

export interface OffenceCandidate {
  ref: string             // "BNS 103"
  name: string
  title: string
  act: string | null
  score: number
  has_checklist: boolean  // an elements checklist exists for it (app/case/elements.py)
  added?: boolean         // added by the lawyer by section number, not found by search
  classification: Classification[]
}

export type ElementStatus = 'shown' | 'not_shown' | 'unclear'

export interface ElementCheck {
  n: number
  label: string
  kind: 'element' | 'any_of'
  group: string
  law: { quote: string; source: string }
  status: ElementStatus
  fact: string             // the words of the facts that show it ("" if none)
  quote_checked: boolean   // false: the model quoted words not in the facts, so the tick was not accepted
}

export interface ElementsResult {
  ref: string
  name: string
  summary: 'met' | 'not met' | 'open'
  elements: ElementCheck[]
}

export interface Standing {
  cited_by: number
  followed_by: number
  distinguished_by: number
  doubted_by: number
  overruled_noted_in: { id: string; title: string; decided: string; evidence: string }[]
  per_incuriam_noted_in: { id: string; title: string; decided: string; evidence: string }[]
}

export interface LawThen {
  ref: string
  note: string            // written by code: what the provision said then, what changed, what replaced it
  amended_since: { action: string; by: string; effective: string | null }[]
  now: string[]
}

export interface Precedent {
  id: string
  title: string
  citation: string        // "[2007] 1 SCR 164 : 2007 INSC 4"
  decided: string
  bench_size: string
  bench: string[]
  catchline: string
  score: number
  matched_refs: string[]  // the provisions it shares with the case (old codes included)
  status: Standing
  holdings: { text: string; paras: string }[]
  passages: { n: number; page: boolean; text: string }[]
  law_at_time: LawThen[]
}

export interface ComparisonPoint {
  point: string
  our_fact: string
  their_fact: string
}

export interface Comparison {
  id: string
  similar: ComparisonPoint[]
  different: ComparisonPoint[]
  bearing: string
  dropped: number         // points left out because their quotes were not in either text
}
