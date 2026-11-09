import { useEffect, useState } from 'react'
import { get, type CheckResult, type Findings, type Utterance } from '../api.ts'
import { Badge, ErrorNote } from '../ui.tsx'
import { errorText } from '../util.ts'
import { mergeTranscript, useCall } from './context.tsx'

const TONE: Record<CheckResult['status'], 'good' | 'bad' | 'warn' | 'info'> = {
  pass: 'good',
  fail: 'bad',
  ungraded: 'warn',
  not_observed: 'warn',
  not_applicable: 'info',
}

/** Deterministic findings after the call. The evidence-linked debrief and the judge land in
 * M3; this lists what the rule checks found. */
export function Review() {
  const { base, transcript, setTranscript } = useCall()
  const [findings, setFindings] = useState<CheckResult[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    get<Findings>(`${base}/findings`).then(
      (f) => setFindings(f.results),
      (err: unknown) => setError(errorText(err)),
    )
    // The call may have ended while a turn was in flight; show the transcript that was
    // graded, not whatever the call screen last had.
    get<Utterance[]>(`${base}/transcript`).then(
      (t) => setTranscript((prev) => mergeTranscript(prev, t)),
      () => {},
    )
  }, [base, setTranscript])

  return (
    <div className="review">
      <h2>Call ended</h2>
      <p className="muted">
        Rule-check findings for this call. The full debrief with the AI judge comes in a later milestone.
      </p>
      <ErrorNote error={error} />
      {findings && (
        <table>
          <thead>
            <tr><th>Check</th><th>Result</th><th>Detail</th><th>Cites</th></tr>
          </thead>
          <tbody>
            {findings.map((f) => (
              <tr key={f.check_id}>
                <td>{f.check_id.replaceAll('_', ' ')}</td>
                <td>
                  <Badge tone={TONE[f.status]}>{f.status.replaceAll('_', ' ')}</Badge>
                  {f.critical && <> <Badge tone="bad">critical</Badge></>}
                </td>
                <td>{f.detail}</td>
                <td className="muted small-text">
                  {[...f.utt_ids, ...f.event_seqs.map((s) => `event ${s}`)].join(', ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Transcript</h3>
      <ol className="transcript static">
        {transcript.map((u) => (
          <li key={u.utt_id} className={`utt ${u.speaker}`}>
            <div className="utt-meta"><span>{u.speaker === 'learner' ? 'You' : 'Customer'}</span><span className="muted">{u.utt_id}</span></div>
            <p>{u.heard_text}</p>
          </li>
        ))}
      </ol>
    </div>
  )
}
