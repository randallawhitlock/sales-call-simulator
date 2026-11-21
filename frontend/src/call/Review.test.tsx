// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CriterionResult, Debrief, Session, Utterance } from '../api.ts'
import { CallContext, type CallContextValue } from './context.tsx'
import { Review } from './Review.tsx'

const utt = (utt_id: string, speaker: Utterance['speaker'], text: string): Utterance => ({
  utt_id,
  speaker,
  channel: 'text',
  text,
  heard_text: text,
  t_ms: 0,
})

const criterion = (over: Partial<CriterionResult>): CriterionResult => ({
  criterion_id: 'disc-doctors',
  dimension: 'discovery',
  title: 'Asked which doctors the customer must keep',
  method: 'judged',
  points: 3,
  earned: 3,
  status: 'pass',
  critical: false,
  detail: 'You asked about her doctors and which office she visits.',
  evidence: [{ utt_id: 'u0001', speaker: 'learner', quote: 'which doctors do you see', verified: true, problem: null }],
  event_seqs: [],
  rules: [],
  ...over,
})

const debrief = (over: Partial<Debrief> = {}): Debrief => ({
  session_id: 's-test',
  scenario_id: 'scenario-101',
  criteria: [
    criterion({}),
    criterion({
      criterion_id: 'ver-claims',
      dimension: 'verification',
      title: 'Coverage claims were backed by a lookup',
      method: 'hybrid',
      points: 2,
      earned: 0,
      status: 'fail',
      critical: true,
      detail: 'Said before any lookup showed it: Priya Raman is in network on Maple Choice PPO.',
      evidence: [{ utt_id: 'u0002', speaker: 'learner', quote: 'is in network', verified: true, problem: null }],
      rules: [
        {
          rule_id: 'R-ACCURATE',
          title: 'Accurate information',
          authority: 'regulatory',
          citation: '42 CFR 422.2262',
          status: 'unverified',
          verified_as_of: null,
        },
      ],
    }),
    criterion({
      criterion_id: 'com-plain',
      dimension: 'communication',
      title: 'Used plain language',
      status: 'review_required',
      earned: null,
      points: 4,
      detail: 'Needs review: the quote isn’t in that line.',
      evidence: [{ utt_id: 'u0001', speaker: 'learner', quote: 'does that make sense', verified: false, problem: "the quote isn't in that line" }],
    }),
  ],
  dimensions: [
    { dimension: 'discovery', weight: 15, earned: 3, possible: 3 },
    { dimension: 'verification', weight: 20, earned: 0, possible: 2 },
    { dimension: 'communication', weight: 10, earned: 0, possible: 0 },
  ],
  earned: 3,
  possible: 5,
  score: 60,
  complete: false,
  critical_findings: ['ver-claims'],
  focus: ['ver-claims'],
  judge: { status: 'done', name: 'claude:test', prompt_version: 'judge-1', graded_at: '2026-11-12T16:00:00Z', error: null, runs_left: 4 },
  ...over,
})

const TRANSCRIPT = [utt('u0001', 'learner', 'Hello, which doctors do you see?'), utt('u0002', 'learner', 'Dr. Raman is in network.')]

function Harness({ initial, onRetry }: { initial: Utterance[]; onRetry?: (s: Session) => void }) {
  const [transcript, setTranscript] = useState(initial)
  const value = {
    session: { session_id: 's-test', scenario_id: 'scenario-101' } as Session,
    base: '/sessions/s-test',
    live: false,
    transcript,
    setTranscript,
  } as unknown as CallContextValue
  return (
    <CallContext.Provider value={value}>
      <Review onRetry={onRetry} />
    </CallContext.Provider>
  )
}

type Route = (path: string, init?: RequestInit) => unknown

