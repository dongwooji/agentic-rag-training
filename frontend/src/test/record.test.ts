// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { recordSession } from '../../scripts/record'
import { appendClarification } from '../lib/questions'
import type { ApiRequest, Recording } from '../lib/contracts'
import { clarification, response } from './fixtures'

describe('real response capture workflow', () => {
  it('records returned JSON unchanged and always sends debug on queries including text follow-up', async () => {
    const requests: ApiRequest[] = []
    const saved: Recording[] = []
    const first = clarification()
    const second = { ...response(), future_server_field: { retained: true } }
    const execute = vi.fn(async (request: ApiRequest) => {
      requests.push(request)
      return requests.length === 1 ? first : second
    })
    const result = await recordSession('TEST: original', 'TEST: capture', {
      execute,
      ask: async () => 'TEST: period',
      save: async (item) => {
        saved.push(item)
      },
    })
    expect(requests).toEqual([
      { endpoint: '/query', body: { question: 'TEST: original', debug: true } },
      {
        endpoint: '/query',
        body: { question: appendClarification('TEST: original', 'TEST: period'), debug: true },
      },
    ])
    expect(saved).toHaveLength(2)
    expect(saved[0].steps).toHaveLength(1)
    expect(result.kind).toBe('recorded')
    expect(result.recorded_at).toMatch(/^\d{4}-\d{2}-\d{2}T/)
    expect(result.steps[1].response).toEqual(second)
  })
  it('captures candidate selection with only the supported clarify fields and no invented debug trace', async () => {
    const first = clarification(true)
    const second = response({ response_metadata: undefined })
    const requests: ApiRequest[] = []
    const result = await recordSession('TEST: exercise', 'TEST: capture', {
      execute: async (request) => {
        requests.push(request)
        return requests.length === 1 ? first : second
      },
      ask: async () => '2',
      save: async () => {},
    })
    expect(requests[1]).toEqual({
      endpoint: '/query/clarify',
      body: { clarification_id: 'test-id', selected_option: '2' },
    })
    expect(result.steps[1].response.response_metadata).toBeUndefined()
  })
  it('retains the completed clarification step when no follow-up is supplied', async () => {
    const result = await recordSession('TEST: incomplete', 'TEST: capture', {
      execute: async () => clarification(),
      ask: async () => null,
      save: async () => {},
    })
    expect(result.steps).toHaveLength(1)
  })
  it('does not save a failed transport or invalid server response as a successful recording', async () => {
    const save = vi.fn()
    await expect(
      recordSession('TEST: failure', 'TEST: capture', {
        execute: async () => {
          throw new Error('HTTP 503')
        },
        ask: async () => null,
        save,
      }),
    ).rejects.toThrow('HTTP 503')
    expect(save).not.toHaveBeenCalled()
  })
})
