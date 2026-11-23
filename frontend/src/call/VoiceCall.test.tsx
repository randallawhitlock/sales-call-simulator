// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { Session, Utterance, VoiceStats } from '../api.ts'
import { App } from '../App.tsx'

type Handler = (...args: unknown[]) => void

const lk = vi.hoisted(() => {
  const state = {
    rooms: [] as unknown[],
    rpc: (() => Promise.resolve('')) as () => Promise<string>,
    order: [] as string[],
    startAudioError: null as Error | null,
    micError: null as Error | null,
  }
  return state
})

vi.mock('livekit-client', () => {
  class FakeRoom {
    handlers = new Map<string, Handler>()
    remoteParticipants = new Map([['agent-1', { identity: 'agent-1', kind: 'agent' }]])
    canPlaybackAudio = false
    connect = vi.fn(async () => {})
    // Like the real one: a blocked start throws and leaves playback off.
    startAudio = vi.fn(async () => {
      this.canPlaybackAudio = lk.startAudioError === null
      if (lk.startAudioError) throw lk.startAudioError
    })
    disconnect = vi.fn(async () => {})
    localParticipant = {
      identity: 'learner-1',
      setMicrophoneEnabled: vi.fn(async () => {
        if (lk.micError) throw lk.micError
      }),
      performRpc: vi.fn(() => {
        lk.order.push('rpc')
        return lk.rpc()
      }),
    }
    constructor() {
      lk.rooms.push(this)
    }
    on(event: string, handler: Handler) {
      this.handlers.set(event, handler)
      return this
    }
  }
  return {
    Room: FakeRoom,
    RoomEvent: {
      TrackSubscribed: 'trackSubscribed',
      TrackUnsubscribed: 'trackUnsubscribed',
      ParticipantConnected: 'participantConnected',
      ParticipantDisconnected: 'participantDisconnected',
      Reconnecting: 'reconnecting',
      Reconnected: 'reconnected',
      Disconnected: 'disconnected',
      AudioPlaybackStatusChanged: 'audioPlaybackChanged',
    },
    ParticipantKind: { AGENT: 'agent' },
    Track: { Kind: { Audio: 'audio' } },
  }
})

interface FakeRoomShape {
  handlers: Map<string, Handler>
  connect: ReturnType<typeof vi.fn>
  startAudio: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
  localParticipant: { setMicrophoneEnabled: ReturnType<typeof vi.fn>; performRpc: ReturnType<typeof vi.fn> }
}
const room = (): FakeRoomShape => lk.rooms.at(-1) as FakeRoomShape

const SESSION: Session = {
  session_id: 's-1',
  scenario_id: 'scenario-101',
  learner_title: 'Inbound call',
  status: 'active',
  frame: {
    call_date: '2026-11-12',
    plan_year: 2027,
    coverage_effective: '2027-01-01',
    agency_name: 'Larkspur Benefits Group',
    learner_role: 'agent',
    county_name: 'Test County',
    carriers_represented: 3,
    products_offered: 6,
  },
  crm_seed: { customer_name: 'Ada Lane', phone: '555-0100', zip: '00042', lead_source: 'Inbound', notes: null },
  created_at: '2026-11-12T15:00:00Z',
  started_at: '2026-11-12T15:00:00Z',
  ended_at: null,
  on_hold: false,
  turn_pending: false,
  retry_of: null,
  focus: [],
  channel: 'voice',
}

const line = (utt_id: string, speaker: Utterance['speaker'], t_ms: number, heard: string, interrupted = false): Utterance => ({
  utt_id,
  speaker,
  channel: 'voice',
  text: heard,
  heard_text: heard,
  interrupted,
  t_ms,
})

const STATS: VoiceStats = {
  final: true,
  recording_parts: 1,
  latency_median_s: 1.2,
  latency_p95_s: 2.5,
  latency_max_s: 3,
  turns_timed: 4,
  barge_ins: 2,
  nudges: 1,
  holds: 0,
  reconnects: 1,
  session_restarts: 0,
  audio_minutes: { input: 3.5, output: 2.25 },
  cost_usd_estimate: 0.42,
  cost_basis: 'Estimated from audio minutes at list price.',
  call_seconds: 300,
  model: 'gemini-live:test',
  recording: 'rec.ogg',
  errors: [],
}

