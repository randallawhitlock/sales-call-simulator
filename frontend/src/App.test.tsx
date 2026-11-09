// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
function serve(state: { session: Session | null; transcript: Utterance[] }) {
  const fetchMock = vi.fn(async (input: string) => {
    const path = String(input).replace(/^\/api/, '').split('?')[0]!
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    if (path === '/health') return json({ status: 'ok', version: 'test', actor: 'scripted', catalog_version: 'v' })
    if (path === '/scenarios') return json([{ scenario_id: 'scenario-101', learner_title: 'Inbound call' }])
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
  vi.unstubAllGlobals()
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
})
