import { useEffect, useState } from 'react'

interface HealthResponse {
  status: string
  version: string
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return typeof record['status'] === 'string' && typeof record['version'] === 'string'
}

type ApiState =
  | { kind: 'checking' }
  | { kind: 'ok'; version: string }
  | { kind: 'unreachable' }

function describe(state: ApiState): string {
  switch (state.kind) {
    case 'checking':
      return 'API: checking…'
    case 'ok':
      return `API: ok (v${state.version})`
    case 'unreachable':
      return 'API: unreachable'
  }
}

export function App() {
  const [api, setApi] = useState<ApiState>({ kind: 'checking' })

  useEffect(() => {
    const controller = new AbortController()

    async function check(): Promise<void> {
      try {
        const res = await fetch('/api/health', { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const body: unknown = await res.json()
        if (!isHealthResponse(body) || body.status !== 'ok') {
          throw new Error('Unexpected health response')
        }
        setApi({ kind: 'ok', version: body.version })
      } catch {
        if (!controller.signal.aborted) setApi({ kind: 'unreachable' })
      }
    }

    void check()
    return () => controller.abort()
  }, [])

  return (
    <main>
      <h1>Sales Call Simulator</h1>
      <p className="subtitle">
        Practice Medicare sales calls against a synthetic customer. Pre-alpha.
      </p>
      <p className="status">{describe(api)}</p>
    </main>
  )
}
