import { useState } from 'react'
import { ApiError } from './api.ts'

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'Something went wrong'
}

/** Runs an async action with busy and error state. */
export function useAction(): {
  busy: boolean
  error: string | null
  run: (action: () => Promise<void>) => Promise<void>
} {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function run(action: () => Promise<void>): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

export function money(value: string | null): string {
  if (value === null) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : value
}

export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
