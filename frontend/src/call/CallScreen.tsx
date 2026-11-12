import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  get,
  post,
  type ApplicationView,
  type CustomerRecord,
  type LogEvent,
  type Session,
  type Utterance,
} from '../api.ts'
import { CallContext, mergeSeen, mergeTranscript, type CallContextValue, type Seen } from './context.tsx'
import { CallPane } from './CallPane.tsx'
import { RecordPane } from './RecordPane.tsx'
import { ReferenceButton } from './ReferenceDrawer.tsx'
import { ResearchPane } from './ResearchPane.tsx'
import { Review } from './Review.tsx'
import { useLatest } from './useLatest.ts'

const EMPTY_SEEN: Seen = { plans: [], providers: [], drugs: [], pharmacies: [] }

export function CallScreen({ initial, actor, onExit }: { initial: Session; actor: string; onExit: () => void }) {
  const [session, setSession] = useState(initial)
  const [transcript, setTranscriptState] = useState<Utterance[]>([])
  const [source, setSource] = useState<Utterance | null>(null)
  const [seen, setSeen] = useState<Seen>(EMPTY_SEEN)
  const [toast, setToast] = useState<string | null>(null)
  const base = `/sessions/${session.session_id}`
  const live = session.status === 'active'

  // Each resource keeps only its newest response, so the initial load below can't overwrite
  // what a save fetched while it was still in flight.
  const [record, refreshRecord] = useLatest(
    useCallback(() => get<CustomerRecord>(`${base}/record`), [base]),
    null as CustomerRecord | null,
  )
  const [applications, refreshApplications] = useLatest(
    useCallback(() => get<ApplicationView[]>(`${base}/applications`), [base]),
    [] as ApplicationView[],
  )
  const [evidence, refreshEvidence] = useLatest(
    useCallback(() => get<LogEvent[]>(`${base}/evidence`), [base]),
    [] as LogEvent[],
  )

  useEffect(() => {
    // Merge, don't replace: a turn sent while this load was in flight must survive it.
    void get<Utterance[]>(`${base}/transcript`).then((t) => setTranscriptState((prev) => mergeTranscript(prev, t)))
    // After a reload, offer again what earlier searches found, so record pickers still work.
    void get<Seen>(`${base}/found`).then((f) => setSeen((prev) => mergeSeen(prev, f)))
    void Promise.resolve().then(() => Promise.all([refreshRecord(), refreshApplications(), refreshEvidence()]))
  }, [base, refreshRecord, refreshApplications, refreshEvidence])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2500)
    return () => clearTimeout(t)
  }, [toast])

  const value = useMemo<CallContextValue>(
    () => ({
      session,
      base,
      live,
      transcript,
      setTranscript: setTranscriptState,
      record,
      refreshRecord,
      applications,
      refreshApplications,
      evidence,
      pin: async (seq) => {
        await post(`${base}/evidence`, { source_seq: seq })
        await refreshEvidence()
        setToast('Pinned to the record')
      },
      pinned: new Set(evidence.map((e) => Number(e.payload['source_seq']))),
      source,
      setSource,
      seen,
      remember: (found) => setSeen((prev) => mergeSeen(prev, found)),
      notify: setToast,
    }),
    [session, base, live, transcript, record, refreshRecord, applications, refreshApplications, evidence, refreshEvidence, source, seen],
  )

  return (
    <CallContext.Provider value={value}>
      <div className="call">
        <header className="call-header">
          <div>
            <strong>{session.learner_title}</strong>
            <span className="muted">
              {' '}
              · {session.frame.agency_name} · {session.frame.call_date} · Plan year {session.frame.plan_year}
            </span>
          </div>
          <div className="row">
            {actor && <span className={`badge ${actor === 'scripted' ? 'warn' : 'info'}`}>Customer: {actor}</span>}
            <ReferenceButton />
            <button type="button" className="small" onClick={onExit}>
              Leave
            </button>
          </div>
        </header>
        {session.status === 'ended' ? (
          <Review />
        ) : (
          <div className="panes">
            <CallPane onSession={setSession} />
            <ResearchPane />
            <RecordPane />
          </div>
        )}
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
      </div>
    </CallContext.Provider>
  )
}
