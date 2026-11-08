// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CustomerRecord, Session } from '../api.ts'
import { CallContext, type CallContextValue } from './context.tsx'
import { PharmacySection } from './RecordPane.tsx'

const RECORD: CustomerRecord = {
  doctors: null,
  drugs: null,
  pharmacy: { pharmacy_id: 'alder-family-rx', name_reported: 'Alder Family', source_utt_id: 'u0004' },
  coverage: null,
  contact: null,
  notes: null,
  disposition: null,
  saved_seq: { pharmacy: 12 },
}

function context(record: CustomerRecord): CallContextValue {
  return {
    session: { session_id: 's-test' } as Session,
    base: '/sessions/s-test',
    live: true,
    transcript: [],
    setTranscript: () => {},
    record,
    refreshRecord: async () => {},
    applications: [],
    refreshApplications: async () => {},
    evidence: [],
    pin: async () => {},
    pinned: new Set(),
    source: null,
    setSource: () => {},
    seen: {
      plans: [],
      providers: [],
      drugs: [],
      pharmacies: [
        {
          pharmacy_id: 'alder-family-rx',
          name: 'Alder Family Pharmacy',
          mail_order: false,
          location: { location_id: 'x', name: 'Northside Plaza', address: '402 Birch Avenue', city: 'Northside', zip: '00041' },
        },
      ],
    },
    remember: () => {},
    notify: () => {},
  }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('PharmacySection', () => {
  it('keeps the saved citation when the section is edited and saved again', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    render(
      <CallContext.Provider value={context(RECORD)}>
        <PharmacySection />
      </CallContext.Provider>,
    )

    expect(screen.getByRole('button', { name: /Cited u0004/ })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('As the customer said it'), { target: { value: 'Alder Family Rx' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/sessions/s-test/record/pharmacy')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({
      pharmacy_id: 'alder-family-rx',
      name_reported: 'Alder Family Rx',
      source_utt_id: 'u0004',
    })
  })
})
