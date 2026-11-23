import { useCallback, useEffect, useState } from 'react'
import {
  get,
  post,
  type ActorQa,
  type ConsistencyFlag,
  type CriterionResult,
  type Debrief,
  type Dimension,
  type EvidenceView,
  type GradeStatus,
  type Session,
  type Utterance,
  type VoiceStats,
} from '../api.ts'
import { Badge, ErrorNote, UttText } from '../ui.tsx'
import { errorText, useAction } from '../util.ts'
import { mergeTranscriptFor, useCall } from './context.tsx'

const VOICE_STATS_POLL_MS = 1000
/** The worker's last write lands within about ten seconds of the end of the call. */
const VOICE_STATS_WAIT_MS = 20_000

const STATUS: Record<GradeStatus, { label: string; tone: 'good' | 'bad' | 'warn' | 'info' }> = {
  pass: { label: 'pass', tone: 'good' },
  fail: { label: 'missed', tone: 'bad' },
  not_observed: { label: 'not observed', tone: 'bad' },
  not_applicable: { label: "doesn't apply", tone: 'info' },
  review_required: { label: 'needs review', tone: 'warn' },
  ungraded: { label: 'ungraded', tone: 'warn' },
}

const DIMENSIONS: Record<Dimension, string> = {
  discovery: 'Discovery and listening',
  eligibility: 'Eligibility and timing',
  verification: 'Provider, drug and pharmacy verification',
  recommendation: 'Recommendation and trade-offs',
  documentation: 'Documentation and application',
  communication: 'Communication and customer choice',
  completion: 'Completion and follow-up',
}

/** The debrief after the call: every rubric item with its evidence, the score over what could
 * be graded, and a targeted retry. Asks the server to run the judge once if it hasn't yet. */
