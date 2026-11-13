import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  get,
  post,
  type ActorQa,
  type Authority,
  type ConsistencyFlag,
  type CriterionResult,
  type Debrief,
  type Dimension,
  type GradeStatus,
  type RuleLabel,
  type Session,
  type Utterance,
} from '../api.ts'
import { Badge, ErrorNote } from '../ui.tsx'
import { clock, errorText, useAction } from '../util.ts'
import { mergeTranscript, useCall } from './context.tsx'

const STATUS: Record<GradeStatus, { label: string; tone: 'good' | 'bad' | 'warn' | 'info' }> = {
  pass: { label: 'Pass', tone: 'good' },
  fail: { label: 'Fail', tone: 'bad' },
  not_observed: { label: 'Not observed', tone: 'warn' },
  not_applicable: { label: 'Not applicable', tone: 'info' },
  review_required: { label: 'Held for review', tone: 'warn' },
  ungraded: { label: 'Not graded', tone: 'info' },
}

const DIMENSION: Record<Dimension, string> = {
  discovery: 'Discovery and listening',
  eligibility: 'Eligibility and timing',
  verification: 'Provider, drug and pharmacy verification',
  recommendation: 'Recommendation and tradeoffs',
  documentation: 'Documentation and application',
  communication: 'Communication and customer choice',
  completion: 'Completion and follow-up',
}

const AUTHORITY: Record<Authority, string> = {
  regulatory: 'Regulation',
  cms_guidance: 'CMS guidance',
  plan: 'Plan rule',
  organization: 'Organization policy',
  training: 'Training objective',
}

/** Statuses that count toward the total. The rest are left out of it. */
const SCORED: GradeStatus[] = ['pass', 'fail', 'not_observed']

const uttAnchor = (uttId: string): string => `utt-${uttId}`
const eventAnchor = (seq: number): string => `event-${seq}`

function outcomeText(outcome: Debrief['outcome']): string {
  switch (outcome.kind) {
    case 'enroll':
      return `Enrolled the customer in ${outcome.plan_name ?? 'a plan'}`
    case 'keep_current':
      return `Kept ${outcome.plan_name ?? 'the current coverage'}`
    case 'defer':
      return 'Deferred the decision'
    case 'stop':
      return 'Stopped without a change'
    default:
      return 'No outcome was recorded'
  }
}

function pointsOf(criteria: CriterionResult[], status: GradeStatus): number {
  return criteria.filter((c) => c.status === status).reduce((sum, c) => sum + c.points, 0)
}

/** The debrief: the score, every finding with the lines and actions behind it, and what to
 * work on next time. Clicking a piece of evidence shows it in the call record alongside. */
