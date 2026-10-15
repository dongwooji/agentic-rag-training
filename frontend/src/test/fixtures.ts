import type { QueryResponse, Trace } from '../lib/contracts'

// Controlled test doubles only. Never imported by the application or recording library.
export const trace: Trace = {
  version: 'query_debug_trace_v1',
  interpreter: { validation: { status: 'ready' } },
  initial_queries: { dense: 'initial test query' },
  retrievals: [{ phase: 'initial', attempt: 0, query: 'initial test query', evidence: [] }],
  graders: [
    {
      attempt: 0,
      execution_status: 'completed',
      evidence_sufficient: false,
      components: [
        { component_id: 'C1', requirement: 'TEST: required evidence', status: 'missing' },
      ],
    },
  ],
  recovery: [{ attempt: 1, status: 'query_ready', query: 'revised test query' }],
  fusions: [],
  final_evidence: [
    {
      chunk_id: 'TEST::001',
      paper_id: 'TEST',
      title: 'TEST: paper title',
      section: 'TEST: Results',
    },
  ],
}
export function response(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    final_status: 'answer_ready',
    answer_text: 'TEST: answer',
    route: 'literature_only',
    tools_used: ['search_literature'],
    retry_count: 1,
    recovery_used: true,
    used_literature_chunk_ids: ['TEST::001'],
    limitations: [],
    latency_ms: 2300,
    response_metadata: { response_mode: 'debug', trace: structuredClone(trace) },
    ...overrides,
  }
}
export function clarification(withOptions = false): QueryResponse {
  return response({
    final_status: 'abstain_ready',
    answer_text: 'TEST: clarify conditions',
    route: 'ambiguous',
    tools_used: [],
    used_literature_chunk_ids: [],
    retry_count: 0,
    recovery_used: false,
    response_metadata: {
      response_mode: 'clarification_required',
      clarification_required: true,
      ...(withOptions
        ? {
            clarification_id: 'test-id',
            options: [
              { id: '1', label: 'TEST: barbell', canonical_exercises: ['TEST: barbell'] },
              { id: '2', label: 'TEST: dumbbell', canonical_exercises: ['TEST: dumbbell'] },
            ],
          }
        : {}),
    },
  })
}
