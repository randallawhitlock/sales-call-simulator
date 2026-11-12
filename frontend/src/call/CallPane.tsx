import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, clientId, get, post, type LogEvent, type Session, type TurnOut, type Utterance } from '../api.ts'
import { ErrorNote } from '../ui.tsx'
import { clock, errorText, useAction } from '../util.ts'
import { mergeTranscript, useCall } from './context.tsx'

const REJOIN_POLL_MS = 1000
const REJOIN_POLL_MAX_MS = 8000
/** Failed checks in a row before the rejoin poll gives up and hands the call back. */
const REJOIN_POLL_FAILURES = 5

interface SentTurn {
  id: string
  text: string
}

/** The line being answered is kept for this tab, so a reload mid-turn can retry it under the
 * same id instead of storing the learner's line twice. */
const sentKey = (sessionId: string): string => `sent-turn:${sessionId}`

function rememberSent(sessionId: string, turn: SentTurn | null): void {
  try {
    if (turn) window.sessionStorage.setItem(sentKey(sessionId), JSON.stringify(turn))
    else window.sessionStorage.removeItem(sentKey(sessionId))
  } catch {
    // No storage: a reload mid-turn just can't offer Retry.
  }
}

function recallSent(sessionId: string): SentTurn | null {
  try {
    const turn = JSON.parse(window.sessionStorage.getItem(sentKey(sessionId)) ?? 'null') as Partial<SentTurn> | null
    return turn && typeof turn.id === 'string' && typeof turn.text === 'string' ? { id: turn.id, text: turn.text } : null
  } catch {
    return null
  }
}

export function CallPane({ onSession }: { onSession: (s: Session) => void }) {
  const { session, base, live, transcript, setTranscript, source, setSource } = useCall()
  const [draft, setDraft] = useState('')
  // Both start from the server, so a reload mid-call shows the call as it really is.
  const [waiting, setWaiting] = useState(session.turn_pending)
  const [failed, setFailed] = useState<{ id: string; text: string; error: string } | null>(null)
  const [dropped, setDropped] = useState(false)
  /** What went wrong picking the call back up after a reload mid-turn. */
  const [rejoinNote, setRejoinNote] = useState<string | null>(null)
  const [onHold, setOnHold] = useState(session.on_hold)
  const [now, setNow] = useState(() => Date.now())
  const [rejoinedMidTurn] = useState(session.turn_pending)
  const control = useAction()
  const bottom = useRef<HTMLLIElement>(null)
  const customer = session.crm_seed.customer_name.split(' ')[0] ?? 'Customer'
  const sessionId = session.session_id

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [transcript.length, waiting])

  useEffect(() => {
    // Reloaded while the customer was answering: the reply is still being generated on the
    // server. Wait for it to finish (or be cut off), then pick it up from the transcript.
    if (!rejoinedMidTurn) return
    let stopped = false
    void (async () => {
      let delay = REJOIN_POLL_MS
      let failures = 0
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, delay))
        if (stopped) return
        try {
          const s = await get<Session>(base)
          if (stopped) return
          if (!s.turn_pending) break
          failures = 0
          delay = REJOIN_POLL_MS
        } catch (err) {
          if (stopped) return
          failures += 1
          if (failures >= REJOIN_POLL_FAILURES) {
            // Stop asking and hand the call back rather than lock the composer for good.
            setRejoinNote(`Couldn't check on ${customer}'s reply: ${errorText(err)}. Reload to look again, or carry on.`)
            setWaiting(false)
            return
          }
          delay = Math.min(delay * 2, REJOIN_POLL_MAX_MS)
        }
      }
      const saved = await get<Utterance[]>(`${base}/transcript`).catch(() => null)
      if (stopped) return
      if (saved) setTranscript((prev) => mergeTranscript(prev, saved))
      const last = saved?.at(-1)
      if (!saved) {
        setRejoinNote(`Couldn't load ${customer}'s reply. Reload to look again, or carry on.`)
      } else if (last?.speaker === 'learner') {
        // The turn ended with no reply: it failed, or a hold cut it off.
        const sent = recallSent(sessionId)
        if (sent && sent.text === last.text) {
          setFailed({ ...sent, error: `${customer} didn't answer your last line.` })
        } else {
          setRejoinNote(`${customer} didn't answer your last line. It was saved; say it again or carry on.`)
        }
      }
      setWaiting(false)
    })()
    return () => {
      stopped = true
    }
  }, [rejoinedMidTurn, base, setTranscript, customer, sessionId])

  async function send(id: string, text: string): Promise<void> {
    setWaiting(true)
    setFailed(null)
    setDropped(false)
    setRejoinNote(null)
    rememberSent(sessionId, { id, text })
    try {
      const out = await post<TurnOut>(`${base}/turns`, { client_utt_id: id, text })
      rememberSent(sessionId, null)
      setTranscript((prev) => mergeTranscript(prev, [out.learner, ...(out.customer ? [out.customer] : [])]))
      // The server drops a reply overtaken by a hold, the end of the call or a newer line.
      setDropped(out.customer === null)
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
        {dropped && !waiting && (
          <li className="muted hint">{customer} was cut off before answering that line.</li>
        )}
        <li ref={bottom} aria-hidden="true" />
      </ol>
      <ErrorNote error={rejoinNote} />
      {failed && (
        <div className="error" role="alert">
          {failed.error}{' '}
          <button
            type="button"
            className="small"
            onClick={() => void send(failed.id, failed.text)}
            disabled={waiting || !live || onHold}
            title={onHold ? `Take ${customer} off hold first` : undefined}
          >
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
          disabled={!live || control.busy}
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
          disabled={!live || control.busy}
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
