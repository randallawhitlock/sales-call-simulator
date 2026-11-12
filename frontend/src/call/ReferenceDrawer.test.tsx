// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Reference } from '../api.ts'
import css from '../index.css?raw'
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
    {
      rule_id: 'R-OLD',
      title: 'Withdrawn rule',
      requirement: 'No longer asked of anyone.',
      authority: 'regulatory',
      citation: '42 CFR 422.0',
      source_urls: ['https://www.ecfr.gov/c'],
      status: 'retired',
      verified_as_of: '2025-01-01',
    },
    {
      rule_id: 'G-DISP',
      title: 'Contested guidance',
      requirement: 'Reviewers disagree about this one.',
      authority: 'cms_guidance',
      citation: 'CMS memo',
      source_urls: ['https://www.cms.gov/d'],
      status: 'disputed',
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
    expect(screen.getAllByText('not yet verified')).toHaveLength(1)
    expect(screen.getByText('Summary of Benefits').parentElement?.textContent).toContain('not yet verified')
    // A disputed rule says so; a retired one is not a requirement and is not listed.
    expect(screen.getByText('Contested guidance').parentElement?.textContent).toContain('disputed')
    expect(screen.queryByText('Withdrawn rule')).toBeNull()
    expect(button.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement?.textContent).toBe('Close')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('complementary', { name: 'Reference' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })

  it('leaves Escape and focus alone while the learner is in another control', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(REFERENCE), { status: 200 })))
    render(
      <>
        <ReferenceButton />
        <textarea aria-label="Your line" />
      </>,
    )
    const button = screen.getByRole('button', { name: 'Reference' })
    const composer = screen.getByLabelText('Your line')
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByText('CMS standardized')).toBeTruthy())

    composer.focus()
    fireEvent.keyDown(composer, { key: 'Escape' })
    expect(screen.getByRole('complementary', { name: 'Reference' })).toBeTruthy()
    expect(document.activeElement).toBe(composer)

    // Closing from the header button while typing does not pull focus out of the composer.
    fireEvent.click(button)
    expect(screen.queryByRole('complementary', { name: 'Reference' })).toBeNull()
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(composer)
  })

  it('closes on Escape from its own button, and from its Close control', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(REFERENCE), { status: 200 })))
    render(<ReferenceButton />)
    const button = screen.getByRole('button', { name: 'Reference' })
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByText('CMS standardized')).toBeTruthy())
    button.focus()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('complementary', { name: 'Reference' })).toBeNull()

    fireEvent.click(button)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('complementary', { name: 'Reference' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })

  it('hangs below the call header instead of covering it', () => {
    // jsdom does not lay out, so this pins the rules that keep Leave and Reference in reach.
    const rule = (selector: string) => new RegExp(`\\${selector} \\{([^}]*)\\}`).exec(css)?.[1] ?? ''
    expect(rule('.drawer')).toMatch(/position: absolute;\s*top: 100%;/)
    expect(rule('.drawer')).not.toMatch(/position: fixed/)
    expect(rule('.call-header')).toMatch(/position: sticky/)
  })
})
