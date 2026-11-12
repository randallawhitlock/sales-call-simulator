// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Session, Utterance } from '../api.ts'
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

function Harness({ initial }: { initial: Utterance[] }) {
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
      <Review />
    </CallContext.Provider>
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Review', () => {
  it('shows the transcript the server graded, including a line the call screen missed', async () => {
    const graded = [utt('u0001', 'learner', 'Hello?'), utt('u0002', 'learner', 'Are you still there?')]
    const fetchMock = vi.fn(async (url: string) => {
      const body = url.endsWith('/transcript') ? graded : { results: [] }
      return new Response(JSON.stringify(body), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Harness initial={[graded[0]!]} />)

    await waitFor(() => expect(screen.getByText('Are you still there?')).toBeTruthy())
    await new Promise((r) => setTimeout(r, 50))
    const transcriptCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/transcript'))
    expect(transcriptCalls).toHaveLength(1)
  })

  it('lists what the AI customer made up, marked as not scored', async () => {
    const flag = { utt_id: 'u0004', kind: 'drug', said: 'lisinopril', text: 'They added lisinopril.' }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const body = url.endsWith('/actor-qa')
          ? { session_id: 's-test', actor: 'scripted', prompt_version: 'v', flags: [flag] }
          : url.endsWith('/transcript')
            ? []
            : { results: [] }
        return new Response(JSON.stringify(body), { status: 200 })
      }),
    )
    render(<Harness initial={[]} />)

    await waitFor(() => expect(screen.getByText('AI customer check')).toBeTruthy())
    expect(screen.getByText(/Not part of your score/)).toBeTruthy()
    expect(screen.getByText('“lisinopril”', { exact: false })).toBeTruthy()
  })
})
