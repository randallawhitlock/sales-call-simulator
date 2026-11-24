import { ErrorNote } from '../ui.tsx'
import type { VoiceCall } from './useVoiceCall.ts'

/** Stands in for the typed composer on a voice call. */
export function VoicePanel({
  voice,
  customer,
  live,
  onHold,
}: {
  voice: VoiceCall
  customer: string
  live: boolean
  onHold: boolean
}) {
  const { audioHost } = voice
  const status = onHold && live ? 'On hold' : voice.status
  return (
    <div className="voice-panel">
      <div ref={audioHost} hidden />
      {voice.phase !== 'in' && (
        <button type="button" className="primary" disabled={!live || voice.phase === 'joining'} onClick={() => void voice.join()}>
          {voice.rejoin ? 'Rejoin call' : 'Join call'}
        </button>
      )}
      <div aria-live="polite" className="muted">
        {status}
      </div>
      {voice.phase === 'in' && voice.audioBlocked && (
        <div className="error" role="alert">
          Your browser blocked {customer}&apos;s audio.{' '}
          <button type="button" className="small" onClick={() => void voice.startAudio()}>
            Turn on sound
          </button>
        </div>
      )}
      <ErrorNote
        error={
          voice.micBlocked
            ? 'Your microphone is blocked. Allow the microphone for this site in your browser, then rejoin the call.'
            : voice.phase === 'dropped'
              ? `The call dropped. Rejoin to keep talking to ${customer}.`
              : voice.phase === 'alone'
                ? `${customer} isn't on the line anymore. Rejoin the call to get them back.`
                : voice.joinError
        }
      />
    </div>
  )
}