function serve(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, '')
    const method = init?.method ?? 'GET'
    const route = routes[`${method} ${path}`]
    if (route) return new Response(JSON.stringify(route(path, init)), { status: 200 })
    if (path.endsWith('/actor-qa')) return new Response(JSON.stringify({ flags: [] }), { status: 200 })
    if (path.endsWith('/transcript')) return new Response(JSON.stringify(TRANSCRIPT), { status: 200 })
    return new Response(JSON.stringify({ detail: `no route ${method} ${path}` }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const calls = (mock: ReturnType<typeof serve>, method: string, suffix: string) =>
  mock.mock.calls.filter(([url, init]) => (init?.method ?? 'GET') === method && String(url).endsWith(suffix))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Review', () => {
  it('shows the transcript the server graded, including a line the call screen missed', async () => {
    const mock = serve({ 'GET /sessions/s-test/debrief': () => debrief() })
    render(<Harness initial={[TRANSCRIPT[0]!]} />)

    await waitFor(() => expect(screen.getByText('Dr. Raman is in network.')).toBeTruthy())
    await new Promise((r) => setTimeout(r, 50))
    expect(calls(mock, 'GET', '/transcript')).toHaveLength(1)
  })

  it('shows the score, critical findings, evidence and rule cites', async () => {
    serve({ 'GET /sessions/s-test/debrief': () => debrief() })
    render(<Harness initial={TRANSCRIPT} />)

    await waitFor(() => expect(screen.getByText('60 / 100 so far')).toBeTruthy())
    expect(screen.getByText('incomplete')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Critical findings' })).toBeTruthy()
    expect(screen.getAllByText('Coverage claims were backed by a lookup').length).toBeGreaterThan(1)
    expect(screen.getByText('“which doctors do you see”', { exact: false })).toBeTruthy()
    expect(screen.getByText(/quote not found/)).toBeTruthy()
    expect(screen.getByText('needs review')).toBeTruthy()
    expect(screen.getByText(/R-ACCURATE Accurate information/)).toBeTruthy()
    expect(screen.getByText('unverified rule')).toBeTruthy()
  })

  it('runs the judge once when the call has not been graded yet', async () => {
    const mock = serve({
      'GET /sessions/s-test/debrief': () => debrief({ judge: { status: 'pending', name: 'claude:test', prompt_version: null, graded_at: null, error: null, runs_left: 5 } }),
      'POST /sessions/s-test/grade': () => debrief({ score: 100, complete: true, critical_findings: [], focus: [] }),
    })
    render(<Harness initial={TRANSCRIPT} />)

    await waitFor(() => expect(screen.getByText('100 / 100')).toBeTruthy())
    expect(calls(mock, 'POST', '/grade')).toHaveLength(1)
    expect(screen.queryByText('Critical findings')).toBeNull()
  })

  it('offers to grade again when the judge failed', async () => {
    const failed = debrief({ judge: { status: 'failed', name: 'claude:test', prompt_version: null, graded_at: null, error: 'overloaded', runs_left: 4 } })
    const mock = serve({
      'GET /sessions/s-test/debrief': () => debrief({ judge: { ...failed.judge, status: 'pending', error: null } }),
      'POST /sessions/s-test/grade': () => (calls(mock, 'POST', '/grade').length > 1 ? debrief() : failed),
    })
    render(<Harness initial={TRANSCRIPT} />)

    const again = await screen.findByRole('button', { name: 'Grade again' })
    expect(screen.getByText(/couldn't grade the conversation: overloaded/)).toBeTruthy()
    fireEvent.click(again)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Grade again' })).toBeNull())
    expect(calls(mock, 'POST', '/grade')).toHaveLength(2)
  })

  it('offers to grade again when the grading request itself fails', async () => {
    const pending = debrief({ judge: { status: 'pending', name: 'claude:test', prompt_version: null, graded_at: null, error: null, runs_left: 5 } })
    let attempts = 0
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/grade') && init?.method === 'POST') {
        attempts += 1
        if (attempts === 1) return new Response('Gateway Timeout', { status: 504 })
        return new Response(JSON.stringify(debrief({ score: 100, complete: true, critical_findings: [], focus: [] })), { status: 200 })
      }
      if (path.endsWith('/debrief')) return new Response(JSON.stringify(pending), { status: 200 })
      return new Response('[]', { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Harness initial={TRANSCRIPT} />)

    const again = await screen.findByRole('button', { name: 'Grade again' })
    expect(screen.getByText(/couldn't grade the conversation: HTTP 504/)).toBeTruthy()
    expect(screen.queryByText(/is grading the conversation/)).toBeNull()
    fireEvent.click(again)
    await waitFor(() => expect(screen.getByText('100 / 100')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Grade again' })).toBeNull()
    expect(attempts).toBe(2)
  })

  it('stops offering when the call has no grading runs left', async () => {
    serve({
      'GET /sessions/s-test/debrief': () =>
        debrief({ judge: { status: 'failed', name: 'claude:test', prompt_version: null, graded_at: null, error: 'overloaded', runs_left: 0 } }),
    })
    render(<Harness initial={TRANSCRIPT} />)

    await waitFor(() => expect(screen.getByText(/no grading runs left/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Grade again' })).toBeNull()
  })

  it('says so when no judge is configured', async () => {
    serve({ 'GET /sessions/s-test/debrief': () => debrief({ judge: { status: 'off', name: null, prompt_version: null, graded_at: null, error: null, runs_left: 5 } }) })
    render(<Harness initial={TRANSCRIPT} />)

    await waitFor(() => expect(screen.getByText(/No AI judge is configured/)).toBeTruthy())
  })

  it('starts a targeted retry of the same case', async () => {
    const retrySession = { session_id: 's-retry', scenario_id: 'scenario-101', status: 'created' } as Session
    const mock = serve({
      'GET /sessions/s-test/debrief': () => debrief(),
      'POST /sessions': () => retrySession,
    })
    const onRetry = vi.fn()
    render(<Harness initial={TRANSCRIPT} onRetry={onRetry} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Retry this call' }))
    await waitFor(() => expect(onRetry).toHaveBeenCalledWith(retrySession))
    const [, init] = calls(mock, 'POST', '/sessions')[0]!
    expect(JSON.parse(String(init?.body))).toEqual({ scenario_id: 'scenario-101', retry_of: 's-test' })
    expect(screen.getByText('Work on these next time:')).toBeTruthy()
  })

  it('highlights the cited line in the transcript', async () => {
    serve({ 'GET /sessions/s-test/debrief': () => debrief() })
    const { container } = render(<Harness initial={TRANSCRIPT} />)

    fireEvent.click((await screen.findAllByRole('button', { name: 'u0002' }))[0]!)
    expect(container.querySelector('#line-u0002')?.className).toContain('picked')
  })

  it('lists what the AI customer made up, marked as not scored', async () => {
    const flag = { utt_id: 'u0004', kind: 'drug', said: 'lisinopril', text: 'They added lisinopril.' }
    serve({
      'GET /sessions/s-test/debrief': () => debrief(),
      'GET /sessions/s-test/actor-qa': () => ({ session_id: 's-test', actor: 'scripted', prompt_version: 'v', flags: [flag] }),
    })
    render(<Harness initial={[]} />)

    await waitFor(() => expect(screen.getByText('AI customer check')).toBeTruthy())
    expect(screen.getByText(/Not part of your score/)).toBeTruthy()
    expect(screen.getByText('“lisinopril”', { exact: false })).toBeTruthy()
  })
})
