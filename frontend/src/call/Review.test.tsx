// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

const criterion = (over: Partial<CriterionResult> & Pick<CriterionResult, 'criterion_id'>): CriterionResult => ({
  dimension: 'discovery',
  title: over.criterion_id,
  points: 3,
  earned: 0,
  method: 'judged',
  status: 'ungraded',
  critical: false,
  detail: '',
  rationale: null,
  utt_ids: [],
  event_seqs: [],
  quotes: [],
  claims: [],
  rules: [],
  ...over,
})

const TRANSCRIPT = [
  utt('u0001', 'learner', 'Which pharmacy do you use?'),
  utt('u0002', 'customer', 'Alder Family Pharmacy.'),
]

function debrief(over: Partial<Debrief> = {}): Debrief {
  return {
    session_id: 's-test',
    scenario_id: 'scenario-101',
    versions: { scenario_version: 2, catalog_version: '2027-v1', ruleset_version: 'mvp-2027.1', rubric: 'mvp v2' },
    state: 'complete',
    score: { earned: 5, possible: 8, percent: 63 },
    dimensions: [
      { dimension: 'discovery', weight: 15, earned: 2, possible: 2, percent: 100 },
      { dimension: 'verification', weight: 20, earned: 3, possible: 6, percent: 50 },
    ],
    criteria: [
      criterion({
        criterion_id: 'disc-pharmacy',
        title: 'Asked which pharmacy the customer uses',
        points: 2,
        earned: 2,
        status: 'pass',
        rationale: 'You asked directly.',
        utt_ids: ['u0001'],
        quotes: [{ utt_id: 'u0001', quote: 'Which pharmacy do you use', verified: true }],
        rules: [
          {
            rule_id: 'R-NEEDS',
            title: 'Needs discussion',
            authority: 'regulatory',
            citation: '42 CFR 422.2274(c)(12)',
            status: 'approved',
            verified_as_of: '2026-11-07',
          },
        ],
      }),
      criterion({
        criterion_id: 'ver-pharmacy',
        dimension: 'verification',
        title: 'Pharmacy network status checked on the outcome plan',
        method: 'deterministic',
        points: 3,
        earned: 3,
        status: 'pass',
        detail: 'Alder Family Pharmacy was checked on Maple Choice PPO.',
        event_seqs: [4],
      }),
      criterion({
        criterion_id: 'ver-drugs',
        dimension: 'verification',
        title: 'Every prescription checked on the outcome plan at the right strength',
        method: 'deterministic',
        points: 3,
        status: 'fail',
        detail: 'Not checked on Maple Choice PPO: apixaban 5 mg.',
      }),
    ],
    critical: [],
    judge: {
      state: 'graded',
      judge: 'claude:claude-opus-5-5',
      served_model: 'claude-opus-5-5',
      prompt_version: 'judge-v1',
      attempt: 1,
      graded_at: '2026-11-12T16:00:00Z',
      error: null,
    },
    outcome: { kind: 'enroll', plan_name: 'Maple Choice PPO' },
    timeline: [
      { seq: 1, t_ms: 0, type: 'call_started', utt_id: null, summary: 'Call started.' },
      { seq: 2, t_ms: 1000, type: 'utterance', utt_id: 'u0001', summary: '' },
      { seq: 3, t_ms: 2000, type: 'utterance', utt_id: 'u0002', summary: '' },
      {
        seq: 4,
        t_ms: 3000,
        type: 'lookup',
        utt_id: null,
        summary: 'Checked Alder Family Pharmacy on Maple Choice PPO: in network, preferred.',
      },
    ],
    case: {
      title: 'Must-keep specialist',
      outcomes: [{ kind: 'enroll', plans: ['Maple Choice PPO'], acceptable: true, rationale: 'Keeps Dr. Raman.' }],
      traps: [{ description: 'Same doctor, other office.', rationale: 'Not a match.' }],
    },
    retry_focus: [
      { criterion_id: 'ver-drugs', title: 'Every prescription checked', why: 'Not checked: apixaban 5 mg.' },
    ],
    ...over,
  }
}

interface Api {
  debrief: Debrief
  /** What POST /debrief/judge returns. */
  judged?: Debrief
  transcript?: Utterance[]
  flags?: unknown[]
}

