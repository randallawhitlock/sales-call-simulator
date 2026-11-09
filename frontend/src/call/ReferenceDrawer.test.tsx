// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Reference } from '../api.ts'
import { ReferenceButton } from './ReferenceDrawer.tsx'

const REFERENCE: Reference = {
  ruleset_version: 'mvp-2027.1',
  verified_as_of: '2026-11-07',
  standard: [
    {
      rule_id: 'R-DISC',
      title: 'TPMO disclaimer',
      text: 'We do not offer every plan available in your area. Currently we represent 3 organizations.',
      citation: '42 CFR 422.2267(e)(41)',
      source_urls: ['https://www.ecfr.gov/a'],
      verified_as_of: '2026-11-07',
    },
  ],
  rules: [
    {
      rule_id: 'O-RECNOTE',
      title: 'Recording notice',
      requirement: 'Tell the caller the call is recorded.',
      authority: 'organization',
      citation: 'Organization policy',
      source_urls: ['https://example.test/policy'],
      status: 'approved',
      verified_as_of: '2026-11-07',
    },
    {
      rule_id: 'R-SB',
      title: 'Summary of Benefits',
      requirement: 'The Summary of Benefits is provided.',
      authority: 'regulatory',
      citation: '42 CFR 422.2267(e)',
      source_urls: ['https://www.ecfr.gov/b'],
      status: 'unverified',
      verified_as_of: null,
    },
  ],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ReferenceButton', () => {
  it('opens a drawer that keeps CMS text apart from the app wording, and closes on Escape', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(REFERENCE), { status: 200 })))
    render(<ReferenceButton />)
    const button = screen.getByRole('button', { name: 'Reference' })
    fireEvent.click(button)

    await waitFor(() => expect(screen.getByText(/Currently we represent 3 organizations/)).toBeTruthy())
    expect(screen.getByText('CMS standardized')).toBeTruthy()
    expect(screen.getByText('Agency policy')).toBeTruthy()
    expect(screen.getByText('not yet verified')).toBeTruthy()
    expect(button.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement?.textContent).toBe('Close')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('complementary', { name: 'Reference' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })
})
