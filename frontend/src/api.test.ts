import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, get } from './api.ts'

function reply(body: string, status: number, statusText = ''): void {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status, statusText })))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request errors', () => {
  it('turns a plain-text 500 into an ApiError with the status', async () => {
    reply('Internal Server Error', 500)
    const err = await get('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).message.startsWith('HTTP 500')).toBe(true)
  })

  it('throws an ApiError when an ok response is not JSON', async () => {
    reply('<html>', 200)
    await expect(get('/x')).rejects.toBeInstanceOf(ApiError)
  })

  it('uses a string detail as the message', async () => {
    reply(JSON.stringify({ detail: 'nope' }), 400)
    await expect(get('/x')).rejects.toMatchObject({ status: 400, message: 'nope' })
  })

  it('falls back to the status when no detail entry has a message', async () => {
    reply(JSON.stringify({ detail: [{ loc: [] }] }), 422)
    await expect(get('/x')).rejects.toMatchObject({ status: 422, message: 'HTTP 422' })
  })

  it('returns parsed data on success', async () => {
    reply(JSON.stringify({ ok: true }), 200)
    await expect(get('/x')).resolves.toEqual({ ok: true })
  })
})
