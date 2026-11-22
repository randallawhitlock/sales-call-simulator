import { useCallback, useEffect, useRef, useState } from 'react'
import { ParticipantKind, Room, RoomEvent, Track, type RemoteParticipant } from 'livekit-client'
import { post } from '../api.ts'
import { errorText } from '../util.ts'

/** Same constraints on join and when a hold ends. */
const MIC = { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
const WRAP_UP_TIMEOUT_MS = 8000

interface VoiceJoin {
  url: string
  room: string
  identity: string
  token: string
}

export type VoicePhase = 'idle' | 'joining' | 'in' | 'dropped'

export interface VoiceCall {
  phase: VoicePhase
  /** Meaningful state for the live region: Connected, Reconnecting, Reconnected, left the call. */
  status: string
  micBlocked: boolean
  /** The browser blocked playback (no user gesture left by the time audio arrived). */
  audioBlocked: boolean
  joinError: string | null
  /** Joined before in this tab, or the call dropped: the button says Rejoin. */
  rejoin: boolean
  /** Ref callback for the hidden element that holds the customer's audio. */
  audioHost: (el: HTMLDivElement | null) => void
  join: () => Promise<void>
  /** Unblocks playback. Call it straight from a click. */
  startAudio: () => Promise<void>
  /** Turns the local mic on or off. Does nothing before the learner has joined. */
  setMic: (on: boolean) => Promise<void>
  /** Asks the customer to save lines still being transcribed. Never throws. */
  wrapUp: () => Promise<void>
  leave: () => void
}

const joinedKey = (sessionId: string): string => `voice-joined:${sessionId}`

function wasJoined(sessionId: string): boolean {
  try {
    return window.sessionStorage.getItem(joinedKey(sessionId)) !== null
  } catch {
    return false
  }
}

function markJoined(sessionId: string): void {
  try {
    window.sessionStorage.setItem(joinedKey(sessionId), '1')
  } catch {
    // No storage: after a reload the button just says Join.
  }
}

/** The LiveKit side of a voice call. Unmounting leaves the room but does not end the call. */
export function useVoiceCall({
  base,
  sessionId,
  customer,
  live,
  onHold,
}: {
  base: string
  sessionId: string
  customer: string
  live: boolean
  onHold: boolean
}): VoiceCall {
  const [phase, setPhase] = useState<VoicePhase>('idle')
  const [status, setStatus] = useState('')
  const [micBlocked, setMicBlocked] = useState(false)
  const [audioBlocked, setAudioBlocked] = useState(false)
  const [joinError, setJoinError] = useState<string | null>(null)
  const [joinedBefore] = useState(() => wasJoined(sessionId))
  const roomRef = useRef<Room | null>(null)
  const audioEl = useRef<HTMLDivElement | null>(null)
  const audioHost = useCallback((el: HTMLDivElement | null) => {
    audioEl.current = el
  }, [])
  // Read inside room events and join, which outlive the render that created them.
  const liveRef = useRef(live)
  const onHoldRef = useRef(onHold)
  useEffect(() => {
    liveRef.current = live
    onHoldRef.current = onHold
  }, [live, onHold])

  const leave = useCallback((): void => {
    const room = roomRef.current
    roomRef.current = null
    void room?.disconnect()
    audioEl.current?.replaceChildren()
  }, [])

  useEffect(() => leave, [leave])

  const join = useCallback(async (): Promise<void> => {
    setPhase('joining')
    setJoinError(null)
    setMicBlocked(false)
    setAudioBlocked(false)
    setStatus('')
    leave()
    let room: Room | null = null
    try {
      const creds = await post<VoiceJoin>(`${base}/voice`)
      const next = new Room()
      room = next
      const mine = (): boolean => roomRef.current === next
      next
        .on(RoomEvent.TrackSubscribed, (track) => {
          if (!mine() || track.kind !== Track.Kind.Audio) return
          audioEl.current?.appendChild(track.attach())
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          for (const el of track.detach()) el.remove()
        })
        .on(RoomEvent.ParticipantDisconnected, (p: RemoteParticipant) => {
          if (mine() && p.kind === ParticipantKind.AGENT) setStatus(`${customer} left the call`)
        })
        .on(RoomEvent.ParticipantConnected, (p: RemoteParticipant) => {
          if (mine() && p.kind === ParticipantKind.AGENT) setStatus('Connected')
        })
        .on(RoomEvent.Reconnecting, () => {
          if (mine()) setStatus('Reconnecting')
        })
        .on(RoomEvent.Reconnected, () => {
          if (mine()) setStatus('Reconnected')
        })
        .on(RoomEvent.AudioPlaybackStatusChanged, () => {
          if (mine()) setAudioBlocked(!next.canPlaybackAudio)
        })
        .on(RoomEvent.Disconnected, () => {
          if (!mine()) return
          roomRef.current = null
          audioEl.current?.replaceChildren()
          setAudioBlocked(false)
          setStatus('')
          if (liveRef.current) setPhase('dropped')
        })
      roomRef.current = next
      await next.connect(creds.url, creds.token)
      // By now the click's user activation may have run out, and Safari then refuses playback.
      // That isn't a failed join: the panel offers a button that starts audio from a fresh click.
      await next.startAudio().catch(() => {})
      await next.localParticipant.setMicrophoneEnabled(!onHoldRef.current, MIC)
      setAudioBlocked(!next.canPlaybackAudio)
      markJoined(sessionId)
      setPhase('in')
      setStatus('Connected')
    } catch (err) {
      if (room && roomRef.current === room) leave()
      setPhase('idle')
      if (err instanceof Error && err.name === 'NotAllowedError') setMicBlocked(true)
      else setJoinError(errorText(err))
    }
  }, [base, sessionId, customer, leave])

  const startAudio = useCallback(async (): Promise<void> => {
    const room = roomRef.current
    if (!room) return
    await room.startAudio().catch(() => {})
    setAudioBlocked(!room.canPlaybackAudio)
  }, [])

  const setMic = useCallback(async (on: boolean): Promise<void> => {
    await roomRef.current?.localParticipant.setMicrophoneEnabled(on, on ? MIC : undefined)
  }, [])

  const wrapUp = useCallback(async (): Promise<void> => {
    const room = roomRef.current
    if (!room) return
    const remotes = [...room.remoteParticipants.values()]
    const agent =
      remotes.find((p) => p.kind === ParticipantKind.AGENT) ??
      remotes.find((p) => p.identity !== room.localParticipant.identity)
    if (!agent) return
    try {
      await room.localParticipant.performRpc({
        destinationIdentity: agent.identity,
        method: 'sim.wrap_up',
        payload: '',
        responseTimeout: WRAP_UP_TIMEOUT_MS,
      })
    } catch {
      // The call still ends. Lines the customer had not saved yet are lost.
    }
  }, [])

  return {
    phase,
    status,
    micBlocked,
    audioBlocked,
    joinError,
    rejoin: phase === 'dropped' || joinedBefore,
    audioHost,
    join,
    startAudio,
    setMic,
    wrapUp,
    leave,
  }
}
