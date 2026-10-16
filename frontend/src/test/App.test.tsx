import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { appendClarification } from '../lib/questions'
import { library } from '../lib/recordings'
import { clarification, response } from './fixtures'

const posts: { url: string; body: Record<string, unknown> }[] = []
function mockServer(responses: (ReturnType<typeof response> | number)[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!init?.method)
        return new Response(
          JSON.stringify(url.endsWith('/health') ? { status: 'ok' } : { app_version: 'test' }),
        )
      posts.push({ url, body: JSON.parse(init.body as string) })
      const result = responses.shift()
      if (typeof result === 'number') return new Response('{}', { status: result })
      if (!result) throw new Error('Unexpected POST in test')
      return new Response(JSON.stringify(result))
    }),
  )
}
async function ask(question = 'TEST: original question') {
  fireEvent.change(screen.getByLabelText('질문 입력'), { target: { value: question } })
  await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }))
}
beforeEach(() => {
  posts.length = 0
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('question and evidence experience', () => {
  it('always sends query debug and presents actual response evidence without inferring parser source', async () => {
    mockServer([response()])
    render(<App />)
    await ask()
    expect(await screen.findByText('TEST: answer')).toBeInTheDocument()
    expect(posts).toEqual([
      { url: '/api/query', body: { question: 'TEST: original question', debug: true } },
    ])
    const panel = within(screen.getByRole('complementary', { name: '실행 근거' }))
    expect(panel.getByText('응답에 제공되지 않음')).toBeInTheDocument()
    expect(panel.getByText('TEST: paper title')).toBeInTheDocument()
    expect(panel.getByText('TEST: Results')).toBeInTheDocument()
    expect(panel.getByText('근거 부족')).toBeInTheDocument()
    expect(panel.getByText('initial test query')).toBeInTheDocument()
    expect(panel.getByText('revised test query')).toBeInTheDocument()
    expect(panel.getByText('search_literature')).toBeInTheDocument()
  })

  it('takes free-text clarification and resubmits the complete question with debug true', async () => {
    mockServer([clarification(), response()])
    render(<App />)
    await ask()
    const reply = await screen.findByLabelText('추가 답변')
    fireEvent.change(reply, { target: { value: '2018-04-01부터 2018-06-30까지' } })
    await userEvent.click(screen.getByRole('button', { name: '추가 답변 보내기' }))
    expect(await screen.findByText('TEST: answer')).toBeInTheDocument()
    expect(posts[1]).toEqual({
      url: '/api/query',
      body: {
        question: appendClarification('TEST: original question', '2018-04-01부터 2018-06-30까지'),
        debug: true,
      },
    })
  })

  it('keeps successive free-text clarification answers attached to the original question', async () => {
    mockServer([clarification(), clarification(), response()])
    render(<App />)
    await ask()
    fireEvent.change(await screen.findByLabelText('추가 답변'), {
      target: { value: 'TEST: first detail' },
    })
    await userEvent.click(screen.getByRole('button', { name: '추가 답변 보내기' }))
    await waitFor(() => expect(posts).toHaveLength(2))
    fireEvent.change(await screen.findByLabelText('추가 답변'), {
      target: { value: 'TEST: second detail' },
    })
    await userEvent.click(screen.getByRole('button', { name: '추가 답변 보내기' }))
    await screen.findByText('TEST: answer')
    expect(posts[2].body.question).toBe(
      appendClarification(
        appendClarification('TEST: original question', 'TEST: first detail'),
        'TEST: second detail',
      ),
    )
  })

  it('submits only accepted fields for exercise selection and recovers from expiration', async () => {
    mockServer([clarification(true), 410, response()])
    render(<App />)
    await ask()
    await userEvent.click(await screen.findByRole('button', { name: 'TEST: dumbbell' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('만료')
    expect(posts[1]).toEqual({
      url: '/api/query/clarify',
      body: { clarification_id: 'test-id', selected_option: '2', debug: true },
    })
    await userEvent.click(screen.getByRole('button', { name: '원 질문 다시 보내기' }))
    expect(await screen.findByText('TEST: answer')).toBeInTheDocument()
    expect(posts[2].body).toEqual({ question: 'TEST: original question', debug: true })
  })

  it.each([true, false])('sends clarify debug and displays only the selected answer evidence (trace=%s)', async (hasTrace) => {
    const selected = response({
      answer_text: 'TEST: selected answer',
      route: 'hybrid',
      tools_used: ['query_training_log', 'compute_metrics', 'search_literature'],
      used_literature_chunk_ids: ['SELECTED::001'],
    })
    const trace = selected.response_metadata!.trace!
    trace.final_evidence = [{ chunk_id: 'SELECTED::001', title: 'TEST: selected paper', section: 'TEST: selected section' }]
    trace.initial_queries.dense = 'TEST: selected query'
    trace.retrievals[0].query = 'TEST: selected query'
    trace.recovery[0].query = 'TEST: selected rewrite'
    trace.graders[0].components[0].requirement = 'TEST: selected requirement'
    if (!hasTrace) selected.response_metadata = undefined
    mockServer([response(), clarification(true), selected])
    render(<App />)
    await ask('TEST: previous independent question')
    await screen.findByText('TEST: answer')
    await ask('TEST: ambiguous exercise question')
    await userEvent.click(await screen.findByRole('button', { name: 'TEST: dumbbell' }))
    await screen.findByText('TEST: selected answer')
    expect(posts).toHaveLength(3)
    expect(posts[2]).toEqual({
      url: '/api/query/clarify',
      body: { clarification_id: 'test-id', selected_option: '2', debug: true },
    })
    const panel = within(screen.getByRole('complementary', { name: '실행 근거' }))
    expect(panel.getByText('hybrid')).toBeInTheDocument()
    expect(panel.getByText('compute_metrics')).toBeInTheDocument()
    expect(panel.queryByText('TEST: paper title')).not.toBeInTheDocument()
    expect(panel.queryByText('initial test query')).not.toBeInTheDocument()
    if (hasTrace) {
      expect(panel.getByText('TEST: selected paper')).toBeInTheDocument()
      expect(panel.getByText('TEST: selected section')).toBeInTheDocument()
      expect(panel.getByText('TEST: selected query')).toBeInTheDocument()
      expect(panel.getByText('TEST: selected requirement')).toBeInTheDocument()
      expect(panel.getByText('TEST: selected rewrite')).toBeInTheDocument()
    } else {
      expect(panel.getByText('논문 제목 미제공')).toBeInTheDocument()
      expect(panel.getByText('Grader 정보가 응답에 제공되지 않았습니다.')).toBeInTheDocument()
    }
  })

  it('treats a structured HTTP 200 execution failure as a failure, not a successful answer', async () => {
    mockServer([
      response({
        final_status: 'execution_failure',
        answer_text: 'TEST: cannot execute',
        error: { code: 'test_failure', message: 'TEST: execution error' },
      }),
    ])
    render(<App />)
    await ask()
    expect(await screen.findByText('실행 실패')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('TEST: execution error')
  })

  it('blocks empty/overlong questions and preserves Korean composition Enter', async () => {
    mockServer([response()])
    render(<App />)
    const input = screen.getByLabelText('질문 입력')
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeDisabled()
    fireEvent.change(input, { target: { value: '가'.repeat(4001) } })
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeDisabled()
    fireEvent.change(input, { target: { value: 'TEST: composing' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(posts).toHaveLength(0)
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(posts).toHaveLength(0)
  })

  it('prevents duplicate submissions while waiting and allows aborting the wait', async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (!init?.method)
        return Promise.resolve(
          new Response(JSON.stringify(url.endsWith('/health') ? { status: 'ok' } : {})),
        )
      posts.push({ url, body: JSON.parse(init.body as string) })
      return new Promise<Response>((_resolve, reject) =>
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        ),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    await ask()
    expect(screen.getByLabelText('질문 입력')).toBeDisabled()
    expect(screen.getByRole('button', { name: '녹화 재생' })).toBeDisabled()
    expect(posts).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: '응답 대기 중지' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('서버 실행은 계속될 수 있습니다')
    expect(screen.getByLabelText('질문 입력')).toHaveValue('TEST: original question')
  })

  it('replays the single labeled format example without any POST or manufactured execution date', async () => {
    mockServer([])
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: '녹화 재생' }))
    const banner = within(screen.getByRole('region', { name: '녹화 정보' }))
    expect(banner.getByText('형식 예시')).toBeInTheDocument()
    expect(banner.getByText(/실행일 없음/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }))
    expect(
      await screen.findByText(/이 응답은 실제 서버 실행 결과가 아닙니다/, {
        selector: '.markdown p',
      }),
    ).toBeInTheDocument()
    expect(posts).toHaveLength(0)
    expect(library.recordings).toHaveLength(1)
    expect(library.recordings[0].kind).toBe('format_example')
  })

  it('rejects an unmatched replay question without consuming the recording', async () => {
    mockServer([])
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: '녹화 재생' }))
    await userEvent.clear(screen.getByLabelText('질문 입력'))
    fireEvent.change(screen.getByLabelText('질문 입력'), {
      target: { value: 'TEST: unrecorded input' },
    })
    await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('일치하는 다음 녹화 단계가 없습니다')
    expect(posts).toHaveLength(0)
    await userEvent.clear(screen.getByLabelText('질문 입력'))
    const first = library.recordings[0].steps[0].request
    if (first.endpoint !== '/query') throw new Error('test recording')
    fireEvent.change(screen.getByLabelText('질문 입력'), { target: { value: first.body.question } })
    await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }))
    expect(
      await screen.findByText(/이 응답은 실제 서버 실행 결과가 아닙니다/, {
        selector: '.markdown p',
      }),
    ).toBeInTheDocument()
  })
})
