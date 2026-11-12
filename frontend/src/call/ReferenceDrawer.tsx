import { useEffect, useRef, useState } from 'react'
import { get, type Authority, type Reference, type RuleReference } from '../api.ts'
import { Badge, ErrorNote } from '../ui.tsx'
import { errorText } from '../util.ts'

/** Anything short of approved is labeled. Retired rules are not requirements and are left out. */
const STATUS: Record<Exclude<RuleReference['status'], 'approved' | 'retired'>, { tone: 'warn' | 'bad'; label: string }> = {
  unverified: { tone: 'warn', label: 'not yet verified' },
  disputed: { tone: 'bad', label: 'disputed' },
}

const AUTHORITY: Record<Authority, string> = {
  regulatory: 'Federal regulation',
  cms_guidance: 'CMS guidance',
  plan: 'Plan rule',
  organization: 'Agency policy',
  training: 'Training objective',
}

/** Reference material beside the call. Not modal, so the learner can read the disclaimer while
 * saying it. CMS standardized text is kept apart from this app's own summaries. */
export function ReferenceButton() {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)

  function close(): void {
    setOpen(false)
    trigger.current?.focus()
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="small"
        aria-expanded={open}
        aria-controls="reference-drawer"
        onClick={() => (open ? close() : setOpen(true))}
      >
        Reference
      </button>
      {open && <ReferenceDrawer onClose={close} />}
    </>
  )
}

function ReferenceDrawer({ onClose }: { onClose: () => void }) {
  const [ref, setRef] = useState<Reference | null>(null)
  const [error, setError] = useState<string | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeButton.current?.focus()
    get<Reference>('/reference').then(setRef, (err: unknown) => setError(errorText(err)))
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside id="reference-drawer" className="drawer" aria-label="Reference">
      <div className="drawer-head">
        <h2>Reference</h2>
        <button ref={closeButton} type="button" className="small" onClick={onClose}>
          Close
        </button>
      </div>
      <ErrorNote error={error} />
      {ref && (
        <>
          <p className="muted small-text">
            Rules verified as of {ref.verified_as_of} · ruleset {ref.ruleset_version}
          </p>

          <section>
            <h3>Standardized CMS text</h3>
            <p className="muted small-text">Say this as written. The numbers are filled in for this call.</p>
            {ref.standard.map((s) => (
              <div key={s.rule_id} className="card">
                <div className="row spread">
                  <strong>{s.title}</strong>
                  <Badge tone="info">CMS standardized</Badge>
                </div>
                <blockquote className="standard-text">{s.text}</blockquote>
                <Source citation={s.citation} urls={s.source_urls} verified={s.verified_as_of} />
              </div>
            ))}
          </section>

          <section>
            <h3>In this app's words</h3>
            <p className="muted small-text">
              Plain summaries of what each rule asks, labeled by where it comes from. Check the source for the
              exact text.
            </p>
            <ul className="plain">
              {ref.rules.filter(current).map((r) => (
                <li key={r.rule_id}>
                  <div className="row spread">
                    <strong>{r.title}</strong>
                    <span className="row">
                      <Badge tone={r.authority === 'organization' || r.authority === 'plan' ? 'warn' : 'info'}>
                        {AUTHORITY[r.authority]}
                      </Badge>
                      {r.status !== 'approved' && <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>}
                    </span>
                  </div>
                  <p className="small-text">{r.requirement}</p>
                  <Source citation={r.citation} urls={r.source_urls} verified={r.verified_as_of} />
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </aside>
  )
}

type CurrentRule = RuleReference & { status: Exclude<RuleReference['status'], 'retired'> }

function current(r: RuleReference): r is CurrentRule {
  return r.status !== 'retired'
}

function Source({ citation, urls, verified }: { citation: string; urls: string[]; verified: string | null }) {
  return (
    <p className="muted small-text">
      {citation}
      {verified && <> · verified {verified}</>}
      {urls.map((u, i) => (
        <span key={u}>
          {' · '}
          <a href={u} target="_blank" rel="noreferrer">
            source{urls.length > 1 ? ` ${i + 1}` : ''}
          </a>
        </span>
      ))}
    </p>
  )
}
