import { readFile, writeFile, mkdir, rename, access } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { parseArgs } from 'node:util'
import { createInterface } from 'node:readline/promises'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { appendClarification } from '../src/lib/questions.ts'
import {
  queryResponseSchema,
  recordingSchema,
  type ApiRequest,
  type QueryResponse,
  type Recording,
} from '../src/lib/contracts.ts'

export type RecordingPorts = {
  execute: (request: ApiRequest) => Promise<QueryResponse>
  ask: (response: QueryResponse) => Promise<string | null>
  save: (recording: Recording) => Promise<void>
}
export async function recordSession(question: string, title: string, ports: RecordingPorts) {
  let effectiveQuestion = question.trim()
  if (!effectiveQuestion || effectiveQuestion.length > 4000)
    throw new Error('질문은 1~4,000자여야 합니다.')
  const recording: Recording = {
    schema_version: 'agentic_rag_recording_v1',
    id: randomUUID(),
    title,
    kind: 'recorded',
    recorded_at: null,
    steps: [],
  }
  let request: ApiRequest = {
    endpoint: '/query',
    body: { question: effectiveQuestion, debug: true },
  }
  for (let index = 0; index < 30; index++) {
    const started = new Date().toISOString()
    const response = await ports.execute(request)
    queryResponseSchema.parse(response)
    recording.recorded_at ||= started
    // Store the server's JSON without filling fields or synthesizing trace data.
    recording.steps.push({ request, response })
    recordingSchema.parse(recording)
    await ports.save(structuredClone(recording))
    const metadata = response.response_metadata
    if (!metadata?.clarification_required) return recording
    const answer = (await ports.ask(response))?.trim()
    if (!answer) return recording
    if (metadata.options?.length && metadata.clarification_id) {
      if (!metadata.options.some((option) => option.id === answer))
        throw new Error('반환된 후보 ID를 선택해 주세요.')
      request = {
        endpoint: '/query/clarify',
        body: { clarification_id: metadata.clarification_id, selected_option: answer, debug: true },
      }
    } else {
      effectiveQuestion = appendClarification(effectiveQuestion, answer)
      if (effectiveQuestion.length > 4000)
        throw new Error('추가 답변을 포함한 질문이 4,000자를 초과했습니다.')
      request = { endpoint: '/query', body: { question: effectiveQuestion, debug: true } }
    }
  }
  throw new Error('녹화는 최대 30단계까지 지원합니다. 완료된 단계는 저장했습니다.')
}
async function main() {
  const { values } = parseArgs({
    options: {
      'question-file': { type: 'string' },
      output: { type: 'string' },
      title: { type: 'string' },
      'base-url': { type: 'string', default: 'http://127.0.0.1:8000' },
      'answers-file': { type: 'string' },
      help: { type: 'boolean' },
    },
  })
  if (values.help) {
    console.log(
      'npm run record -- --question-file question.txt --output recordings/my-run.json --title "실제 실행" [--base-url http://127.0.0.1:8000] [--answers-file answers.json]',
    )
    return
  }
  if (!values['question-file'] || !values.output || !values.title?.trim())
    throw new Error('--question-file, --output, --title을 지정해 주세요.')
  const output = resolve(values.output)
  try {
    await access(output)
    throw new Error('기존 파일을 덮어쓰지 않습니다. 새 output 경로를 지정해 주세요.')
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error
  }
  const answers: unknown = values['answers-file']
    ? JSON.parse(await readFile(values['answers-file'], 'utf8'))
    : null
  if (
    answers !== null &&
    (!Array.isArray(answers) || !answers.every((item) => typeof item === 'string'))
  )
    throw new Error('answers 파일은 문자열 배열이어야 합니다.')
  const queue = answers as string[] | null
  const rl =
    queue === null ? createInterface({ input: process.stdin, output: process.stdout }) : null
  let written = false
  try {
    const recording = await recordSession(
      await readFile(values['question-file'], 'utf8'),
      values.title.trim(),
      {
        execute: async (request) => {
          const response = await fetch(values['base-url']!.replace(/\/$/, '') + request.endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request.body),
            signal: AbortSignal.timeout(180_000),
          })
          if (!response.ok)
            throw new Error('서버 HTTP ' + response.status + ': 완료된 단계까지만 저장했습니다.')
          const body: unknown = await response.json()
          queryResponseSchema.parse(body)
          return body as QueryResponse
        },
        ask: async (response) => {
          console.log(response.answer_text)
          const options = response.response_metadata?.options
          if (options?.length)
            console.log(options.map((option) => option.id + ': ' + option.label).join('\n'))
          return queue
            ? queue.shift() || null
            : await rl!.question(
                options?.length
                  ? '선택할 후보 ID (빈 입력: 저장 후 종료): '
                  : '추가 답변 (빈 입력: 저장 후 종료): ',
              )
        },
        save: async (recording) => {
          await mkdir(dirname(output), { recursive: true })
          const content = JSON.stringify(recording, null, 2) + '\n'
          if (!written) {
            await writeFile(output, content, { encoding: 'utf8', flag: 'wx' })
            written = true
          } else {
            const temporary = output + '.' + recording.id + '.tmp'
            await writeFile(temporary, content, { encoding: 'utf8', flag: 'wx' })
            await rename(temporary, output)
          }
        },
      },
    )
    console.log(
      '저장 완료: ' +
        output +
        ' (' +
        recording.steps.length +
        '단계, 실행일 ' +
        recording.recorded_at +
        ')',
    )
  } finally {
    rl?.close()
  }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : '녹화에 실패했습니다.')
    process.exitCode = 1
  })
}
