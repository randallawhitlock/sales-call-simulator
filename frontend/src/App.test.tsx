// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { CustomerRecord, Session, Utterance } from './api.ts'
import { App } from './App.tsx'

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
}

const EMPTY_RECORD: CustomerRecord = {
  doctors: null,
  drugs: null,
  pharmacy: null,
  coverage: null,
  contact: null,
  notes: null,
  disposition: null,
  saved_seq: {},
}

const utt = (utt_id: string, speaker: Utterance['speaker'], text: string): Utterance => ({
  utt_id,
  speaker,
  channel: 'text',
  text,
  heard_text: text,
  t_ms: 0,
})

/** A fake API. ``session`` is read on every call, so a test can change it mid-flight. */
function serve(state: { session: Session | null; transcript: Utterance[]; down?: boolean }) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, '').split('?')[0]!
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    if (state.down && path.startsWith('/sessions/')) return json({ detail: 'server is restarting' }, 503)
    if (path.endsWith('/turns')) {
      const sent = JSON.parse(String(init?.body)) as { text: string }
      return json({ learner: utt('u0001', 'learner', sent.text), customer: utt('u0002', 'customer', 'Sorry, yes.') })
    }
    if (path === '/health') return json({ status: 'ok', version: 'test', actor: 'scripted', catalog_version: 'v' })
    if (path === '/scenarios') return json([{ scenario_id: 'scenario-101', learner_title: 'Inbound call' }])
    if (path === '/sessions' && state.session) return json({ ...state.session, status: 'created' })
    if (path === '/sessions/s-1/start' && state.session) return json({ ...state.session, status: 'active' })
    if (path === '/sessions/s-1') return state.session ? json(state.session) : json({ detail: 'session not found' }, 404)
    if (path.endsWith('/transcript')) return json(state.transcript)
    if (path.endsWith('/found')) return json({ plans: [], providers: [], drugs: [], pharmacies: [] })
    if (path.endsWith('/record')) return json(EMPTY_RECORD)
    if (path.endsWith('/applications') || path.endsWith('/evidence') || path.endsWith('/documents')) return json([])
    return json({ detail: `unexpected ${path}` }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function openAt(search: string): void {
  window.history.replaceState(null, '', `/${search}`)
}

beforeAll(() => {
  // jsdom doesn't lay out, so it has no scrollIntoView; the transcript calls it on every line.
  Element.prototype.scrollIntoView = () => {}
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
  openAt('')
})

describe('App rejoin', () => {
  it('reopens the call in the URL after a reload, on hold if the server says so', async () => {
    openAt('?session=s-1')
    const fetchMock = serve({ session: { ...SESSION, on_hold: true }, transcript: [utt('u0001', 'learner', 'Hi there')] })
    render(<App />)

    await waitFor(() => expect(screen.getByText('Hi there')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Take off hold' })).toBeTruthy()
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/sessions/s-1/found'))).toBe(true)
  })

  it('waits for a reply that was still being generated when the page reloaded', async () => {
    openAt('?session=s-1')
    const state = { session: { ...SESSION, turn_pending: true } as Session, transcript: [utt('u0001', 'learner', 'Which pharmacy?')] }
    serve(state)
    render(<App />)

    await waitFor(() => expect(screen.getByText('Ada is answering…')).toBeTruthy())
    state.session = { ...SESSION, turn_pending: false }
    state.transcript = [...state.transcript, utt('u0002', 'customer', 'Alder Family Pharmacy.')]
    await waitFor(() => expect(screen.getByText('Alder Family Pharmacy.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.queryByText('Ada is answering…')).toBeNull()
  })

  it('falls back to the case picker when the call in the URL is gone', async () => {
    openAt('?session=s-1')
    serve({ session: null, transcript: [] })
    render(<App />)

    await waitFor(() => expect(screen.getByText(/Couldn't reopen that call/)).toBeTruthy())
    expect(screen.getByText('Choose a call')).toBeTruthy()
    expect(window.location.search).toBe('')
  })

  it('forgets the incoming call it reopened once that call is answered and left', async () => {
    openAt('?session=s-1')
    serve({ session: { ...SESSION, status: 'created' }, transcript: [] })
    render(<App />)

    await waitFor(() => expect(screen.getByText('Incoming call')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Answer the call' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Leave' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }))

    await waitFor(() => expect(screen.getByText('Choose a call')).toBeTruthy())
    expect(screen.queryByText('Incoming call')).toBeNull()
    expect(window.location.search).toBe('')
  })

  it('drops the reopen error once another call has started', async () => {
    openAt('?session=s-1')
    const state = { session: null as Session | null, transcript: [] }
    serve(state)
    render(<App />)
    await waitFor(() => expect(screen.getByText(/Couldn't reopen that call/)).toBeTruthy())

    state.session = SESSION
    fireEvent.click(await screen.findByRole('button', { name: 'Inbound call' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Answer the call' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Leave' })).toBeTruthy())
    expect(screen.queryByText(/Couldn't reopen that call/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }))

    await waitFor(() => expect(screen.getByText('Choose a call')).toBeTruthy())
    expect(screen.queryByText(/Couldn't reopen that call/)).toBeNull()
  })

  it('keeps the call in the URL when reopening fails for now, and tries again on request', async () => {
    openAt('?session=s-1')
    const state = { session: SESSION as Session | null, transcript: [utt('u0001', 'learner', 'Hi there')], down: true }
    serve(state)
    render(<App />)

    await waitFor(() => expect(screen.getByText(/Couldn't reopen that call: server is restarting/)).toBeTruthy())
    expect(window.location.search).toBe('?session=s-1')

    state.down = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Hi there')).toBeTruthy())
    expect(window.location.search).toBe('?session=s-1')
  })

  it('treats an empty session in the URL as no call', async () => {
    openAt('?session=')
    const fetchMock = serve({ session: SESSION, transcript: [] })
    render(<App />)

    await waitFor(() => expect(screen.getByText('Choose a call')).toBeTruthy())
    expect(screen.queryByText('Rejoining the call…')).toBeNull()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/sessions'))).toBe(false)
  })

  it('never puts a session value that is not an id into an API path', async () => {
    openAt('?session=../reference')
    const fetchMock = serve({ session: SESSION, transcript: [] })
    render(<App />)

    await waitFor(() => expect(screen.getByText(/Couldn't reopen that call/)).toBeTruthy())
    expect(screen.getByText('Choose a call')).toBeTruthy()
    expect(window.location.search).toBe('')
    expect(fetchMock.mock.calls.some(([url]) => /reference|sessions/.test(String(url)))).toBe(false)
  })

  it('offers the line again when the reply pending at reload never came', async () => {
    openAt('?session=s-1')
    window.sessionStorage.setItem('sent-turn:s-1', JSON.stringify({ id: 'turn-abc', text: 'Which pharmacy?' }))
    const state = { session: { ...SESSION, turn_pending: true } as Session, transcript: [utt('u0001', 'learner', 'Which pharmacy?')] }
    const fetchMock = serve(state)
    render(<App />)

    await waitFor(() => expect(screen.getByText('Ada is answering…')).toBeTruthy())
    state.session = { ...SESSION, turn_pending: false }
    await waitFor(() => expect(screen.getByText(/Ada didn't answer your last line/)).toBeTruthy(), { timeout: 3000 })
    expect(screen.queryByText('Ada is answering…')).toBeNull()
    expect((screen.getByLabelText('Your line') as HTMLTextAreaElement).disabled).toBe(false)

    // Retry reuses the original id, so the server does not store the learner's line twice.
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('Sorry, yes.')).toBeTruthy())
    const turn = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/turns'))!
    expect(JSON.parse(String(turn[1]?.body))).toEqual({ client_utt_id: 'turn-abc', text: 'Which pharmacy?' })
    expect(screen.queryByText(/didn't answer/)).toBeNull()
  })

  it('says so when the pending reply never came and the sent line is not known', async () => {
    openAt('?session=s-1')
    const state = { session: { ...SESSION, turn_pending: true } as Session, transcript: [utt('u0001', 'learner', 'Which pharmacy?')] }
    serve(state)
    render(<App />)

    await waitFor(() => expect(screen.getByText('Ada is answering…')).toBeTruthy())
    state.session = { ...SESSION, turn_pending: false }
    await waitFor(() => expect(screen.getByText(/Ada didn't answer your last line. It was saved/)).toBeTruthy(), {
      timeout: 3000,
    })
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect((screen.getByLabelText('Your line') as HTMLTextAreaElement).disabled).toBe(false)
  })

  it('stops waiting on a pending reply when the server keeps failing, and hands the call back', async () => {
    vi.useFakeTimers()
    openAt('?session=s-1')
    const state = { session: { ...SESSION, turn_pending: true } as Session, transcript: [], down: false }
    const fetchMock = serve(state)
    render(<App />)
    await act(() => vi.advanceTimersByTimeAsync(10))
    expect(screen.getByText('Ada is answering…')).toBeTruthy()

    state.down = true
    const polls = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/sessions/s-1')).length
    const before = polls()
    for (let i = 0; i < 120; i++) await act(() => vi.advanceTimersByTimeAsync(1000))

    expect(screen.getByText(/Couldn't check on Ada's reply: server is restarting/)).toBeTruthy()
    expect(screen.queryByText('Ada is answering…')).toBeNull()
    expect((screen.getByLabelText('Your line') as HTMLTextAreaElement).disabled).toBe(false)
    expect(polls() - before).toBe(5)
  })

  it('rings another attempt with what the last debrief said to work on', async () => {
    openAt('?session=s-2')
    const retry: Session = {
      ...SESSION,
      session_id: 's-2',
      status: 'created',
      retry_of: 's-1',
      retry_focus: ['Asked which pharmacy the customer uses'],
    }
    const fetchMock = vi.fn(async (input: string) => {
      const path = String(input).replace(/^\/api/, '')
      const body = path === '/sessions/s-2' ? retry : path === '/scenarios' ? [] : { status: 'ok', actor: 'scripted' }
      return new Response(JSON.stringify(body), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    await waitFor(() => expect(screen.getByText('Incoming call')).toBeTruthy())
    expect(screen.getByText('Another attempt at this call.')).toBeTruthy()
    expect(screen.getByText('Asked which pharmacy the customer uses')).toBeTruthy()
    expect(new URLSearchParams(window.location.search).get('session')).toBe('s-2')
  })
})
