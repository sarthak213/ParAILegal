import type { SourceChunk } from '../types'
import { formatLegalCitation } from './citations'

/**
 * Copy text to clipboard with fallback.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Fallback for older browsers
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  }
}

/**
 * Copy a source citation in legal format.
 */
export async function copyCitation(source: SourceChunk): Promise<boolean> {
  const citation = formatLegalCitation(source)
  return copyToClipboard(citation)
}

/**
 * Export the answer and sources as a formatted text for PDF printing.
 * Opens a print dialog with a clean printable layout.
 */
export function exportToPDF(
  query: string,
  answer: string,
  sources: SourceChunk[],
  domain: string,
): void {
  const timestamp = new Date().toLocaleDateString('en-IN', {
    day: 'numeric', month: 'long', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })

  const html = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>ParAILegal Research — ${query.slice(0, 60)}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Georgia', serif;
      font-size: 12pt;
      line-height: 1.7;
      color: #1a1a1a;
      padding: 2.5cm 3cm;
      max-width: 21cm;
      margin: 0 auto;
    }
    .header {
      border-bottom: 2px solid #1a1a1a;
      padding-bottom: 12pt;
      margin-bottom: 24pt;
    }
    .logo {
      font-size: 18pt;
      font-weight: bold;
      letter-spacing: -0.5px;
    }
    .logo span { color: #8b1c2b; }
    .meta {
      font-size: 9pt;
      color: #666;
      margin-top: 4pt;
    }
    .query-section {
      background: #f5f5f5;
      border-left: 3px solid #8b1c2b;
      padding: 12pt 16pt;
      margin-bottom: 20pt;
    }
    .query-label {
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #666;
      margin-bottom: 4pt;
    }
    .query-text {
      font-size: 13pt;
      font-weight: bold;
    }
    .domain-badge {
      display: inline-block;
      background: #1a1a1a;
      color: white;
      font-size: 8pt;
      padding: 2pt 8pt;
      margin-top: 8pt;
      letter-spacing: 1px;
      text-transform: uppercase;
    }
    .answer-section { margin-bottom: 28pt; }
    .section-title {
      font-size: 9pt;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #666;
      border-bottom: 1px solid #ddd;
      padding-bottom: 6pt;
      margin-bottom: 14pt;
    }
    .answer-text { white-space: pre-wrap; }
    .sources-section {}
    .source-item {
      margin-bottom: 14pt;
      padding-bottom: 14pt;
      border-bottom: 1px solid #eee;
    }
    .source-item:last-child { border-bottom: none; }
    .source-num {
      font-size: 9pt;
      color: #8b1c2b;
      font-weight: bold;
    }
    .disclaimer {
      margin-top: 28pt;
      border-top: 1px solid #ddd;
      padding-top: 10pt;
      font-size: 9pt;
      color: #888;
      font-style: italic;
    }
    @media print {
      body { padding: 0; }
      .no-print { display: none; }
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="logo">Par<span>AI</span>Legal</div>
    <div class="meta">Indian Legal Research · Generated ${timestamp}</div>
  </div>

  <div class="query-section">
    <div class="query-label">Research Query</div>
    <div class="query-text">${escHtml(query)}</div>
    ${domain ? `<div class="domain-badge">${escHtml(domain)}</div>` : ''}
  </div>

  <div class="answer-section">
    <div class="section-title">Answer</div>
    <div class="answer-text">${escHtml(answer.replace(/\[([^\]]+)\]/g, '[$1]'))}</div>
  </div>

  ${sources.length > 0 ? `
  <div class="sources-section">
    <div class="section-title">Retrieved Sources (${sources.length})</div>
    ${sources.map((s, i) => `
    <div class="source-item">
      <div class="source-num">[${i + 1}] ${escHtml(formatLegalCitation(s))}</div>
      ${s.text ? `<div style="font-size:10pt;color:#444;margin-top:4pt;">${escHtml(s.text.slice(0, 400))}${s.text.length > 400 ? '…' : ''}</div>` : ''}
    </div>
    `).join('')}
  </div>
  ` : ''}

  <div class="disclaimer">
    ⚖ This is a research tool. Verify all provisions against the official Gazette. This is not legal advice.
  </div>

  <script>window.print(); window.onafterprint = () => window.close();</script>
</body>
</html>`

  const blob = new Blob([html], { type: 'text/html' })
  const url = URL.createObjectURL(blob)
  const w = window.open(url, '_blank')
  if (w) setTimeout(() => URL.revokeObjectURL(url), 10000)
}

function escHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
