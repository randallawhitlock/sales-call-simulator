import type { ReactNode } from 'react'
import type { Utterance } from './api.ts'
import { useCall } from './call/context.tsx'
import { useAction } from './util.ts'

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className="error" role="alert">
      {error}
    </p>
  )
}

export function PinButton({ seq }: { seq: number }) {
  const { pin, pinned, live } = useCall()
  const { busy, error, run } = useAction()
  const done = pinned.has(seq)
  return (
    <span className="pin">
      <button
        type="button"
        className="small"
        disabled={!live || busy || done}
        onClick={() => void run(() => pin(seq))}
        title="Save this result to the record as evidence"
      >
        {done ? 'Pinned' : 'Pin to record'}
      </button>
      <ErrorNote error={error} />
    </span>
  )
}

export function Badge({ tone, children }: { tone: 'good' | 'bad' | 'warn' | 'info'; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  )
}

/** What was heard. A customer line the learner talked over is marked as cut off. */
export function UttText({ u, customer }: { u: Utterance; customer: string }) {
  if (!u.interrupted) return <p>{u.heard_text}</p>
  return (
    <p>
      {u.heard_text ? `${u.heard_text} ` : ''}
      <em className="muted">({u.heard_text ? 'cut off' : `cut off before ${customer} said anything`})</em>
    </p>
  )
}
