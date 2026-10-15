import { afterEach, expect, it, vi } from 'vitest'
import { executeRequest } from '../lib/api'
import { requestSchema } from '../lib/contracts'

afterEach(() => vi.unstubAllGlobals())
const request = {
  endpoint: '/query' as const,
  body: { question: 'TEST: input', debug: true as const },
}
it('reports invalid JSON without exposing raw server content', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('<html>internal details</html>')),
  )
  await expect(executeRequest(request)).rejects.toThrow('서버 응답 형식')
})
it('reports invalid response contracts and unavailable runtime distinctly', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}')),
  )
  await expect(executeRequest(request)).rejects.toThrow('서버 응답 형식')
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}', { status: 503 })),
  )
  await expect(executeRequest(request)).rejects.toThrow('질문 실행 환경')
})
it('rejects disabled debug and unsupported clarify fields before transport', () => {
  expect(
    requestSchema.safeParse({ ...request, body: { ...request.body, debug: false } }).success,
  ).toBe(false)
  expect(
    requestSchema.safeParse({
      endpoint: '/query/clarify',
      body: { clarification_id: 'id', selected_option: '1', debug: true },
    }).success,
  ).toBe(false)
})
