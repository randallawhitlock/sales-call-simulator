// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Markdown } from './Markdown.tsx'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Markdown', () => {
  it.each([['#hashtag'], ['#'], ['##### Five']])('renders %j as paragraph text and finishes', (text) => {
    const { container } = render(<Markdown text={text} />)
    const paras = container.querySelectorAll('p')
    expect(paras).toHaveLength(1)
    expect(paras[0]?.textContent).toBe(text)
  })

  it('handles CRLF line endings', () => {
    const { container } = render(<Markdown text={'# Title\r\nBody'} />)
    expect(container.querySelector('h3')?.textContent).toBe('Title')
    expect(container.querySelector('p')?.textContent).toBe('Body')
  })

  it('gives every block a distinct key', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(<Markdown text={'para\n# H'} />)
    expect(container.querySelector('p')?.textContent).toBe('para')
    expect(container.querySelector('h3')?.textContent).toBe('H')
    expect(spy).not.toHaveBeenCalled()
  })

  it('renders a normal document', () => {
    const text = '# Plan\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n- one\n- two\n\nSome **bold** text'
    const { container } = render(<Markdown text={text} />)
    expect(container.querySelector('h3')?.textContent).toBe('Plan')
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelectorAll('td')).toHaveLength(2)
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.querySelector('p strong')?.textContent).toBe('bold')
  })
})
