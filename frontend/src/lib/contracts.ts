import { z } from 'zod'

const nullableText = z.string().nullish()
export const evidenceSchema = z.looseObject({
  chunk_id: nullableText,
  paper_id: nullableText,
  title: nullableText,
  section: nullableText,
  rank: z.number().nullish(),
})
const gradeComponentSchema = z.looseObject({
  component_id: nullableText,
  requirement: nullableText,
  status: z.string().optional(),
})
const traceSchema = z.looseObject({
  version: z.string(),
  interpreter: z.looseObject({
    // The current API omits parsing_source. Never infer it from other fields.
    parsing_source: z.enum(['rule', 'llm']).nullish(),
    validation: z.looseObject({ status: z.string().optional() }).optional(),
  }),
  initial_queries: z.looseObject({
    korean: nullableText,
    english: nullableText,
    dense: nullableText,
    bm25: nullableText,
  }),
  retrievals: z.array(
    z.looseObject({
      phase: z.string(),
      attempt: z.number(),
      query: nullableText,
      evidence: z.array(evidenceSchema).optional(),
    }),
  ),
  graders: z.array(
    z.looseObject({
      attempt: z.number(),
      execution_status: z.string(),
      evidence_sufficient: z.boolean(),
      components: z.array(gradeComponentSchema),
    }),
  ),
  recovery: z.array(
    z.looseObject({
      attempt: z.number(),
      status: z.string(),
      query: nullableText,
    }),
  ),
  fusions: z.array(z.unknown()),
  final_evidence: z.array(evidenceSchema),
})
export const queryResponseSchema = z.looseObject({
  final_status: z.enum(['answer_ready', 'abstain_ready', 'execution_failure']),
  answer_text: z.string().min(1),
  route: z.string(),
  tools_used: z.array(z.string()),
  retry_count: z.number().int().nonnegative(),
  recovery_used: z.boolean(),
  used_literature_chunk_ids: z.array(z.string()),
  limitations: z.array(z.string()),
  latency_ms: z.number().nonnegative(),
  error: z.object({ code: z.string(), message: z.string() }).nullish(),
  response_metadata: z
    .looseObject({
      response_mode: z.string(),
      clarification_required: z.boolean().optional(),
      clarification_id: nullableText,
      options: z
        .array(
          z.object({
            id: z.string(),
            label: z.string(),
            canonical_exercises: z.array(z.string()),
          }),
        )
        .optional(),
      expires_at: nullableText,
      trace: traceSchema.nullish(),
    })
    .nullish(),
})
export type QueryResponse = z.infer<typeof queryResponseSchema>
export type Evidence = z.infer<typeof evidenceSchema>
export type Trace = z.infer<typeof traceSchema>

export const requestSchema = z.discriminatedUnion('endpoint', [
  z
    .object({
      endpoint: z.literal('/query'),
      body: z
        .object({ question: z.string().trim().min(1).max(4000), debug: z.literal(true) })
        .strict(),
    })
    .strict(),
  z
    .object({
      endpoint: z.literal('/query/clarify'),
      // The backend forbids debug here; do not send an unsupported field.
      body: z
        .object({ clarification_id: z.string().min(1), selected_option: z.string().min(1) })
        .strict(),
    })
    .strict(),
])
export type ApiRequest = z.infer<typeof requestSchema>
export const recordingSchema = z
  .object({
    schema_version: z.literal('agentic_rag_recording_v1'),
    id: z.string().min(1),
    title: z.string().min(1),
    kind: z.enum(['recorded', 'format_example']),
    recorded_at: z.iso.datetime({ offset: true }).nullable(),
    steps: z
      .array(
        z
          .object({
            request: requestSchema,
            response: queryResponseSchema,
          })
          .strict(),
      )
      .min(1),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.kind === 'recorded' && value.recorded_at === null) {
      context.addIssue({ code: 'custom', message: '실제 녹화에는 recorded_at이 필요합니다.' })
    }
    if (value.kind === 'format_example' && value.recorded_at !== null) {
      context.addIssue({ code: 'custom', message: '형식 예시에 실행일을 지정할 수 없습니다.' })
    }
    if (value.steps[0].request.endpoint !== '/query') {
      context.addIssue({ code: 'custom', message: '첫 단계는 /query여야 합니다.' })
    }
  })
export type Recording = z.infer<typeof recordingSchema>