type StatsReply = VoiceStats | null | (() => VoiceStats | null)

function serve(opts: { voice?: string; transcript?: Utterance[]; session?: Session; stats?: StatsReply } = {}) {
  const session = opts.session ?? SESSION
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, '').split('?')[0]!
    const method = init?.method ?? 'GET'
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    if (path === '/health')
      return json({ status: 'ok', version: 't', actor: 'live', catalog_version: 'v', judge: 'off', voice: opts.voice ?? 'off' })
    if (path === '/scenarios') return json([{ scenario_id: 'scenario-101', learner_title: 'Inbound call' }])
    if (path === '/sessions' && method === 'POST') return json({ ...session, status: 'created' })
    if (path === '/sessions/s-1/start') return json(session)
    if (path === '/sessions/s-1') return json(session)
    if (path === '/sessions/s-1/voice') return json({ url: 'wss://lk.test', room: 'r', identity: 'learner-1', token: 'tok' })
    if (path === '/sessions/s-1/hold') return json({ seq: 1 })
    if (path === '/sessions/s-1/end') {
      lk.order.push('end')
      return json({ ...session, status: 'ended' })
    }
    if (path === '/sessions/s-1/voice-stats') {
      const stats = typeof opts.stats === 'function' ? opts.stats() : opts.stats
      return stats ? json(stats) : json({ detail: 'no stats' }, 404)
    }
    if (path === '/sessions/s-1/transcript') return json(opts.transcript ?? [])
    if (path.endsWith('/found')) return json({ plans: [], providers: [], drugs: [], pharmacies: [] })
    if (path.endsWith('/record')) return json({ saved_seq: {} })
    if (path.endsWith('/applications') || path.endsWith('/evidence')) return json([])
    if (path.endsWith('/actor-qa')) return json({ flags: [] })
    if (path.endsWith('/debrief'))
      return json({
        session_id: 's-1', scenario_id: 'scenario-101', criteria: [], dimensions: [], earned: 0, possible: 0, score: null,
        complete: false, critical_findings: [], focus: [],
        judge: { status: 'off', name: null, prompt_version: null, graded_at: null, error: null, runs_left: 0 },
      })
    return json({ detail: `unexpected ${method} ${path}` }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const posted = (mock: ReturnType<typeof serve>, suffix: string) =>
  mock.mock.calls.filter(([url, init]) => init?.method === 'POST' && String(url).endsWith(suffix))

beforeAll(() => {
  Element.prototype.scrollIntoView = () => {}
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
  window.history.replaceState(null, '', '/')
  lk.rooms.length = 0
  lk.order.length = 0
  lk.startAudioError = null
  lk.micError = null
  lk.rpc = () => Promise.resolve('')
})

async function openCall(opts: Parameters<typeof serve>[0] = {}) {
  window.history.replaceState(null, '', '?session=s-1')
  const mock = serve({ voice: 'gemini-live:test', ...opts })
  render(<App />)
  await screen.findByRole('button', { name: 'Join call' })
  return mock
}

describe('starting a voice call', () => {
  it('offers the choice only when voice is on, and posts the channel', async () => {
    const mock = serve({ voice: 'gemini-live:test' })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inbound call (voice call)' }))
    await screen.findByText('Incoming call')
    expect(JSON.parse(String(posted(mock, '/sessions')[0]![1]?.body))).toEqual({ scenario_id: 'scenario-101', channel: 'voice' })
  })

  it('shows a single button per case when voice is off', async () => {
    const mock = serve({ voice: 'off' })
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Inbound call' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /voice call/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Inbound call' }))
    await screen.findByText('Incoming call')
    expect(JSON.parse(String(posted(mock, '/sessions')[0]![1]?.body))).toEqual({ scenario_id: 'scenario-101' })
  })
})