export function Review({ onRetry }: { onRetry: (next: Session) => void }) {
  const { base, session, transcript, setTranscript } = useCall()
  const [debrief, setDebrief] = useState<Debrief | null>(null)
  const [judging, setJudging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actorFlags, setActorFlags] = useState<ConsistencyFlag[]>([])
  const [shown, setShown] = useState<string | null>(null)
  const retry = useAction()

  const askJudge = useCallback(
    async (regrade: boolean): Promise<void> => {
      setJudging(true)
      setError(null)
      try {
        setDebrief(await post<Debrief>(`${base}/debrief/judge`, { regrade }))
      } catch (err) {
        setError(errorText(err))
      } finally {
        setJudging(false)
      }
    },
    [base],
  )

  useEffect(() => {
    let stale = false
    get<Debrief>(`${base}/debrief`).then(
      (d) => {
        if (stale) return
        setDebrief(d)
        // The rule checks are already on screen; the judge fills in the rest when it answers.
        if (d.judge.state === 'not_run') void askJudge(false)
      },
      (err: unknown) => !stale && setError(errorText(err)),
    )
    // Content QA only; the debrief works without it.
    get<ActorQa>(`${base}/actor-qa`).then(
      (qa) => !stale && setActorFlags(qa.flags ?? []),
      () => {},
    )
    // The call may have ended while a turn was in flight; show the transcript that was
    // graded, not whatever the call screen last had.
    get<Utterance[]>(`${base}/transcript`).then(
      (t) => !stale && setTranscript((prev) => mergeTranscript(prev, t)),
      () => {},
    )
    return () => {
      stale = true
    }
  }, [base, setTranscript, askJudge])

  function show(anchor: string): void {
    setShown(anchor)
    const el = document.getElementById(anchor)
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  if (!debrief) {
    return (
      <div className="review">
        <h2>Call ended</h2>
        {error ? <ErrorNote error={error} /> : <p className="muted">Loading the debrief…</p>}
      </div>
    )
  }

  const critical = debrief.criteria.filter((c) => debrief.critical.includes(c.criterion_id))
  return (
    <div className="review">
      <Summary debrief={debrief} judging={judging} onJudge={askJudge} />
      <ErrorNote error={error} />
      {critical.length > 0 && (
        <section className="critical" aria-label="Critical findings">
          <h3>Critical findings</h3>
          <ul>
            {critical.map((c) => (
              <li key={c.criterion_id}>
                <strong>Failed: {c.title}.</strong> {c.rationale ?? c.detail}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="debrief-body">
        <section aria-label="Scorecard">
          {debrief.dimensions.map((d) => {
            const rows = debrief.criteria.filter((c) => c.dimension === d.dimension)
            return (
              <section key={d.dimension} className="dimension">
                <div className="row spread">
                  <h3>{DIMENSION[d.dimension]}</h3>
                  <span className="muted small-text">
                    {d.possible > 0 ? `${d.earned} of ${d.possible} points` : 'Nothing scored'}
                  </span>
                </div>
                <ul className="criteria">
                  {rows.map((c) => (
                    <Criterion key={c.criterion_id} result={c} onShow={show} />
                  ))}
                </ul>
              </section>
            )
          })}
        </section>
        <CallRecord debrief={debrief} transcript={transcript} shown={shown} />
      </div>
      <section className="card retry" aria-label="Try again">
        <h3>Try this call again</h3>
        {debrief.retry_focus.length > 0 ? (
          <>
            <p>Next time, put your attention here:</p>
            <ol>
              {debrief.retry_focus.map((f) => (
                <li key={f.criterion_id}>
                  <strong>{f.title}.</strong> {f.why}
                </li>
              ))}
            </ol>
          </>
        ) : (
          <p className="muted">Nothing scored was missed. Another attempt gets a different mood from the caller.</p>
        )}
        <button
          type="button"
          className="primary"
          disabled={retry.busy}
          onClick={() =>
            void retry.run(async () =>
              onRetry(
                await post<Session>('/sessions', { scenario_id: debrief.scenario_id, retry_of: session.session_id }),
              ),
            )
          }
        >
          Retry this call
        </button>
        <ErrorNote error={retry.error} />
      </section>
      <CaseNotes debrief={debrief} />
      {actorFlags.length > 0 && (
        <section className="actor-qa">
          <h3>AI customer check</h3>
          <p className="muted">
            Not part of your score. The AI customer said these things that its case never gave it, so treat
            them as made up. Content authors use this to fix the case.
          </p>
          <ul>
            {actorFlags.map((f) => (
              <li key={`${f.utt_id}-${f.kind}-${f.said}`}>
                <Badge tone="warn">{f.kind}</Badge> “{f.said}”{' '}
                <button type="button" className="chip" onClick={() => show(uttAnchor(f.utt_id))}>
                  {f.utt_id}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="muted small-text">
        Graded on scenario v{debrief.versions.scenario_version}, catalog {debrief.versions.catalog_version}, rules{' '}
        {debrief.versions.ruleset_version}, rubric {debrief.versions.rubric}. The rubric is an original training
        design, not issued by CMS.
      </p>
    </div>
  )
}

function Summary({
  debrief,
  judging,
  onJudge,
}: {
  debrief: Debrief
  judging: boolean
  onJudge: (regrade: boolean) => Promise<void>
}) {
  const { score, judge, criteria } = debrief
  const waiting = pointsOf(criteria, 'ungraded')
  const held = pointsOf(criteria, 'review_required')
  const heldCount = criteria.filter((c) => c.status === 'review_required').length
  const total = criteria.filter((c) => SCORED.includes(c.status)).length
  return (
    <header className="debrief-head">
      <div>
        <h2>Debrief</h2>
        <p className="score">
          <span className="score-number">{score.earned}</span> of {score.possible} points
          {debrief.state === 'incomplete' ? ' graded so far' : ''}
          {score.percent !== null && <span className="muted"> ({score.percent}%)</span>}
        </p>
        <p className="muted small-text">
          {outcomeText(debrief.outcome)} · {total} of {criteria.length} items scored
        </p>
      </div>
      <div className="debrief-state">
        {debrief.state === 'complete' && <Badge tone="good">Grading complete</Badge>}
        {debrief.state === 'needs_review' && <Badge tone="warn">Needs review</Badge>}
        {debrief.state === 'incomplete' && <Badge tone="info">Incomplete</Badge>}
        {waiting > 0 && (
          <p className="small-text">
            {waiting} points are not graded yet, so they are left out of the total.{' '}
            {judging || judge.state === 'not_run'
              ? 'The judge is reading the call. This can take a minute or two.'
              : judge.state === 'off'
                ? 'No judge is configured: set ANTHROPIC_API_KEY on the server to grade the conversation.'
                : judge.state === 'failed'
                  ? `The judge did not answer: ${judge.error ?? 'unknown error'}.`
                  : 'The judge returned no ruling on them.'}
          </p>
        )}
        {heldCount > 0 && (
          <p className="small-text">
            {heldCount === 1 ? '1 item is' : `${heldCount} items are`} held for review ({held} points) and left
            out of the total: a ruling that can’t be traced to a line of the call never counts against you.
          </p>
        )}
        {judge.state === 'graded' && (
          <p className="muted small-text">
            Conversation judged by {judge.served_model ?? judge.judge}
            {judge.attempt !== null && judge.attempt > 1 ? `, attempt ${judge.attempt}` : ''}.
            {judge.error && ` The last regrade failed: ${judge.error}.`}
          </p>
        )}
        {(judge.state === 'failed' || judge.state === 'graded') && (
          <button
            type="button"
            className="small"
            disabled={judging}
            onClick={() => {
              const regrade = judge.state === 'graded'
              if (regrade && !window.confirm('Ask the judge to grade this call again? The earlier rulings are kept on file.')) return
              void onJudge(regrade)
            }}
          >
            {judging ? 'Judging…' : judge.state === 'failed' ? 'Ask the judge again' : 'Regrade'}
          </button>
        )}
      </div>
    </header>
  )
}

function Criterion({ result: c, onShow }: { result: CriterionResult; onShow: (anchor: string) => void }) {
  const status = STATUS[c.status]
  const scored = SCORED.includes(c.status)
  return (
    <li className={`criterion ${c.status}`}>
      <div className="criterion-head">
        <span className="criterion-title">{c.title}</span>
        <span className="criterion-result">
          {c.critical && <Badge tone="bad">Critical</Badge>} <Badge tone={status.tone}>{status.label}</Badge>{' '}
          <span className="points" title={scored ? undefined : 'Left out of the total'}>
            {scored ? c.earned : '–'}/{c.points}
          </span>
        </span>
      </div>
      {c.detail && <p>{c.detail}</p>}
      {c.rationale && (
        <p>
          <span className="muted">Judge: </span>
          {c.rationale}
        </p>
      )}
      {c.quotes.map((q) => (
        <blockquote key={`${q.utt_id}-${q.quote}`} className={q.verified ? 'quote' : 'quote unverified'}>
          “{q.quote}”{' '}
          {q.utt_id === null ? (
            <span className="muted small-text">saved notes</span>
          ) : q.verified ? (
            <EvidenceChip label={q.utt_id} anchor={uttAnchor(q.utt_id)} onShow={onShow} />
          ) : (
            <span className="muted small-text">{q.utt_id}</span>
          )}
          {!q.verified && <Badge tone="warn">not found where cited</Badge>}
        </blockquote>
      ))}
      {c.claims.length > 0 && (
        <ul className="claims">
          {c.claims.map((claim) => (
            <li key={`${claim.utt_id}-${claim.label}`}>
              <Badge tone={claim.status === 'backed' ? 'good' : claim.status === 'unbacked' ? 'bad' : 'warn'}>
                {claim.status === 'backed' ? 'Backed' : claim.status === 'unbacked' ? 'No lookup' : 'Can’t check'}
              </Badge>{' '}
              {claim.status === 'unverifiable' ? `“${claim.quote}”` : claim.label}. {claim.detail}{' '}
              {claim.status !== 'unverifiable' && (
                <EvidenceChip label={claim.utt_id} anchor={uttAnchor(claim.utt_id)} onShow={onShow} />
              )}
              {claim.event_seqs.map((seq) => (
                <EvidenceChip key={seq} label={`event ${seq}`} anchor={eventAnchor(seq)} onShow={onShow} />
              ))}
            </li>
          ))}
        </ul>
      )}
      {(c.utt_ids.length > 0 || c.event_seqs.length > 0) && (
        <p className="evidence small-text">
          <span className="muted">Evidence: </span>
          {c.utt_ids.map((id) => (
            <EvidenceChip key={id} label={id} anchor={uttAnchor(id)} onShow={onShow} />
          ))}
          {c.event_seqs.map((seq) => (
            <EvidenceChip key={seq} label={`event ${seq}`} anchor={eventAnchor(seq)} onShow={onShow} />
          ))}
        </p>
      )}
      <p className="sources muted small-text">
        {c.rules.length === 0 ? 'Training objective' : c.rules.map((r) => <Source key={r.rule_id} rule={r} />)}
      </p>
    </li>
  )
}

function EvidenceChip({ label, anchor, onShow }: { label: string; anchor: string; onShow: (anchor: string) => void }) {
  return (
    <button type="button" className="chip" onClick={() => onShow(anchor)} title="Show in the call record">
      {label}
    </button>
  )
}

/** Where a requirement comes from. A local policy is never shown as a federal rule, and a cite
 * nobody has confirmed says so. */
function Source({ rule }: { rule: RuleLabel }) {
  return (
    <span className="source">
      {AUTHORITY[rule.authority]}: {rule.title} ({rule.citation}){' '}
      {rule.status === 'unverified' && <Badge tone="warn">cite unverified</Badge>}
    </span>
  )
}

/** Everything said and done, in order. Evidence links land here. */
function CallRecord({
  debrief,
  transcript,
  shown,
}: {
  debrief: Debrief
  transcript: Utterance[]
  shown: string | null
}) {
  const byId = useMemo(() => new Map(transcript.map((u) => [u.utt_id, u])), [transcript])
  return (
    <aside className="call-record" aria-label="Call record">
      <h3>Call record</h3>
      <ol className="transcript static">
        {debrief.timeline.map((entry) => {
          if (entry.utt_id === null) {
            const anchor = eventAnchor(entry.seq)
            return (
              <li key={entry.seq} id={anchor} className={`activity${shown === anchor ? ' shown' : ''}`}>
                <span className="muted">
                  event {entry.seq} · {clock(entry.t_ms)}
                </span>{' '}
                {entry.summary}
              </li>
            )
          }
          const u = byId.get(entry.utt_id)
          if (!u) return null
          const anchor = uttAnchor(u.utt_id)
          return (
            <li key={entry.seq} id={anchor} className={`utt ${u.speaker}${shown === anchor ? ' shown' : ''}`}>
              <div className="utt-meta">
                <span>{u.speaker === 'learner' ? 'You' : 'Customer'}</span>
                <span className="muted">
                  {u.utt_id} · {clock(u.t_ms)}
                </span>
              </div>
              <p>{u.heard_text || <span className="muted">(cut off before anything was heard)</span>}</p>
            </li>
          )
        })}
      </ol>
    </aside>
  )
}

function CaseNotes({ debrief }: { debrief: Debrief }) {
  const { case: notes } = debrief
  return (
    <details className="card case-notes">
      <summary>What this case was testing: {notes.title}</summary>
      <h4>Outcomes</h4>
      <ul>
        {notes.outcomes.map((o) => (
          <li key={`${o.kind}-${o.plans.join()}`}>
            <Badge tone={o.acceptable ? 'good' : 'bad'}>{o.acceptable ? 'Acceptable' : 'Not acceptable'}</Badge>{' '}
            <strong>
              {o.kind.replaceAll('_', ' ')}
              {o.plans.length > 0 && `: ${o.plans.join(', ')}`}
            </strong>
            . {o.rationale}
          </li>
        ))}
      </ul>
      {notes.traps.length > 0 && (
        <>
          <h4>Traps</h4>
          <ul>
            {notes.traps.map((t) => (
              <li key={t.description}>
                {t.description} <span className="muted">{t.rationale}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  )
}
