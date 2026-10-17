import { describe, expect, it } from 'vitest'
import { loadRecordings, replayStep, recordedDate } from '../lib/recordings'
import { recordingSchema, type Recording } from '../lib/contracts'
import { interpretationLabel, searchChanges, usedEvidence } from '../lib/evidence'
import { response, trace } from './fixtures'

function recorded(): Recording {
  return {
    schema_version: 'agentic_rag_recording_v1',
    id: 'test',
    title: 'TEST: actual capture',
    kind: 'recorded',
    recorded_at: '2026-10-14T16:30:00Z',
    steps: [
      {
        request: { endpoint: '/query', body: { question: 'TEST: question', debug: true } },
        response: response(),
      },
    ],
  }
}
describe('recording contract and provenance', () => {
  it('keeps legacy clarify captures readable and replays stored responses without adding trace', () => {
    const value = recorded()
    const saved = response({ response_metadata: undefined })
    value.steps.push({
      request: { endpoint: '/query/clarify', body: { clarification_id: 'legacy-id', selected_option: '1' } },
      response: saved,
    })
    const parsed = recordingSchema.parse(value)
    expect(parsed.steps[1].request.body).not.toHaveProperty('debug')
    expect(replayStep(parsed, 1, {
      endpoint: '/query/clarify',
      body: { clarification_id: 'legacy-id', selected_option: '1', debug: true },
    })).toEqual(saved)
    expect(saved.response_metadata).toBeUndefined()
  })
  it('requires an actual date for recorded captures and no invented date on examples', () => {
    expect(recordingSchema.safeParse({ ...recorded(), recorded_at: null }).success).toBe(false)
    expect(recordingSchema.safeParse({ ...recorded(), kind: 'format_example' }).success).toBe(false)
    expect(recordingSchema.safeParse({ ...recorded(), recorded_at: 'not a date' }).success).toBe(
      false,
    )
    expect(recordingSchema.safeParse(recorded()).success).toBe(true)
  })
  it('reports malformed and duplicate files instead of treating them as recordings', () => {
    const result = loadRecordings({ 'a.json': recorded(), 'b.json': {}, 'c.json': recorded() })
    expect(result.recordings).toHaveLength(1)
    expect(result.errors).toEqual(['b.json', 'c.json'])
  })
  it('only returns the exact next saved response, with independent object identity', () => {
    const value = recorded()
    expect(replayStep(value, 0, value.steps[0].request)).toEqual(value.steps[0].response)
    expect(replayStep(value, 0, value.steps[0].request)).not.toBe(value.steps[0].response)
    expect(() => replayStep(value, 1, value.steps[0].request)).toThrow('녹화가 끝났습니다')
    expect(() =>
      replayStep(value, 0, { endpoint: '/query', body: { question: 'different', debug: true } }),
    ).toThrow('일치')
  })
  it('renders execution dates in Seoul time regardless of host timezone', () => {
    expect(recordedDate('2026-10-14T16:30:00Z')).toContain('2026. 10. 15.')
    expect(recordedDate(null)).toBe('실행일 없음')
    expect(recordedDate('bad date')).toBe('실행일 확인 불가')
  })
})
describe('evidence projection', () => {
  it('never guesses the parser source from validation status or normalized fields', () => {
    expect(interpretationLabel(trace)).toBe('응답에 제공되지 않음')
    expect(interpretationLabel({ ...trace, interpreter: { parsing_source: 'rule' } })).toBe(
      '규칙 parser',
    )
    expect(interpretationLabel({ ...trace, interpreter: { parsing_source: 'llm' } })).toBe(
      'LLM Interpreter',
    )
  })
  it('keeps cited evidence order, omits unused retrieved papers, and falls back honestly for missing metadata', () => {
    const value = response({ used_literature_chunk_ids: ['MISSING', 'TEST::001'] })
    expect(usedEvidence(value)).toEqual([{ chunk_id: 'MISSING' }, trace.final_evidence[0]])
  })
  it('does not advance the previous query to a failed recovery query', () => {
    const value = structuredClone(trace)
    value.recovery = [
      { attempt: 1, status: 'failure', query: 'failed rewrite' },
      { attempt: 2, status: 'query_ready', query: 'successful rewrite' },
    ]
    expect(searchChanges(value)[1].before).toBe('initial test query')
  })
})

it('loads raw JSON captures and reports syntax errors without breaking the app', () => {
  const loaded = loadRecordings({ 'valid.json': JSON.stringify(recorded()), 'bad.json': '{broken' })
  expect(loaded.recordings).toHaveLength(1)
  expect(loaded.errors).toEqual(['bad.json'])
})
