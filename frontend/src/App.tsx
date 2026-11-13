import { useEffect, useState } from 'react'
import { ApiError, get, post, type Health, type ScenarioCard, type Session } from './api.ts'
import { CallScreen } from './call/CallScreen.tsx'
import { ErrorNote } from './ui.tsx'
import { errorText, useAction } from './util.ts'

const SESSION_PARAM = 'session'

/** The open call lives in the URL, so a reload rejoins it instead of dropping the learner back
 * at the case picker. */
function sessionInUrl(): string | null {
  return new URLSearchParams(window.location.search).get(SESSION_PARAM) || null
}

/** Session ids are short tokens. Anything else in the URL is not a call and must not reach an
 * API path. */
const SESSION_ID = /^[A-Za-z0-9_-]{1,64}$/

function callInUrl(): string | null {
  const id = sessionInUrl()
  return id !== null && SESSION_ID.test(id) ? id : null
}

const NOT_A_CALL = "Couldn't reopen that call: the link is not a call link."

function putSessionInUrl(sessionId: string | null): void {
  const url = new URL(window.location.href)
  if (sessionId) url.searchParams.set(SESSION_PARAM, sessionId)
  else url.searchParams.delete(SESSION_PARAM)
  window.history.replaceState(null, '', url)
}

export function App() {
  const [health, setHealth] = useState<Health | null | 'down'>(null)
  const [session, setSessionState] = useState<Session | null>(null)
  const [incoming, setIncoming] = useState<Session | null>(null)
  const [rejoining, setRejoining] = useState(() => callInUrl() !== null)
  const [rejoinError, setRejoinError] = useState<string | null>(() =>
    sessionInUrl() !== null && callInUrl() === null ? NOT_A_CALL : null,
  )
  const [canRetry, setCanRetry] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    get<Health>('/health').then(setHealth, () => setHealth('down'))
  }, [])

  useEffect(() => {
    const id = callInUrl()
    if (!id) {
      if (sessionInUrl() !== null) putSessionInUrl(null)
      return
    }
    get<Session>(`/sessions/${encodeURIComponent(id)}`)
      .then(
        (s) => (s.status === 'created' ? setIncoming(s) : setSessionState(s)),
        (err: unknown) => {
          // Only a call the server says is gone leaves the URL. Anything else may pass, and
          // the link is the only way back into the call.
          const gone = err instanceof ApiError && err.status === 404
          if (gone) putSessionInUrl(null)
          setCanRetry(!gone)
          setRejoinError(`Couldn't reopen that call: ${errorText(err)}`)
        },
      )
      .finally(() => setRejoining(false))
  }, [attempt])

  function retryRejoin(): void {
    setRejoinError(null)
    setCanRetry(false)
    setRejoining(true)
    setAttempt((n) => n + 1)
  }

  /** A call starting or being left. What the rejoin left behind is stale from here on: the
   * incoming card is for a call already answered, and the error is about an older link. */
  function setSession(s: Session | null): void {
    putSessionInUrl(s?.session_id ?? null)
    setIncoming(null)
    setRejoinError(null)
    setCanRetry(false)
    setSessionState(s)
  }

  if (rejoining) {
    return <main className="home muted">Rejoining the call…</main>
  }
  /** The debrief started another attempt: leave this call and show the new one ringing. */
  function retry(next: Session): void {
    setSession(null)
    putSessionInUrl(next.session_id)
    setIncoming(next)
  }

  if (session) {
    return (
      <CallScreen
        key={session.session_id}
        initial={session}
        actor={health && health !== 'down' ? health.actor : ''}
        onExit={() => setSession(null)}
        onRetry={retry}
      />
    )
  }
  return (
    <main className="home">
      <h1>Sales Call Simulator</h1>
      <p className="muted">
        Take an inbound Annual Election Period call from a synthetic Medicare beneficiary. Research
        fictional 2027 plans while you talk, record what you learn, and reach the right outcome.
      </p>
      {health === 'down' && <p className="error">The API is unreachable. Start the backend on port 8000.</p>}
      {health && health !== 'down' && health.actor === 'scripted' && (
        <p className="notice">
          Offline customer: no <code>GEMINI_API_KEY</code> is set, so a scripted stand-in answers from the
          case's facts by topic. It is not a realistic caller.
        </p>
      )}
      {rejoinError && canRetry ? (
        <div className="error" role="alert">
          {rejoinError} The call may still be open.{' '}
          <button type="button" className="small" onClick={retryRejoin}>
            Try again
          </button>
        </div>
      ) : (
        <ErrorNote error={rejoinError} />
      )}
      <CasePicker initial={incoming} onReady={setSession} />
    </main>
  )
}

function CasePicker({ initial, onReady }: { initial: Session | null; onReady: (s: Session) => void }) {
  const [cases, setCases] = useState<ScenarioCard[]>([])
  const [pending, setPendingState] = useState<Session | null>(initial)
  const [loadError, setLoadError] = useState<string | null>(null)
  const { busy, error, run } = useAction()

  useEffect(() => {
    get<ScenarioCard[]>('/scenarios').then(setCases, (err: unknown) => setLoadError(errorText(err)))
  }, [])

  function setPending(s: Session | null): void {
    putSessionInUrl(s?.session_id ?? null)
    setPendingState(s)
  }

  if (pending) {
    const crm = pending.crm_seed
    return (
      <section className="incoming">
        <h2>Incoming call</h2>
        <dl>
          <dt>Caller</dt>
          <dd>{crm.customer_name}</dd>
          <dt>Phone</dt>
          <dd>{crm.phone}</dd>
          <dt>ZIP</dt>
          <dd>{crm.zip}</dd>
          <dt>Lead source</dt>
          <dd>{crm.lead_source}</dd>
          {crm.notes && (
            <>
              <dt>CRM note</dt>
              <dd>{crm.notes}</dd>
            </>
          )}
        </dl>
        <p className="muted">
          You are a licensed agent at {pending.frame.agency_name}, representing {pending.frame.carriers_represented}{' '}
          organizations with {pending.frame.products_offered} products in {pending.frame.county_name}. Today is{' '}
          {pending.frame.call_date}.
        </p>
        {pending.retry_of && (
          <div className="notice retry-focus">
            <strong>Another attempt at this call.</strong>
            {(pending.retry_focus ?? []).length > 0 && (
              <>
                {' '}
                Your last debrief said to work on:
                <ul>
                  {(pending.retry_focus ?? []).map((title) => (
                    <li key={title}>{title}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        <div className="row">
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() =>
              void run(async () => onReady(await post<Session>(`/sessions/${pending.session_id}/start`)))
            }
          >
            Answer the call
          </button>
          <button type="button" onClick={() => setPending(null)} disabled={busy}>
            Back
          </button>
        </div>
        <ErrorNote error={error} />
      </section>
    )
  }

  return (
    <section>
      <h2>Choose a call</h2>
      <ul className="cases">
        {cases.map((c) => (
          <li key={c.scenario_id}>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => setPending(await post<Session>('/sessions', { scenario_id: c.scenario_id })))
              }
            >
              {c.learner_title}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNote error={error ?? loadError} />
    </section>
  )
}
