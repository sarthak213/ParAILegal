import type { SourceChunk } from '../types'

/**
 * Format a source chunk as a proper legal citation string.
 * Used for the "Copy Citation" feature.
 */
export function formatLegalCitation(source: SourceChunk): string {
  const citation = source.citation || source.hierarchy || source.section || ''
  const title = source.document_title || ''

  if (!citation) return title || 'Unknown source'

  // First line of citation is the main identifier
  const firstLine = citation.split('\n')[0].trim()

  if (title && !firstLine.includes(title)) {
    return `${firstLine}, ${title}`
  }
  return firstLine
}

// ── Citations in answers ───────────────────────────────────────────────

export type Act = 'BNS' | 'BNSS' | 'BSA'

export interface ParsedCitation {
  kind: 'section' | 'article' | 'case' | 'other'
  number?: string
  /** First sub-section, e.g. "(1)" in "Section 103(1)(a)" */
  sub?: string
  act?: Act
  text: string
}

const ACT_PATTERNS: [Act, RegExp][] = [
  // BNSS before BNS: "BNS" is a prefix of "BNSS"
  ['BNSS', /\bBNSS\b|nagarik\s+suraksha/i],
  ['BNS', /\bBNS\b|nyaya\s+sanhita/i],
  ['BSA', /\bBSA\b|sakshya/i],
]

/**
 * Turn "[Section 35(1)(b), BNSS]", "[Article 21A]" or "[Bachan Singh v State of Punjab]"
 * into a structured citation, so it can be matched to a source by its fields, not by text.
 */
export function parseCitation(raw: string): ParsedCitation {
  const text = raw.trim()
  const section = text.match(/\b(?:section|sec\.?|s\.)\s*(\d+[A-Z]?)\s*(\(\w+\))?/i)
  if (section) {
    const act = ACT_PATTERNS.find(([, re]) => re.test(text))?.[0]
    return { kind: 'section', number: section[1].toUpperCase(), sub: section[2]?.toLowerCase(), act, text }
  }
  const article = text.match(/\b(?:article|art\.)\s*(\d+[A-Z]?)/i)
  if (article) return { kind: 'article', number: article[1].toUpperCase(), text }
  if (/\s(?:v\.?|vs\.?|versus)\s/i.test(text)) return { kind: 'case', text }
  return { kind: 'other', text }
}

/** The act a statute chunk belongs to, from its chunk id ("bnss_482_1" → BNSS) or title. */
export function actOf(source: SourceChunk): Act | undefined {
  const prefix = (source.chunk_id || '').split('_')[0].toUpperCase()
  if (prefix === 'BNS' || prefix === 'BNSS' || prefix === 'BSA') return prefix
  const title = source.document_title || ''
  return ACT_PATTERNS.find(([, re]) => re.test(title))?.[0]
}

function isJudgement(source: SourceChunk): boolean {
  return /judgement/i.test(source.source_type || '')
}

/** Distinctive words of a case name: drops "v", "State of", "Union of India" and the like. */
function caseKeywords(name: string): string[] {
  const stop = new Set(['v', 'vs', 'versus', 'state', 'of', 'union', 'india', 'the', 'and', 'ltd', 'others', 'anr', 'ors'])
  return name.toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(w => w.length > 2 && !stop.has(w))
}

/**
 * Index of the source a citation refers to, or -1 if none of the retrieved sources match.
 * A -1 means the answer cites something that was not retrieved: the UI flags it.
 */
export function matchSource(citation: ParsedCitation, sources: SourceChunk[]): number {
  if (citation.kind === 'section') {
    const candidates = sources
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => !isJudgement(s) && s.source_type !== 'constitution'
        && (s.section || '').toUpperCase() === citation.number)
    const sameAct = candidates.filter(({ s }) => citation.act && actOf(s) === citation.act)
    // Prefer the chunk for the cited sub-section: "Section 103(1)" → the "(1)" chunk, not "(2)"
    const exact = sameAct.find(({ s }) => citation.sub && (s.label || '').toLowerCase() === citation.sub) ?? sameAct[0]
    if (exact) return exact.i
    // No act named in the citation: accept only an unambiguous number.
    if (!citation.act && candidates.length > 0 && new Set(candidates.map(({ s }) => actOf(s))).size === 1) {
      return candidates[0].i
    }
    return -1
  }
  if (citation.kind === 'article') {
    return sources.findIndex(s => s.source_type === 'constitution' && (s.section || '').toUpperCase() === citation.number)
  }
  if (citation.kind === 'case') {
    const words = caseKeywords(citation.text.split(/\s(?:v\.?|vs\.?|versus)\s/i)[0])
    if (!words.length) return -1
    return sources.findIndex(s => {
      if (!isJudgement(s)) return false
      const hay = `${s.citation || ''} ${s.chunk_id || ''} ${s.document_title || ''}`.toLowerCase().replace(/_/g, ' ')
      return words.every(w => hay.includes(w))
    })
  }
  return -1
}

/**
 * Bracketed text in an answer that should become a citation chip. Adjacent brackets are allowed
 * ("[1][3]": the answer model writes them), so markdown reference links ("[x][y]") are not kept.
 */
