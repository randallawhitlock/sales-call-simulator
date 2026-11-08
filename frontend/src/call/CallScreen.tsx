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
import { CallContext, mergeById, mergeTranscript, type CallContextValue, type Seen } from './context.tsx'
import { CallPane } from './CallPane.tsx'
import { RecordPane } from './RecordPane.tsx'
import { ResearchPane } from './ResearchPane.tsx'
import { Review } from './Review.tsx'

const EMPTY_SEEN: Seen = { plans: [], providers: [], drugs: [], pharmacies: [] }

export function CallScreen({ initial, actor, onExit }: { initial: Session; actor: string; onExit: () => void }) {
  const [session, setSession] = useState(initial)
  const [transcript, setTranscriptState] = useState<Utterance[]>([])
  const [record, setRecord] = useState<CustomerRecord | null>(null)
  const [applications, setApplications] = useState<ApplicationView[]>([])
  const [evidence, setEvidence] = useState<LogEvent[]>([])
  const [source, setSource] = useState<Utterance | null>(null)
  const [seen, setSeen] = useState<Seen>(EMPTY_SEEN)
  const [toast, setToast] = useState<string | null>(null)
  const base = `/sessions/${session.session_id}`
  const live = session.status === 'active'

  const refreshRecord = useCallback(async () => setRecord(await get<CustomerRecord>(`${base}/record`)), [base])
  const refreshApplications = useCallback(
    async () => setApplications(await get<ApplicationView[]>(`${base}/applications`)),
    [base],
  )
  const refreshEvidence = useCallback(async () => setEvidence(await get<LogEvent[]>(`${base}/evidence`)), [base])

  useEffect(() => {
    void Promise.all([
      get<Utterance[]>(`${base}/transcript`),
      get<CustomerRecord>(`${base}/record`),
      get<ApplicationView[]>(`${base}/applications`),
      get<LogEvent[]>(`${base}/evidence`),
    ]).then(([t, r, a, e]) => {
      // Merge, don't replace: a turn sent while this load was in flight must survive it.
      setTranscriptState((prev) => mergeTranscript(prev, t))
      setRecord(r)
      setApplications(a)
      setEvidence(e)
    })
  }, [base])

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
      setTranscript: (update) => setTranscriptState(update),
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
      remember: (found) =>
        setSeen((prev) => ({
          plans: mergeById(prev.plans, found.plans ?? [], (p) => p.plan_id),
          providers: mergeById(prev.providers, found.providers ?? [], (p) => p.provider_id),
          drugs: mergeById(prev.drugs, found.drugs ?? [], (d) => d.drug_id),
          pharmacies: mergeById(prev.pharmacies, found.pharmacies ?? [], (p) => p.pharmacy_id),
        })),
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
