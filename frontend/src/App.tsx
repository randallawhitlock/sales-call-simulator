import { useEffect, useState } from 'react'
import { get, post, type Health, type ScenarioCard, type Session } from './api.ts'
import { CallScreen } from './call/CallScreen.tsx'
import { ErrorNote } from './ui.tsx'
import { errorText, useAction } from './util.ts'

export function App() {
  const [health, setHealth] = useState<Health | null | 'down'>(null)
  const [session, setSession] = useState<Session | null>(null)

  useEffect(() => {
    get<Health>('/health').then(setHealth, () => setHealth('down'))
  }, [])

  if (session) {
    return <CallScreen initial={session} actor={health && health !== 'down' ? health.actor : ''} onExit={() => setSession(null)} />
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
      <CasePicker onReady={setSession} />
    </main>
  )
}

function CasePicker({ onReady }: { onReady: (s: Session) => void }) {
  const [cases, setCases] = useState<ScenarioCard[]>([])
  const [pending, setPending] = useState<Session | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const { busy, error, run } = useAction()

  useEffect(() => {
    get<ScenarioCard[]>('/scenarios').then(setCases, (err: unknown) => setLoadError(errorText(err)))
  }, [])

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
