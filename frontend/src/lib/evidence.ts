import type { Evidence, QueryResponse, Trace } from './contracts'

export function interpretationLabel(trace?: Trace | null) {
  const source = trace?.interpreter.parsing_source
  return source === 'rule'
    ? '규칙 parser'
    : source === 'llm'
      ? 'LLM Interpreter'
      : '응답에 제공되지 않음'
}
export function usedEvidence(response: QueryResponse): Evidence[] {
  const trace = response.response_metadata?.trace
  const known = [
    ...(trace?.final_evidence || []),
    ...(trace?.retrievals.flatMap((item) => item.evidence || []) || []),
  ]
  const byId = new Map<string, Evidence>()
  for (const evidence of known) {
    if (evidence.chunk_id && !byId.has(evidence.chunk_id)) byId.set(evidence.chunk_id, evidence)
  }
  return response.used_literature_chunk_ids.map((id) => byId.get(id) || { chunk_id: id })
}
export function searchChanges(trace?: Trace | null) {
  if (!trace) return []
  let previous =
    trace.retrievals.find((item) => item.phase === 'initial')?.query ||
    trace.initial_queries.dense ||
    trace.initial_queries.english ||
    null
  return trace.recovery.map((item) => {
    const change = {
      attempt: item.attempt,
      before: previous,
      after: item.query,
      status: item.status,
    }
    // A failed query is not the baseline for a subsequent rewrite.
    if (item.status === 'query_ready' && item.query) previous = item.query
    return change
  })
}
