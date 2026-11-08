import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, clientId, get, post, type LogEvent, type Session, type TurnOut, type Utterance } from '../api.ts'
import { ErrorNote } from '../ui.tsx'
import { clock, errorText, useAction } from '../util.ts'
import { mergeTranscript, useCall } from './context.tsx'

export function CallPane({ onSession }: { onSession: (s: Session) => void }) {
  const { session, base, live, transcript, setTranscript, source, setSource } = useCall()
  const [draft, setDraft] = useState('')
  const [waiting, setWaiting] = useState(false)
  const [failed, setFailed] = useState<{ id: string; text: string; error: string } | null>(null)
  const [onHold, setOnHold] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const control = useAction()
  const bottom = useRef<HTMLLIElement>(null)
  const customer = session.crm_seed.customer_name.split(' ')[0] ?? 'Customer'

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [transcript.length, waiting])

  async function send(id: string, text: string): Promise<void> {
    setWaiting(true)
    setFailed(null)
    try {
      const out = await post<TurnOut>(`${base}/turns`, { client_utt_id: id, text })
      setTranscript((prev) => mergeTranscript(prev, [out.learner, ...(out.customer ? [out.customer] : [])]))
    } catch (err) {
      setFailed({ id, text, error: errorText(err) })
      if (err instanceof ApiError && err.status === 502) {
        // The learner's line was saved even though the reply failed; show it.
        const saved = await get<Utterance[]>(`${base}/transcript`).catch(() => null)
        if (saved) setTranscript((prev) => mergeTranscript(prev, saved))
      }
    } finally {
      setWaiting(false)
    }
  }

  function submit(e?: FormEvent): void {
    e?.preventDefault()
    const text = draft.trim()
    if (!text || waiting) return
    setDraft('')
    void send(clientId('turn'), text)
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const started = session.started_at ? Date.parse(session.started_at) : now
  return (
    <section className="pane call-pane" aria-label="Call">
      <div className="pane-head">
        <h2>Call</h2>
        <span className="timer" aria-label="Call time">
          {clock(now - started)}
        </span>
      </div>
      <ol className="transcript">
        {transcript.length === 0 && <li className="muted hint">The caller is on the line. Greet them to begin.</li>}
        {transcript.map((u) => {
          const picked = source?.utt_id === u.utt_id
          return (
            <li key={u.utt_id} className={`utt ${u.speaker}${picked ? ' picked' : ''}`}>
              <div className="utt-meta">
                <span>{u.speaker === 'learner' ? 'You' : customer}</span>
                <span className="muted">
                  {u.utt_id} · {clock(u.t_ms)}
                </span>
              </div>
              <p>{u.heard_text}</p>
              {u.speaker === 'customer' && live && (
                <button
                  type="button"
                  className="small link"
                  onClick={() => setSource(picked ? null : u)}
                  title="Cite this line when you save a record field or mark consent"
                >
                  {picked ? 'Selected as source' : 'Use as source'}
                </button>
              )}
            </li>
          )
        })}
        {waiting && <li className="muted hint">{customer} is answering…</li>}
        <li ref={bottom} aria-hidden="true" />
      </ol>
      {failed && (
        <div className="error" role="alert">
          {failed.error}{' '}
          <button type="button" className="small" onClick={() => void send(failed.id, failed.text)} disabled={waiting}>
            Retry
          </button>
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder={onHold ? `${customer} is on hold` : `Say something to ${customer}… (Enter to send)`}
          disabled={!live || waiting || onHold}
          rows={3}
          aria-label="Your line"
        />
        <button type="submit" className="primary" disabled={!live || waiting || onHold || !draft.trim()}>
          Send
        </button>
      </form>
      <div className="row controls">
        <button
          type="button"
          disabled={!live || control.busy || waiting}
          title={waiting ? `Wait for ${customer} to finish` : undefined}
          onClick={() =>
            void control.run(async () => {
              await post<LogEvent>(`${base}/hold`, { on: !onHold })
              setOnHold(!onHold)
            })
          }
        >
          {onHold ? 'Take off hold' : 'Hold'}
        </button>
        <button
          type="button"
          className="danger"
          disabled={!live || control.busy || waiting}
          onClick={() => {
            if (!window.confirm('End the call? The debrief opens and the call cannot resume.')) return
            void control.run(async () => onSession(await post<Session>(`${base}/end`)))
          }}
        >
          End call
        </button>
      </div>
      <ErrorNote error={control.error} />
    </section>
  )
}
