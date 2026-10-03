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

/**
 * Render answer text with [Citation] tags replaced by styled spans.
 * Returns an array of {text, isCitation, citation} segments.
 */
export interface TextSegment {
  text: string
  isCitation: boolean
  citation?: string
}

export function parseAnswerCitations(text: string): TextSegment[] {
  // Strip markdown formatting — Sarvam sometimes returns **bold** and *italic*
  const stripped = text
    .replace(/\*\*([^*]+)\*\*/g, '$1')  // **bold** → plain
    .replace(/\*([^*]+)\*/g, '$1')       // *italic* → plain

  const segments: TextSegment[] = []
  const citationRe = /\[([^[\]]+)\]/g
  let last = 0
  let match: RegExpExecArray | null

  while ((match = citationRe.exec(stripped)) !== null) {
    if (match.index > last) {
      segments.push({ text: stripped.slice(last, match.index), isCitation: false })
    }
    segments.push({ text: match[0], isCitation: true, citation: match[1] })
    last = match.index + match[0].length
  }

  if (last < stripped.length) {
    segments.push({ text: stripped.slice(last), isCitation: false })
  }

  return segments
}

/**
 * Get source type display label and color class.
 */
export function getSourceMeta(sourceType?: string): { label: string; colorClass: string } {
  switch (sourceType?.toLowerCase()) {
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