export function Review({ onRetry }: { onRetry?: (s: Session) => void }) {
  const { base, session, transcript, setTranscript } = useCall()
  const [debrief, setDebrief] = useState<Debrief | null>(null)
  const [grading, setGrading] = useState(false)
  const [actorFlags, setActorFlags] = useState<ConsistencyFlag[]>([])
  const [error, setError] = useState<string | null>(null)
  const [gradeError, setGradeError] = useState<string | null>(null)
  const [voiceStats, setVoiceStats] = useState<VoiceStats | null>(null)
  const [statsSettled, setStatsSettled] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const retry = useAction()
  const voice = session.channel === 'voice'
  const merge = mergeTranscriptFor(session)

  /** Asks the server to run the judge. A request that fails outright (a timeout, a dropped
   * connection) is kept apart from a judge failure the server reports, and either one leaves
   * the learner a way to ask again. */
  const grade = useCallback(
    async (alive: () => boolean = () => true) => {
      setGrading(true)
      setGradeError(null)
      try {
        const graded = await post<Debrief>(`${base}/grade`)
        if (alive()) setDebrief(graded)
      } catch (err) {
        if (alive()) setGradeError(errorText(err))
      } finally {
        if (alive()) setGrading(false)
      }
    },
    [base],
  )

  useEffect(() => {
    let current = true
    get<Debrief>(`${base}/debrief`)
      .then((d) => {
        if (!current) return
        setDebrief(d)
        if (d.judge.status === 'pending' && d.judge.runs_left > 0) void grade(() => current)
      })
      .catch((err: unknown) => current && setError(errorText(err)))
    // Content QA only; the debrief works without it.
    get<ActorQa>(`${base}/actor-qa`).then(
      (qa) => current && setActorFlags(qa.flags ?? []),
      () => {},
    )
    // The call may have ended while a turn was in flight; show the transcript that was
    // graded, not whatever the call screen last had.
    get<Utterance[]>(`${base}/transcript`).then(
      (t) => current && setTranscript((prev) => merge(prev, t)),
      () => {},
    )
    return () => {
      current = false
    }
  }, [base, grade, setTranscript, merge])

  // An extra: a call with no stats just doesn't show them, or its recording. The worker saves
  // its last figures a few seconds after the call ends, so keep asking until they're final.
  // Stats that never get there (a worker that crashed) still show once the wait runs out.
  useEffect(() => {
    if (!voice) return
    let current = true
    let timer: number | undefined
    const deadline = Date.now() + VOICE_STATS_WAIT_MS
    function again(): void {
      if (Date.now() < deadline) timer = window.setTimeout(poll, VOICE_STATS_POLL_MS)
      else setStatsSettled(true)
    }
    function poll(): void {
      get<VoiceStats>(`${base}/voice-stats`).then(
        (s) => {
          if (!current) return
          setVoiceStats(s)
          if (s.final) setStatsSettled(true)
          else again()
        },
        () => {
          if (current) again()
        },
      )
    }
    poll()
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [base, voice])

  function show(uttId: string): void {
    setPicked(uttId)
    document.getElementById(`line-${uttId}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }

  const titles = new Map(debrief?.criteria.map((c) => [c.criterion_id, c.title]) ?? [])

  return (
    <div className="review">
      <h2>Call debrief</h2>
      <ErrorNote error={error} />
      {debrief && (
        <>
          <Summary debrief={debrief} grading={grading} />
          {!grading && (gradeError !== null || debrief.judge.status === 'failed') && (
            <div className="error" role="alert">
              The AI judge couldn't grade the conversation: {gradeError ?? debrief.judge.error}.{' '}
              {debrief.judge.runs_left > 0 ? (
                <button type="button" className="small" onClick={() => void grade()}>
                  Grade again
                </button>
              ) : (
                'This call has no grading runs left.'
              )}
            </div>
          )}
          {debrief.critical_findings.length > 0 && (
            <section className="critical" role="alert">
              <h3>Critical findings</h3>
              <ul>
                {debrief.critical_findings.map((id) => {
                  const c = debrief.criteria.find((x) => x.criterion_id === id)
                  return (
                    <li key={id}>
                      <strong>{c?.title ?? id}.</strong> {c?.detail}
                    </li>
                  )
                })}
              </ul>
            </section>
          )}
          <table className="dimensions">
            <thead>
              <tr><th>Area</th><th>Points</th><th>Weight</th></tr>
            </thead>
            <tbody>
              {debrief.dimensions.map((d) => (
                <tr key={d.dimension}>
                  <td>{DIMENSIONS[d.dimension]}</td>
                  <td>{d.possible ? `${d.earned} of ${d.possible}` : '—'}</td>
                  <td className="muted">{d.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {debrief.dimensions.map((d) => (
            <section key={d.dimension} className="dimension">
              <h3>{DIMENSIONS[d.dimension]}</h3>
              <ul className="criteria">
                {debrief.criteria
                  .filter((c) => c.dimension === d.dimension)
                  .map((c) => (
                    <Criterion key={c.criterion_id} c={c} onShow={show} />
                  ))}
              </ul>
            </section>
          ))}
          <section className="retry">
            <h3>Practice this call again</h3>
            {debrief.focus.length > 0 ? (
              <>
                <p>Work on these next time:</p>
                <ol>
                  {debrief.focus.map((id) => (
                    <li key={id}>{titles.get(id) ?? id}</li>
                  ))}
                </ol>
              </>
            ) : (
              <p className="muted">Nothing graded was missed. Try it again with a different customer mood.</p>
            )}
            {onRetry && (
              <button
                type="button"
                className="primary"
                disabled={retry.busy}
                onClick={() =>
                  void retry.run(async () =>
                    onRetry(
                      await post<Session>('/sessions', {
                        scenario_id: session.scenario_id,
                        retry_of: session.session_id,
                        channel: session.channel,
                      }),
                    ),
                  )
                }
              >
                Retry this call
              </button>
            )}
            <ErrorNote error={retry.error} />
          </section>
        </>
      )}
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
                <Badge tone="warn">{f.kind}</Badge> “{f.said}” <span className="muted small-text">in {f.utt_id}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {voiceStats && <VoiceStatsBlock stats={voiceStats} base={base} settled={statsSettled} />}
      <h3>Transcript</h3>
      <ol id="review-transcript" className="transcript static">
        {transcript.map((u) => (
          <li key={u.utt_id} id={`line-${u.utt_id}`} className={`utt ${u.speaker}${picked === u.utt_id ? ' picked' : ''}`}>
            <div className="utt-meta"><span>{u.speaker === 'learner' ? 'You' : 'Customer'}</span><span className="muted">{u.utt_id}</span></div>
            <UttText u={u} customer="Customer" />
          </li>
        ))}
      </ol>
    </div>
  )
}

const seconds = (n: number | null): string => (n === null ? 'n/a' : `${n.toFixed(1)} s`)

/** A voice call often costs a fraction of a cent, so amounts under a dollar keep four decimals. */
const usd = (n: number): string =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: n < 1 ? 4 : 2 })

/** `settled`: the stats are final, so the recording is complete. */
function VoiceStatsBlock({ stats, base, settled }: { stats: VoiceStats; base: string; settled: boolean }) {
  const parts = stats.recording_parts ?? (stats.recording !== null ? 1 : 0)
  return (
    <section className="voice-stats">
      <h3>Voice call</h3>
      <dl>
        <dt>Reply delay, median</dt>
        <dd>{seconds(stats.latency_median_s)}</dd>
        <dt>Reply delay, 95th percentile</dt>
        <dd>{seconds(stats.latency_p95_s)}</dd>
        <dt>Times you talked over the customer</dt>
        <dd>{stats.barge_ins}</dd>
        <dt>Nudges</dt>
        <dd>{stats.nudges}</dd>
        <dt>Reconnects</dt>
        <dd>{stats.reconnects}</dd>
        <dt>Audio in / out</dt>
        <dd>
          {stats.audio_minutes.input.toFixed(1)} / {stats.audio_minutes.output.toFixed(1)} min
        </dd>
        <dt>Estimated cost</dt>
        <dd>{usd(stats.cost_usd_estimate)}</dd>
      </dl>
      <p className="muted small-text">{stats.cost_basis}</p>
      {settled &&
        Array.from({ length: parts }, (_, i) => (
          <audio
            key={i}
            controls
            preload="none"
            src={`/api${base}/recording${i === 0 ? '' : `?part=${i + 1}`}`}
            aria-label={parts > 1 ? `Call recording, part ${i + 1} of ${parts}` : 'Call recording'}
            aria-details="review-transcript"
          />
        ))}
    </section>
  )
}

function Summary({ debrief, grading }: { debrief: Debrief; grading: boolean }) {
  const { judge, score, complete } = debrief
  return (
    <section className="summary">
      <p className="score">
        {score === null ? 'No score yet' : complete ? `${score} / 100` : `${score} / 100 so far`}
        {!complete && <> <Badge tone="warn">incomplete</Badge></>}
      </p>
      <p className="muted">
        {complete
          ? `Every item that applies was graded: ${debrief.earned} of ${debrief.possible} points.`
          : `Scored on the ${debrief.possible} points graded so far. Items marked ungraded or needs review don't count yet.`}
      </p>
      {grading && <p className="notice" role="status">The AI judge is grading the conversation…</p>}
      {judge.status === 'pending' && !grading && judge.runs_left === 0 && (
        <p className="notice">
          The AI judge couldn't grade this call, and it has no grading runs left. Conversation items stay ungraded.
        </p>
      )}
      {judge.status === 'off' && (
        <p className="notice">
          No AI judge is configured (set <code>ANTHROPIC_API_KEY</code>), so conversation items stay ungraded. Rule
          checks still ran.
        </p>
      )}
      {judge.status === 'done' && (
        <p className="muted small-text">
          Conversation items graded by {judge.name}. Every quote below was checked against the transcript.
        </p>
      )}
    </section>
  )
}

function Criterion({ c, onShow }: { c: CriterionResult; onShow: (uttId: string) => void }) {
  const status = STATUS[c.status]
  return (
    <li className={`criterion ${c.status}`}>
      <div className="criterion-head">
        <strong>{c.title}</strong>
        <span className="row">
          <Badge tone={status.tone}>{status.label}</Badge>
          {c.critical && <Badge tone="bad">critical</Badge>}
          <span className="muted small-text">{c.earned === null ? `– / ${c.points}` : `${c.earned} / ${c.points}`}</span>
        </span>
      </div>
      {c.detail && <p>{c.detail}</p>}
      {c.evidence.length > 0 && (
        <ul className="evidence">
          {c.evidence.map((e, i) => (
            <Evidence key={`${e.utt_id}-${i}`} e={e} onShow={onShow} />
          ))}
        </ul>
      )}
      {c.rules.length > 0 && (
        <p className="muted small-text">
          {c.rules.map((r, i) => (
            <span key={r.rule_id}>
              {i > 0 && ' · '}
              {r.rule_id} {r.title} ({r.citation})
              {r.status !== 'approved' && <> <Badge tone="warn">{r.status} rule</Badge></>}
            </span>
          ))}
        </p>
      )}
    </li>
  )
}

function Evidence({ e, onShow }: { e: EvidenceView; onShow: (uttId: string) => void }) {
  const who = e.utt_id === 'notes' ? 'Your notes' : e.speaker === 'customer' ? 'Customer' : 'You'
  return (
    <li className={e.verified ? '' : 'unverified'}>
      “{e.quote}” <span className="muted small-text">— {who}</span>{' '}
      {e.utt_id !== 'notes' && (
        <button type="button" className="link small-text" onClick={() => onShow(e.utt_id)}>
          {e.utt_id}
        </button>
      )}
      {!e.verified && <> <Badge tone="warn">quote not found{e.problem ? `: ${e.problem}` : ''}</Badge></>}
    </li>
  )
}
