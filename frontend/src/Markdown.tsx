import type { ReactNode } from 'react'

/** Renders the small Markdown subset training documents use (headings, tables, lists,
 * paragraphs, **bold**) as React elements. No HTML is injected. */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  const lines = text.split(/\r?\n/)
  let i = 0
  while (i < lines.length) {
    const start = i
    const line = lines[i] ?? ''
    if (!line.trim()) {
      i++
      continue
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      const level = Math.min(6, (heading[1]?.length ?? 1) + 2)
      const Tag = `h${level}` as 'h3'
      blocks.push(<Tag key={start}>{inline(heading[2] ?? '')}</Tag>)
      i++
      continue
    }
    if (line.trimStart().startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && (lines[i] ?? '').trimStart().startsWith('|')) {
        const cells = (lines[i] ?? '').trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells)
        i++
      }
      const [head, ...body] = rows
      blocks.push(
        <table key={start}>
          {head && (
            <thead>
              <tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr>
            </thead>
          )}
          <tbody>
            {body.map((r, k) => (
              <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i] ?? '')) {
        items.push((lines[i] ?? '').replace(/^\s*([-*]|\d+\.)\s+/, ''))
        i++
      }
      const List = ordered ? 'ol' : 'ul'
      blocks.push(<List key={start}>{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</List>)
      continue
    }
    // Always consume the current line, even one that looks like the start of another block
    // (`#hashtag`), so the loop can't stall.
    const para: string[] = [line]
    i++
    while (
      i < lines.length &&
      (lines[i] ?? '').trim() &&
      !/^#{1,4}\s/.test(lines[i] ?? '') &&
      !(lines[i] ?? '').trimStart().startsWith('|') &&
      !/^\s*([-*]|\d+\.)\s+/.test(lines[i] ?? '')
    ) {
      para.push(lines[i] ?? '')
      i++
    }
    blocks.push(<p key={start}>{inline(para.join(' '))}</p>)
  }
  return <div className="markdown">{blocks}</div>
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
  )
}
