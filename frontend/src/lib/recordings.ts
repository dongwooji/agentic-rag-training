import { recordingSchema, type ApiRequest, type Recording } from './contracts'

export function loadRecordings(modules: Record<string, unknown>) {
  const recordings: Recording[] = []
  const errors: string[] = []
  const ids = new Set<string>()
  for (const [path, value] of Object.entries(modules)) {
    let input: unknown = value
    if (typeof value === 'string') {
      try {
        input = JSON.parse(value)
      } catch {
        errors.push(path.split('/').pop() || path)
        continue
      }
    }
    const parsed = recordingSchema.safeParse(input)
    if (!parsed.success || ids.has(parsed.data.id)) {
      errors.push(path.split('/').pop() || path)
    } else {
      ids.add(parsed.data.id)
      recordings.push(parsed.data)
    }
  }
  return { recordings, errors }
}
export function replayStep(recording: Recording, index: number, request: ApiRequest) {
  const step = recording.steps[index]
  if (!step)
    throw new Error('녹화가 끝났습니다. 처음부터 재생하거나 실제 서버 모드에서 질문해 주세요.')
  const expected = step.request
  const matches =
    expected.endpoint === '/query' && request.endpoint === '/query'
      ? expected.body.question === request.body.question && request.body.debug === true
      : expected.endpoint === '/query/clarify' &&
        request.endpoint === '/query/clarify' &&
        expected.body.clarification_id === request.body.clarification_id &&
        expected.body.selected_option === request.body.selected_option
  if (!matches) {
    throw new Error(
      '이 입력과 일치하는 다음 녹화 단계가 없습니다. 녹화된 입력을 사용하거나 실제 서버 모드로 전환해 주세요.',
    )
  }
  return structuredClone(step.response)
}
export const library = loadRecordings(
  import.meta.glob('../../recordings/*.json', { eager: true, query: '?raw', import: 'default' }),
)
export function recordedDate(value: string | null) {
  if (!value) return '실행일 없음'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '실행일 확인 불가'
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
