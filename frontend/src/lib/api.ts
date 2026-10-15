import { queryResponseSchema, requestSchema, type ApiRequest } from './contracts'

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
const messages: Record<number, string> = {
  400: '선택 내용을 확인해 주세요.',
  404: '확인 요청을 찾을 수 없거나 이미 사용했습니다. 원 질문을 다시 보내 주세요.',
  409: '운동 후보 선택에는 서버의 Phase A 모드가 필요합니다.',
  410: '확인 요청이 만료되었습니다. 원 질문을 다시 보내 주세요.',
  422: '입력 형식이나 질문 길이를 확인해 주세요.',
  500: '서버에서 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  503: '질문 실행 환경을 사용할 수 없습니다. 서버 설정을 확인해 주세요.',
}
export async function executeRequest(request: ApiRequest, signal?: AbortSignal) {
  requestSchema.parse(request)
  let response: Response
  try {
    response = await fetch(baseUrl + request.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.body),
      signal,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new Error('서버에 연결하지 못했습니다. API 실행 상태를 확인해 주세요.', { cause: error })
  }
  if (!response.ok) throw new Error(messages[response.status] || '서버 요청에 실패했습니다.')
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error('서버 응답 형식을 확인할 수 없습니다.')
  }
  const parsed = queryResponseSchema.safeParse(body)
  if (!parsed.success) throw new Error('서버 응답 형식을 확인할 수 없습니다.')
  return parsed.data
}
export async function readSystem(signal?: AbortSignal) {
  const results = await Promise.allSettled([
    fetch(baseUrl + '/health', { signal }).then(async (response) => {
      if (!response.ok || (await response.json()).status !== 'ok') throw new Error('health')
    }),
    fetch(baseUrl + '/version', { signal }).then(async (response) => {
      if (!response.ok) return null
      const value = await response.json()
      return typeof value.app_version === 'string' ? value.app_version : null
    }),
  ])
  return {
    connected: results[0].status === 'fulfilled',
    version: results[1].status === 'fulfilled' ? results[1].value : null,
  }
}