const CITATION_RE = /(?<![!\\])\[([^[\]\n]{1,160})\](?![(:])/g

/** "[1]", "[2, 3]": the v2 answer model cites its sources by number, in the order they were sent. */
const SOURCE_NUMBERS_RE = /^\s*\d{1,2}(?:\s*[,;]\s*\d{1,2})*\s*$/

export function isSourceNumbers(label: string): boolean {
  return SOURCE_NUMBERS_RE.test(label)
}

function looksLikeCitation(label: string): boolean {
  return isSourceNumbers(label)
    || /\b(?:section|sec\.?|s\.|article|art\.)\s*\d|\bBNSS?\b|\bBSA\b|\s(?:v\.?|vs\.?|versus)\s|\bAIR\b|\bSCC\b|source unspecified/i.test(label)
}

/**
 * A short label for a numbered source chip: "S. 103 BNS", "Art. 21", "S. 138 NI Act 1881",
 * "Bachan Singh". Built from the source itself, so it is right whatever the model wrote.
 */
export function shortLabel(source: SourceChunk): string {
  const first = (source.citation || source.hierarchy || '').split('\n')[0]
  const head = first.split('—')[0].trim()
  if (source.source_type === 'constitution') return head.replace(/^Article\s+/i, 'Art. ')
  if (/judge?ment/i.test(source.source_type || '')) return judgmentLabel(first)
  const section = head.replace(/^Section\s+/i, 'S. ')
  const act = actOf(source) ?? shortActName(source.document_title || '')
  return act ? `${section} ${act}` : section
}

/**
 * A chip-sized judgment name: the first party and the year, "Jagjit Singh (2018)", from
 * "JAGJIT SINGH v. STATE OF PUNJAB, [2018] 13 SCR 405 : 2018 INSC 883 (Supreme Court, decided 2018-09-26)".
 */
function judgmentLabel(header: string): string {
  const party = header.split(/\s+v(?:s)?\.?\s+/i)[0]
    .replace(/\s*(?:&|and)\s+(?:ORS|ANR|OTHERS|ANOTHER)\.?.*$/i, '')
    .replace(/\s*@.*$/, '')  // "CHARAN SINGH @ CHARANJIT SINGH": the first name it gives
    .trim()
  const name = party.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase())
  const year = header.match(/decided\s+(\d{4})/)?.[1] ?? header.match(/\[(\d{4})\]|\b(\d{4})\s+INSC/)?.slice(1).find(Boolean)
  return (name || 'Judgment') + (year ? ` (${year})` : '')
}

/**
 * A chip-sized Act name: short titles stay whole ("Code on Wages 2019"), longer ones become the
 * initials of their capitalised words ("Payment and Settlement Systems Act, 2007" -> "PSSA 2007").
 * The full title is in the chip's tooltip.
 */
function shortActName(title: string): string {
  const t = title.replace(/^The\s+/i, '').replace(/,/g, '')
  const year = t.match(/\b(1[89]\d\d|20\d\d)\b/)?.[1] ?? ''
  const words = t.replace(/\b(1[89]\d\d|20\d\d)\b/, '').split(/\s+/).filter(Boolean)
  const name = words.length <= 3 ? words.join(' ') : words.filter(w => /^[A-Z]/.test(w)).map(w => w[0]).join('')
  return [name, year].filter(Boolean).join(' ')
}

/**
 * Rewrite "[Section 103, BNS]" into a markdown link "[Section 103, BNS](#cite)" so the markdown
 * renderer hands it to the citation chip component. Ordinary brackets are left alone.
 */
export function linkCitations(markdown: string): string {
  return markdown.replace(CITATION_RE, (whole, label: string) =>
    looksLikeCitation(label) ? `[${label}](#cite)` : whole)
}

// ── Display helpers ────────────────────────────────────────────────────

/**
 * Get source type display label and color class.
 * Statute chunks report source_type "statutes", so the act comes from the chunk itself.
 */
export function getSourceMeta(sourceType?: string, source?: SourceChunk): { label: string; colorClass: string } {
  const act = source ? actOf(source) : undefined
  const type = (act || sourceType || '').toLowerCase()
  switch (type) {
    case 'constitution':
      return { label: 'Constitution', colorClass: 'source-tag--constitution' }
    case 'bns':
      return { label: 'BNS 2023', colorClass: 'source-tag--bns' }
    case 'bnss':
      return { label: 'BNSS 2023', colorClass: 'source-tag--bnss' }
    case 'bsa':
      return { label: 'BSA 2023', colorClass: 'source-tag--bsa' }
    case 'statutes':
      return { label: 'Statutes', colorClass: 'source-tag--statutes' }
    case 'judgement':
    case 'judgements':
      return { label: 'Judgement', colorClass: 'source-tag--judgement' }
    default:
      return { label: sourceType || 'Source', colorClass: 'source-tag--default' }
  }
}

/**
 * Format timestamp as relative time string.
 */
export function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`
  return new Date(timestamp).toLocaleDateString('en-IN', {
    day: 'numeric', month: 'short'
  })
}
