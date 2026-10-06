import type { ElementCheck, ElementsResult, ElementStatus } from '../../types/case'

export const MAX_OFFENCES = 6    // the brief takes at most six provisions
export const MAX_PRECEDENTS = 4  // and at most four judgments

/** 'met', 'not met' or 'open', as app/case/builder.py element_summary: every required element,
 *  and at least one of each group of alternatives. */
export function summarise(checks: ElementCheck[]): ElementsResult['summary'] {
  const states: ElementStatus[] = checks.filter(c => c.kind === 'element').map(c => c.status)
  const groups = new Map<string, ElementStatus[]>()
  for (const c of checks.filter(c => c.kind === 'any_of')) groups.set(c.group, [...(groups.get(c.group) ?? []), c.status])
  for (const alts of groups.values()) {
    states.push(alts.includes('shown') ? 'shown' : alts.includes('unclear') ? 'unclear' : 'not_shown')
  }
  if (states.includes('not_shown')) return 'not met'
  return states.every(s => s === 'shown') ? 'met' : 'open'
}

/** "bns 85", "BNS s.85", "Section 85 BNS" -> "BNS 85"; '' when it is not a BNS, IPC or BNSS section. */
export function normaliseRef(text: string): string {
  const t = text.toUpperCase().replace(/SECTION|SEC\.?|\bS\./g, ' ')
  const act = t.match(/\b(BNSS|BNS|IPC|CRPC)\b/)?.[1]
  const num = t.match(/\b(\d{1,3}[A-Z]{0,2})\b/)?.[1]
  return act && num ? `${act} ${num}` : ''
}