function serve(api: Api) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    if (url.endsWith('/debrief/judge')) return json(api.judged ?? api.debrief)
    if (url.endsWith('/debrief')) return json(api.debrief)
    if (url.endsWith('/transcript')) return json(api.transcript ?? TRANSCRIPT)
    if (url.endsWith('/actor-qa')) return json({ session_id: 's-test', actor: 'scripted', prompt_version: 'v', flags: api.flags ?? [] })
    if (url.endsWith('/sessions') && init?.method === 'POST') {
      return json({ session_id: 's-retry', status: 'created', ...(JSON.parse(String(init.body)) as object) }, 201)
    }
    return json({ detail: `unexpected ${url}` }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function Harness({ initial = [], onRetry = () => {} }: { initial?: Utterance[]; onRetry?: (s: Session) => void }) {
  const [transcript, setTranscript] = useState(initial)
  const value = {
    session: { session_id: 's-test' } as Session,
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

const posts = (fetchMock: ReturnType<typeof serve>, suffix: string) =>
  fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith(suffix) && init?.method === 'POST')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Review', () => {
  it('shows the score, what was scored and where each requirement comes from', async () => {
    serve({ debrief: debrief() })
    render(<Harness />)

    await waitFor(() => expect(screen.getByText('Grading complete')).toBeTruthy())
    const head = screen.getByRole('banner')
    expect(head.textContent).toContain('5 of 8 points')
    expect(head.textContent).toContain('63%')
    expect(head.textContent).toContain('Enrolled the customer in Maple Choice PPO')
    expect(head.textContent).toContain('Conversation judged by claude-opus-5-5')
    expect(screen.getByText('Provider, drug and pharmacy verification')).toBeTruthy()
    expect(screen.getByText('3 of 6 points')).toBeTruthy()

    const asked = screen.getByText('Asked which pharmacy the customer uses').closest('li')!
    expect(asked.textContent).toContain('Pass')
    expect(asked.textContent).toContain('2/2')
    expect(asked.textContent).toContain('Judge: You asked directly.')
    expect(asked.textContent).toContain('Regulation: Needs discussion (42 CFR 422.2274(c)(12))')
    const failed = screen.getByText('Every prescription checked on the outcome plan at the right strength').closest('li')!
    expect(failed.textContent).toContain('Fail')
    expect(failed.textContent).toContain('0/3')
    // No rule behind it, so it is labeled as the training design it is.
    expect(failed.textContent).toContain('Training objective')
    expect(screen.queryByLabelText('Critical findings')).toBeNull()
  })

  it('shows the transcript the server graded, including a line the call screen missed', async () => {
    const fetchMock = serve({ debrief: debrief() })
    render(<Harness initial={[TRANSCRIPT[0]!]} />)

    await waitFor(() => expect(screen.getByText('Alder Family Pharmacy.')).toBeTruthy())
    await new Promise((r) => setTimeout(r, 50))
    const transcriptCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/transcript'))
    expect(transcriptCalls).toHaveLength(1)
  })

  it('puts what was said and what was done on one record, and evidence links point into it', async () => {
    serve({ debrief: debrief() })
    render(<Harness />)

    const record = await screen.findByLabelText('Call record')
    await waitFor(() => expect(within(record).getByText('Which pharmacy do you use?')).toBeTruthy())
    const rows = [...record.querySelectorAll('li')].map((li) => li.id)
    expect(rows).toEqual(['event-1', 'utt-u0001', 'utt-u0002', 'event-4'])
    expect(document.getElementById('event-4')!.textContent).toContain('Checked Alder Family Pharmacy')

    expect(record.querySelector('.shown')).toBeNull()
    fireEvent.click(within(screen.getByLabelText('Scorecard')).getByRole('button', { name: 'event 4' }))
    expect(document.getElementById('event-4')!.className).toContain('shown')
    fireEvent.click(within(screen.getByLabelText('Scorecard')).getAllByRole('button', { name: 'u0001' })[0]!)
    expect(document.getElementById('utt-u0001')!.className).toContain('shown')
    expect(document.getElementById('event-4')!.className).not.toContain('shown')
  })

  it('has the judge grade a call it has not graded yet, and shows the rule checks meanwhile', async () => {
    const waiting = debrief({
      state: 'incomplete',
      judge: { ...debrief().judge, state: 'not_run', served_model: null, attempt: null },
      criteria: debrief().criteria.map((c) =>
        c.criterion_id === 'disc-pharmacy'
          ? { ...c, status: 'ungraded', earned: 0, rationale: null, quotes: [], detail: 'Waiting for the judge.' }
          : c,
      ),
    })
    const fetchMock = serve({ debrief: waiting, judged: debrief() })
    render(<Harness />)

    await waitFor(() => expect(screen.getByText('Grading complete')).toBeTruthy())
    expect(posts(fetchMock, '/debrief/judge')).toHaveLength(1)
    expect(JSON.parse(String(posts(fetchMock, '/debrief/judge')[0]![1]!.body))).toEqual({ regrade: false })
    expect(screen.getByText('Judge:', { exact: false })).toBeTruthy()
  })

  it('says plainly when there is no judge, and never asks for one', async () => {
    const off = debrief({
      state: 'incomplete',
      judge: { ...debrief().judge, state: 'off', judge: null, served_model: null, attempt: null },
      criteria: debrief().criteria.map((c) =>
        c.criterion_id === 'disc-pharmacy' ? { ...c, status: 'ungraded', earned: 0, rationale: null, quotes: [] } : c,
      ),
    })
    const fetchMock = serve({ debrief: off })
    render(<Harness />)

    await waitFor(() => expect(screen.getByText('Incomplete')).toBeTruthy())
    expect(screen.getByRole('banner').textContent).toContain('5 of 8 points graded so far')
    expect(screen.getByRole('banner').textContent).toContain('2 points are not graded yet')
    expect(screen.getByRole('banner').textContent).toContain('No judge is configured')
    const ungraded = screen.getByText('Asked which pharmacy the customer uses').closest('li')!
    expect(ungraded.textContent).toContain('Not graded')
    expect(ungraded.textContent).toContain('–/2')
    await new Promise((r) => setTimeout(r, 30))
    expect(posts(fetchMock, '/debrief/judge')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /judge|Regrade/ })).toBeNull()
  })

  it('offers to ask again when the judge did not answer', async () => {
    const failed = debrief({
      state: 'incomplete',
      judge: { ...debrief().judge, state: 'failed', served_model: null, attempt: null, error: 'rate limited' },
      criteria: debrief().criteria.map((c) =>
        c.criterion_id === 'disc-pharmacy' ? { ...c, status: 'ungraded', earned: 0, rationale: null, quotes: [] } : c,
      ),
    })
    const fetchMock = serve({ debrief: failed, judged: debrief() })
    render(<Harness />)

    await waitFor(() => expect(screen.getByRole('banner').textContent).toContain('The judge did not answer: rate limited'))
    expect(posts(fetchMock, '/debrief/judge')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Ask the judge again' }))
    await waitFor(() => expect(screen.getByText('Grading complete')).toBeTruthy())
    expect(posts(fetchMock, '/debrief/judge')).toHaveLength(1)
  })

  it('hands the grading back when the request itself fails, instead of waiting forever', async () => {
    const waiting = debrief({
      state: 'incomplete',
      judge: { ...debrief().judge, state: 'not_run', served_model: null, attempt: null },
      criteria: debrief().criteria.map((c) =>
        c.criterion_id === 'disc-pharmacy' ? { ...c, status: 'ungraded', earned: 0, rationale: null, quotes: [] } : c,
      ),
    })
    let broken = true
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input)
      if (url.endsWith('/debrief/judge')) {
        // Not JSON: what a crashed server or a proxy in front of it sends.
        return broken
          ? new Response('Internal Server Error', { status: 500 })
          : new Response(JSON.stringify(debrief()), { status: 200 })
      }
      const body = url.endsWith('/debrief') ? waiting : url.endsWith('/transcript') ? TRANSCRIPT : { flags: [] }
      return new Response(JSON.stringify(body), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Harness />)

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('HTTP 500'))
    const head = screen.getByRole('banner')
    expect(head.textContent).toContain('The judge has not graded this call yet.')
    expect(head.textContent).not.toContain('The judge is reading the call')
    broken = false
    fireEvent.click(screen.getByRole('button', { name: 'Ask the judge' }))
    await waitFor(() => expect(screen.getByText('Grading complete')).toBeTruthy())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('regrades only when asked, after a confirmation', async () => {
    const fetchMock = serve({ debrief: debrief() })
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    render(<Harness />)

    fireEvent.click(await screen.findByRole('button', { name: 'Regrade' }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(posts(fetchMock, '/debrief/judge')).toHaveLength(0)
    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Regrade' }))
    await waitFor(() => expect(posts(fetchMock, '/debrief/judge')).toHaveLength(1))
    expect(JSON.parse(String(posts(fetchMock, '/debrief/judge')[0]![1]!.body))).toEqual({ regrade: true })
  })

  it('shows a ruling held for review as out of the total, with the quote that was not found', async () => {
    const held = debrief({
      state: 'needs_review',
      criteria: debrief().criteria.map((c) =>
        c.criterion_id === 'disc-pharmacy'
          ? {
              ...c,
              status: 'review_required',
              earned: 0,
              utt_ids: [],
              detail: 'The judge ruled pass, but a quote it cited is not in u0001. Held for review.',
              quotes: [{ utt_id: 'u0001', quote: 'Where do you fill your prescriptions', verified: false }],
            }
          : c,
      ),
    })
    serve({ debrief: held })
    render(<Harness />)

    await waitFor(() => expect(screen.getByText('Needs review')).toBeTruthy())
    expect(screen.getByRole('banner').textContent).toContain('1 item is held for review (2 points)')
    const row = screen.getByText('Asked which pharmacy the customer uses').closest('li')!
    expect(row.textContent).toContain('Held for review')
    expect(row.textContent).toContain('–/2')
    expect(row.textContent).toContain('not found where cited')
    // A quote that isn't in the line is not a link to it.
    expect(within(row).queryByRole('button', { name: 'u0001' })).toBeNull()
  })

  it('leads with critical findings and lists each coverage claim with its lookup', async () => {
    const claims = criterion({
      criterion_id: 'ver-claims',
      dimension: 'verification',
      title: 'Every coverage claim was backed by a lookup made before the claim',
      method: 'hybrid',
      points: 2,
      status: 'fail',
      critical: true,
      detail: '1 of 2 coverage claims had no lookup behind them.',
      utt_ids: ['u0001'],
      claims: [
        {
          utt_id: 'u0001',
          quote: 'is in network',
          label: 'Alder Family Pharmacy is in network on Maple Choice PPO',
          status: 'backed',
          detail: 'A lookup made before the claim showed it.',
          event_seqs: [4],
        },
        {
          utt_id: 'u0001',
          quote: 'apixaban is covered',
          label: 'apixaban 5 mg is covered on Maple Choice PPO',
          status: 'unbacked',
          detail: 'No lookup made before u0001 showed this.',
          event_seqs: [],
        },
      ],
    })
    serve({ debrief: debrief({ critical: ['ver-claims'], criteria: [...debrief().criteria, claims] }) })
    render(<Harness />)

    const critical = await screen.findByLabelText('Critical findings')
    expect(critical.textContent).toContain('Failed: Every coverage claim was backed by a lookup made before the claim.')
    expect(critical.textContent).toContain('1 of 2 coverage claims had no lookup behind them.')
    const row = within(screen.getByLabelText('Scorecard')).getByText(claims.title).closest('li')!
    expect(row.textContent).toContain('Critical')
    expect(row.textContent).toContain('No lookup')
    expect(row.textContent).toContain('apixaban 5 mg is covered on Maple Choice PPO')
    expect(within(row).getAllByRole('button', { name: 'event 4' })).toHaveLength(1)
  })

  it('ends with what to work on, and starts another attempt at the same case', async () => {
    const fetchMock = serve({ debrief: debrief() })
    const onRetry = vi.fn()
    render(<Harness onRetry={onRetry} />)

    const retry = await screen.findByLabelText('Try again')
    expect(retry.textContent).toContain('Every prescription checked.')
    expect(retry.textContent).toContain('Not checked: apixaban 5 mg.')
    fireEvent.click(within(retry).getByRole('button', { name: 'Retry this call' }))
    await waitFor(() => expect(onRetry).toHaveBeenCalledOnce())
    expect(JSON.parse(String(posts(fetchMock, '/sessions')[0]![1]!.body))).toEqual({
      scenario_id: 'scenario-101',
      retry_of: 's-test',
    })
    expect(onRetry.mock.calls[0]![0]).toMatchObject({ session_id: 's-retry', retry_of: 's-test' })
  })

  it('shows what the case was testing', async () => {
    serve({ debrief: debrief() })
    render(<Harness />)

    const notes = (await screen.findByText('What this case was testing: Must-keep specialist')).closest('details')!
    expect(notes.textContent).toContain('Acceptable')
    expect(notes.textContent).toContain('Keeps Dr. Raman.')
    expect(notes.textContent).toContain('Same doctor, other office.')
  })

  it('lists what the AI customer made up, marked as not scored', async () => {
    const flag = { utt_id: 'u0002', kind: 'drug', said: 'lisinopril', text: 'They added lisinopril.' }
    serve({ debrief: debrief(), flags: [flag] })
    render(<Harness />)

    await waitFor(() => expect(screen.getByText('AI customer check')).toBeTruthy())
    expect(screen.getByText(/Not part of your score/)).toBeTruthy()
    expect(screen.getByText('“lisinopril”', { exact: false })).toBeTruthy()
  })

  it('says so when the debrief cannot be loaded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'this session was pinned to content that is not loaded' }), { status: 409 })),
    )
    render(<Harness />)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('pinned to content that is not loaded'))
  })
})