describe('a voice call', () => {
  it('has no typed composer, and Join connects and turns the mic on', async () => {
    const mock = await openCall()
    expect(screen.queryByLabelText('Your line')).toBeNull()
    expect(screen.getByRole('button', { name: 'Hold' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await waitFor(() => expect(room().localParticipant.setMicrophoneEnabled).toHaveBeenCalled())
    expect(posted(mock, '/sessions/s-1/voice')).toHaveLength(1)
    expect(room().connect).toHaveBeenCalledWith('wss://lk.test', 'tok')
    expect(room().localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    })
    await waitFor(() => expect(screen.getByText('Connected')).toBeTruthy())
  })

  it('shows Rejoin call after a reload of a call already joined in this tab', async () => {
    window.sessionStorage.setItem('voice-joined:s-1', '1')
    window.history.replaceState(null, '', '?session=s-1')
    serve({ voice: 'gemini-live:test' })
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Rejoin call' })).toBeTruthy()
  })

  it('wraps up the customer before ending, and still ends when the RPC fails', async () => {
    const mock = await openCall()
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await screen.findByText('Connected')
    fireEvent.click(screen.getByRole('button', { name: 'End call' }))
    await waitFor(() => expect(posted(mock, '/sessions/s-1/end')).toHaveLength(1))
    expect(room().localParticipant.performRpc).toHaveBeenCalledWith({
      destinationIdentity: 'agent-1',
      method: 'sim.wrap_up',
      payload: '',
      responseTimeout: 8000,
    })
    expect(lk.order).toEqual(['rpc', 'end'])
    await waitFor(() => expect(room().disconnect).toHaveBeenCalled())
  })

  it('still ends the call when the wrap-up RPC rejects', async () => {
    lk.rpc = () => Promise.reject(new Error('timeout'))
    const mock = await openCall()
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await screen.findByText('Connected')
    fireEvent.click(screen.getByRole('button', { name: 'End call' }))
    await waitFor(() => expect(posted(mock, '/sessions/s-1/end')).toHaveLength(1))
    expect(lk.order).toEqual(['rpc', 'end'])
  })

  it('turns the mic off on hold and back on after', async () => {
    await openCall()
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await screen.findByText('Connected')
    const mic = room().localParticipant.setMicrophoneEnabled
    mic.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Hold' }))
    await waitFor(() => expect(mic).toHaveBeenCalledWith(false, undefined))
    await screen.findByText('On hold')

    fireEvent.click(screen.getByRole('button', { name: 'Take off hold' }))
    await waitFor(() => expect(mic).toHaveBeenLastCalledWith(true, expect.objectContaining({ echoCancellation: true })))
  })

  it('tells the learner when the microphone is blocked', async () => {
    await openCall()
    const err = new Error('denied')
    err.name = 'NotAllowedError'
    lk.micError = err
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('microphone is blocked'))
    expect(screen.getByRole('button', { name: 'Join call' })).toBeTruthy()
  })

  it('stays on the call when the browser blocks playback, and offers to turn sound on', async () => {
    await openCall()
    const err = new Error('autoplay')
    err.name = 'NotAllowedError'
    lk.startAudioError = err
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await screen.findByText('Connected')
    expect(room().disconnect).not.toHaveBeenCalled()
    expect(screen.queryByText(/microphone is blocked/)).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain("blocked Ada's audio")

    lk.startAudioError = null
    fireEvent.click(screen.getByRole('button', { name: 'Turn on sound' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Turn on sound' })).toBeNull())
    expect(room().startAudio).toHaveBeenCalledTimes(2)
  })

  it('offers Rejoin when the customer leaves mid-call, and rejoining brings them back', async () => {
    const mock = await openCall()
    fireEvent.click(screen.getByRole('button', { name: 'Join call' }))
    await screen.findByText('Connected')
    act(() => room().handlers.get('participantDisconnected')!({ identity: 'agent-1', kind: 'agent' }))
    expect(screen.getByText('Ada left the call')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain("Ada isn't on the line anymore")
    const first = room()

    fireEvent.click(screen.getByRole('button', { name: 'Rejoin call' }))
    await waitFor(() => expect(posted(mock, '/sessions/s-1/voice')).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Rejoin call' })).toBeNull())
    expect(first.disconnect).toHaveBeenCalled()
    expect(room()).not.toBe(first)
  })

  it('orders voice lines by time, not id, and marks cut-off lines', async () => {
    await openCall({
      transcript: [
        line('u0003', 'customer', 9000, 'Later line.'),
        line('u0004', 'learner', 1000, 'Hello Ada.'),
        line('u0005', 'customer', 4000, 'I was about to say', true),
        line('u0006', 'customer', 5000, '', true),
      ],
    })
    await waitFor(() => expect(screen.getByText('Later line.')).toBeTruthy())
    const lines = [...document.querySelectorAll('.utt p')].map((p) => p.textContent)
    expect(lines).toEqual([
      'Hello Ada.',
      'I was about to say (cut off)',
      '(cut off before Ada said anything)',
      'Later line.',
    ])
  })
})

describe('voice review', () => {
  const ended = { ...SESSION, status: 'ended' as const }

  it('shows the stats and the recording', async () => {
    window.history.replaceState(null, '', '?session=s-1')
    serve({ voice: 'gemini-live:test', session: ended, stats: STATS })
    render(<App />)
    await screen.findByText('Voice call')
    expect(screen.getByText('1.2 s')).toBeTruthy()
    expect(screen.getByText('3.5 / 2.3 min')).toBeTruthy()
    expect(screen.getByText('$0.42')).toBeTruthy()
    expect(screen.getByText(STATS.cost_basis)).toBeTruthy()
    const audio = document.querySelector('audio')!
    expect(audio.getAttribute('src')).toBe('/api/sessions/s-1/recording')
    expect(audio.getAttribute('aria-details')).toBe('review-transcript')
    expect(document.getElementById('review-transcript')).toBeTruthy()
  })

  it('waits for the worker to finish its stats, and plays every recording part', async () => {
    window.history.replaceState(null, '', '?session=s-1')
    let asked = 0
    const late = { ...STATS, cost_usd_estimate: 0.0293, recording_parts: 2 }
    // The worker hasn't written anything yet, then writes its last figures.
    serve({ voice: 'gemini-live:test', session: ended, stats: () => (++asked === 1 ? null : late) })
    render(<App />)
    await screen.findByText('Voice call', undefined, { timeout: 4000 })
    expect(screen.getByText('$0.0293')).toBeTruthy()
    const audios = [...document.querySelectorAll('audio')]
    expect(audios.map((a) => a.getAttribute('src'))).toEqual([
      '/api/sessions/s-1/recording',
      '/api/sessions/s-1/recording?part=2',
    ])
    expect(audios.map((a) => a.getAttribute('aria-label'))).toEqual([
      'Call recording, part 1 of 2',
      'Call recording, part 2 of 2',
    ])
  })

  it('holds the recording back until the stats are final', async () => {
    window.history.replaceState(null, '', '?session=s-1')
    serve({ voice: 'gemini-live:test', session: ended, stats: { ...STATS, final: false } })
    render(<App />)
    await screen.findByText('Voice call')
    expect(document.querySelector('audio')).toBeNull()
  })

  it('retries a voice call as a voice call', async () => {
    window.history.replaceState(null, '', '?session=s-1')
    const mock = serve({ voice: 'gemini-live:test', session: ended, stats: STATS })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry this call' }))
    await waitFor(() => expect(posted(mock, '/sessions')).toHaveLength(1))
    expect(JSON.parse(String(posted(mock, '/sessions')[0]![1]?.body))).toEqual({
      scenario_id: 'scenario-101',
      retry_of: 's-1',
      channel: 'voice',
    })
  })

  it('hides the stats when there are none', async () => {
    window.history.replaceState(null, '', '?session=s-1')
    const mock = serve({ voice: 'gemini-live:test', session: ended, stats: null })
    render(<App />)
    await screen.findByText('Call debrief')
    await waitFor(() => expect(mock.mock.calls.some(([u]) => String(u).endsWith('/voice-stats'))).toBe(true))
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByText('Voice call')).toBeNull()
    expect(document.querySelector('audio')).toBeNull()
  })
})
