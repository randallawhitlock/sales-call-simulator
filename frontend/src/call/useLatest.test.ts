// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useLatest } from './useLatest.ts'

describe('useLatest', () => {
  it('keeps the newest response when an older request resolves last', async () => {
    const pending: ((value: string) => void)[] = []
    const load = () => new Promise<string>((resolve) => pending.push(resolve))
    const { result } = renderHook(() => useLatest(load, 'initial'))

    let initialLoad!: Promise<void>
    let afterSave!: Promise<void>
    act(() => {
      initialLoad = result.current[1]()
      afterSave = result.current[1]()
    })
    await act(async () => {
      pending[1]?.('saved')
      await afterSave
    })
    await act(async () => {
      pending[0]?.('stale')
      await initialLoad
    })
    expect(result.current[0]).toBe('saved')
  })

  it('applies a lone response', async () => {
    const { result } = renderHook(() => useLatest(() => Promise.resolve(3), 0))
    await act(() => result.current[1]())
    expect(result.current[0]).toBe(3)
  })
})
