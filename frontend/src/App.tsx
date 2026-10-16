import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Activity,
  ArrowDown,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  CircleHelp,
  Clipboard,
  Database,
  FlaskConical,
  Layers,
  LoaderCircle,
  MessageSquare,
  Plus,
  Radio,
  Send,
  Square,
  X,
} from 'lucide-react'

import EvidencePanel from './components/EvidencePanel'
import { executeRequest, readSystem } from './lib/api'
import { appendClarification } from './lib/questions'
import type { ApiRequest, QueryResponse, Recording } from './lib/contracts'
import { library, recordedDate } from './lib/recordings'
import { replayStep } from './lib/recordings'

const MarkdownAnswer = lazy(() => import('./components/MarkdownAnswer'))
type Mode = 'live' | 'replay'
type Turn = {
  id: number
  question: string
  effectiveQuestion: string
  request: ApiRequest
  response?: QueryResponse
  error?: string
  source: { kind: 'live' | Recording['kind']; recordedAt: string | null }
}
const examples = [
  {
    icon: BookOpen,
    type: '문헌 살펴보기',
    question:
      '근력 운동에서 실패 지점까지 수행하는 방식과 여유 반복을 남기는 방식의 차이를 문헌으로 설명해줘.',
  },
  {
    icon: Activity,
    type: '나의 기록 분석',
    question: '2018-04-01부터 2018-06-30까지 내 벤치프레스의 주간 훈련량을 정리해줘.',
  },
  {
    icon: Layers,
    type: '기록과 문헌 연결',
    question:
      '내 스쿼트의 처음 4세션과 최근 4세션 median e1RM을 비교하고, 근력 적응에 관한 문헌과 함께 해석해줘.',
  },
]
const statusLabels = {
  answer_ready: '답변 완료',
  abstain_ready: '답변 보류',
  execution_failure: '실행 실패',
}
export default function App() {
  const [mode, setMode] = useState<Mode>('live')
  const [recordingId, setRecordingId] = useState(library.recordings[0]?.id || '')
  const recording = library.recordings.find((item) => item.id === recordingId)
  const [step, setStep] = useState(0)
  const [turns, setTurns] = useState<Turn[]>([])
  const [activeId, setActiveId] = useState<number>()
  const [draft, setDraft] = useState('')
  const [clarification, setClarification] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [system, setSystem] = useState<{ connected: boolean | null; version: string | null }>({
    connected: null,
    version: null,
  })
  const [systemRefresh, setSystemRefresh] = useState(0)
  const [showEvidence, setShowEvidence] = useState(false)
  const [copiedId, setCopiedId] = useState<number>()
  const [notice, setNotice] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const control = useRef<AbortController | null>(null)
  const nextId = useRef(0)
  const selected = turns.find((turn) => turn.id === activeId)
  const latest = turns.at(-1)
  const metadata = latest?.response?.response_metadata
  const needsClarification = !!metadata?.clarification_required && !latest?.error
  const hasOptions =
    needsClarification && !!metadata?.options?.length && !!metadata.clarification_id
  const nextRecorded = mode === 'replay' ? recording?.steps[step]?.request : undefined
  const validationError = draft.length > 4000 ? '질문은 4,000자까지 입력할 수 있습니다.' : ''

  useEffect(() => {
    if (mode !== 'live') return
    const controller = new AbortController()
    let mounted = true
    readSystem(controller.signal).then((value) => {
      if (mounted) setSystem(value)
    })
    return () => {
      mounted = false
      controller.abort()
    }
  }, [mode, systemRefresh])
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [busy])
  useEffect(() => {
    if (turns.length) bottomRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [turns, busy])
  useEffect(() => () => control.current?.abort(), [])

  function reset(nextMode: Mode, nextRecording = recordingId) {
    setMode(nextMode)
    setRecordingId(nextRecording)
    setTurns([])
    setActiveId(undefined)
    setStep(0)
    setClarification('')
    setNotice('')
    const first = library.recordings.find((item) => item.id === nextRecording)?.steps[0].request
    setDraft(nextMode === 'replay' && first?.endpoint === '/query' ? first.body.question : '')
    inputRef.current?.focus()
  }
  async function run(request: ApiRequest, question: string, effectiveQuestion: string) {
    if (busy || control.current) return
    if (effectiveQuestion.length > 4000) {
      setNotice('원 질문과 추가 답변의 합계는 4,000자 이하여야 합니다. 질문을 짧게 정리해 주세요.')
      return
    }
    let replayResponse: QueryResponse | undefined
    if (mode === 'replay') {
      try {
        if (!recording) throw new Error('선택한 녹화 파일을 찾을 수 없습니다.')
        replayResponse = replayStep(recording, step, request)
      } catch (error) {
        setNotice(error instanceof Error ? error.message : '녹화를 재생할 수 없습니다.')
        return
      }
    }
    const id = ++nextId.current
    const controller = new AbortController()
    control.current = controller
    setBusy(true)
    setElapsed(0)
    setNotice('')
    setClarification('')
    setDraft('')
    const turn: Turn = {
      id,
      question,
      effectiveQuestion,
      request,
      source: {
        kind: mode === 'live' ? 'live' : recording?.kind || 'format_example',
        recordedAt: mode === 'replay' ? recording?.recorded_at || null : null,
      },
    }
    setTurns((items) => [...items, turn])
    setActiveId(id)
    let timedOut = false
    const timeout = window.setTimeout(() => {
      timedOut = true
      controller.abort()
    }, 180_000)
    try {
      const response =
        mode === 'live' ? await executeRequest(request, controller.signal) : replayResponse
      if (!response) throw new Error('선택한 녹화 파일을 찾을 수 없습니다.')
      setTurns((items) => items.map((item) => (item.id === id ? { ...item, response } : item)))
      if (mode === 'replay') setStep((value) => value + 1)
    } catch (error) {
      const message = controller.signal.aborted
        ? timedOut
          ? '응답 대기 시간이 초과되었습니다. 서버 실행은 계속될 수 있습니다.'
          : '화면 대기를 중지했습니다. 서버 실행은 계속될 수 있습니다.'
        : error instanceof Error && error.message !== 'Failed to fetch'
          ? error.message
          : '서버에 연결하지 못했습니다. API 실행 상태를 확인해 주세요.'
      setTurns((items) =>
        items.map((item) => (item.id === id ? { ...item, error: message } : item)),
      )
      if (request.endpoint === '/query') setDraft(request.body.question)
    } finally {
      window.clearTimeout(timeout)
      control.current = null
      setBusy(false)
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (question && !validationError)
      void run({ endpoint: '/query', body: { question, debug: true } }, question, question)
  }
  function submitClarification(event: FormEvent) {
    event.preventDefault()
    if (!latest || !clarification.trim()) return
    const combined = appendClarification(latest.effectiveQuestion, clarification)
    void run(
      { endpoint: '/query', body: { question: combined, debug: true } },
      clarification.trim(),
      combined,
    )
  }
  async function copyAnswer(turn: Turn) {
    try {
      await navigator.clipboard.writeText(turn.response?.answer_text || '')
      setCopiedId(turn.id)
      window.setTimeout(() => setCopiedId(undefined), 2000)
    } catch {
      setNotice('복사할 수 없습니다. 답변을 선택해 복사해 주세요.')
    }
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#main" aria-label="Groundwork 시작">
          <span className="brand-mark">
            <Layers size={20} strokeWidth={2} />
          </span>
          <span>
            groundwork<span className="brand-dot">.</span>
          </span>
        </a>
        <span className="workspace-label">TRAINING INTELLIGENCE</span>
        <button className="new-chat" disabled={busy} onClick={() => reset(mode)}>
          <Plus size={17} /> 새 질문 시작 <span>↗</span>
        </button>
        <nav aria-label="주 메뉴">
          <span className="nav-item active">
            <MessageSquare size={17} /> 질문과 답변
            <span className="tiny-dot" />
          </span>
        </nav>
        <div className="history-heading">
          이번 세션 <span>{turns.length}</span>
        </div>
        <div className="history">
          {turns.map((turn) => (
            <button
              key={turn.id}
              className={turn.id === activeId ? 'selected' : ''}
              onClick={() => {
                setActiveId(turn.id)
                document
                  .getElementById('turn-' + turn.id)
                  ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
              }}
            >
              <MessageSquare size={14} />
              <span>{turn.question}</span>
            </button>
          ))}
          {!turns.length && (
            <p>
              질문을 시작하면
              <br />
              여기에 이력이 쌓입니다.
            </p>
          )}
        </div>
        <div className="sidebar-note">
          <span className="mini-icon">
            <Database size={17} />
          </span>
          <strong>
            기록에서 발견하고,
            <br />
            문헌으로 확인하세요.
          </strong>
          <p>
            운동 데이터와 연구 근거를
            <br />
            하나의 답변으로 연결합니다.
          </p>
        </div>
        <div className="sidebar-bottom">
          <span className="avatar">G</span>
          <span>
            로컬 데모<small>현재 세션에만 이력 보관</small>
          </span>
          <span className="version-label">v0.1</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <span className="breadcrumb">워크스페이스</span>
            <span className="slash">/</span>
            <strong>질문과 답변</strong>
            <span className="beta-label">DEMO</span>
          </div>
          <div className="header-actions">
            <span
              className="system-status"
              title="API liveness만 확인합니다. DB·LLM 준비 상태를 보장하지 않습니다."
            >
              <span
                className={
                  'status-dot ' +
                  (mode === 'replay' ? 'amber' : system.connected ? 'green' : 'gray')
                }
              />
              {mode === 'replay'
                ? '로컬 재생'
                : system.connected === null
                  ? 'API 확인 중'
                  : system.connected
                    ? 'API 연결됨'
                    : 'API 연결 안 됨'}
              {mode === 'live' && system.version && <small>v{system.version}</small>}
            </span>
            {mode === 'live' && !system.connected && (
              <button
                className="icon-button"
                aria-label="API 연결 다시 확인"
                onClick={() => setSystemRefresh((value) => value + 1)}
              >
                <Radio size={16} />
              </button>
            )}
            <button className="mobile-evidence" onClick={() => setShowEvidence(true)}>
              <ScanIcon /> 근거
            </button>
          </div>
        </header>
        <div className="workspace">
          <main id="main" className="chat-main">
            <div className="mode-bar">
              <div className="mode-switch" aria-label="실행 모드">
                <button
                  disabled={busy}
                  aria-pressed={mode === 'live'}
                  className={mode === 'live' ? 'active' : ''}
                  onClick={() => {
                    if (mode !== 'live') reset('live')
                  }}
                >
                  <Radio size={14} /> 실제 서버
                </button>
                <button
                  disabled={busy}
                  aria-pressed={mode === 'replay'}
                  className={mode === 'replay' ? 'active' : ''}
                  onClick={() => {
                    if (mode !== 'replay') reset('replay')
                  }}
                >
                  <FlaskConical size={14} /> 녹화 재생
                </button>
              </div>
              <span className="session-note">운동 기록 × 연구 문헌</span>
            </div>
            {mode === 'replay' && (
              <section className="replay-banner" aria-label="녹화 정보">
                <div>
                  <span className="recording-tag">
                    {recording?.kind === 'recorded' ? '녹화된 응답' : '형식 예시'}
                  </span>
                  <span>
                    {recordedDate(recording?.recorded_at || null)}
                    {recording?.recorded_at ? ' 실행 · 한국 시간' : ' · 실제 서버 실행 아님'}
                  </span>
                </div>
                <label>
                  재생 파일
                  <select
                    value={recordingId}
                    disabled={busy}
                    onChange={(event) => reset('replay', event.target.value)}
                  >
                    {library.recordings.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.kind === 'format_example' ? '[형식 예시] ' : '[녹화된 응답] '}
                        {item.title}
                      </option>
                    ))}
                  </select>
                </label>
                {nextRecorded && (
                  <p>
                    다음 입력:{' '}
                    <span>
                      {nextRecorded.endpoint === '/query'
                        ? nextRecorded.body.question
                        : '후보 ' + nextRecorded.body.selected_option + ' 선택'}
                    </span>
                  </p>
                )}
                {!!turns.length && (
                  <button className="text-button" disabled={busy} onClick={() => reset('replay')}>
                    처음부터 재생
                  </button>
                )}
                {!recording && <p>사용 가능한 녹화 파일이 없습니다.</p>}
                {!!library.errors.length && (
                  <p role="alert">형식을 확인할 수 없는 파일: {library.errors.join(', ')}</p>
                )}
              </section>
            )}
            <div className="conversation">
              {!turns.length ? (
                <div className="welcome">
                  <span className="section-kicker">
                    <span className="short-line" /> EVIDENCE, NOT GUESSWORK
                  </span>
                  <div className="hero-symbol" aria-hidden="true">
                    <div className="symbol-grid">
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                    </div>
                    <div className="symbol-line" />
                    <span>
                      <Activity size={24} />
                    </span>
                  </div>
                  <h1>
                    더 나은 훈련의 시작,
                    <br />
                    <span>근거 있는 질문에서.</span>
                  </h1>
                  <p className="welcome-description">
                    나의 운동 기록과 연구 문헌을 연결해 보세요.
                    <br />
                    답변뿐 아니라, 답변에 이르는 근거까지 확인할 수 있습니다.
                  </p>
                  {mode === 'live' ? (
                    <>
                      <div className="suggestion-heading">
                        이렇게 질문해 보세요 <ArrowDown size={13} />
                      </div>
                      <div className="suggestions">
                        {examples.map(({ icon: Icon, type, question }) => (
                          <button
                            key={type}
                            onClick={() => {
                              setDraft(question)
                              inputRef.current?.focus()
                            }}
                          >
                            <span className="suggestion-top">
                              <Icon size={17} />
                              <span>{type}</span>
                              <ArrowRight size={16} />
                            </span>
                            <span className="suggestion-question">{question}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="replay-intro">
                      {recording?.kind === 'format_example'
                        ? '현재는 파일 형식을 보여주는 예시 1개만 있습니다. 입력된 질문을 전송해 구조를 확인하세요.'
                        : '아래 질문을 전송하면 실제 서버에서 저장한 응답을 재생합니다.'}
                    </p>
                  )}
                  <div className="welcome-foot">
                    <span>
                      <Check size={13} /> 사용한 근거 추적
                    </span>
                    <span>
                      <Check size={13} /> 불확실하면 확인 요청
                    </span>
                    <span>
                      <Check size={13} /> 한국어 질문 지원
                    </span>
                  </div>
                </div>
              ) : (
                <div className="turn-list">
                  {turns.map((turn) => (
                    <article key={turn.id} id={'turn-' + turn.id} className="turn">
                      <div className="user-question">
                        <span className="user-avatar">나</span>
                        <div>
                          <span className="message-label">질문</span>
                          <p>{turn.question}</p>
                          {turn.request.endpoint === '/query' &&
                            turn.question !== turn.effectiveQuestion && (
                              <details>
                                <summary>추가 답변을 포함한 제출 질문</summary>
                                <p>{turn.effectiveQuestion}</p>
                              </details>
                            )}
                        </div>
                      </div>
                      <div className={'answer-card ' + (turn.id === activeId ? 'focused' : '')}>
                        <div className="answer-heading">
                          <span className="answer-mark">
                            <Layers size={16} />
                          </span>
                          <strong>Groundwork</strong>
                          {turn.source.kind !== 'live' && (
                            <span className="recording-tag">
                              {turn.source.kind === 'recorded' ? '녹화된 응답' : '형식 예시'}
                            </span>
                          )}
                          {turn.response && (
                            <span className={'answer-status ' + turn.response.final_status}>
                              {turn.response.response_metadata?.clarification_required
                                ? '추가 확인 필요'
                                : statusLabels[turn.response.final_status]}
                            </span>
                          )}
                        </div>
                        {turn.source.kind !== 'live' && (
                          <p className="source-date">
                            {recordedDate(turn.source.recordedAt)}
                            {turn.source.kind === 'format_example'
                              ? ' · 실제 실행 결과가 아닙니다.'
                              : ' 실행 · 한국 시간'}
                          </p>
                        )}
                        {turn.response ? (
                          <>
                            <div className="markdown">
                              <Suspense fallback={<p>{turn.response.answer_text}</p>}>
                                <MarkdownAnswer text={turn.response.answer_text} />
                              </Suspense>
                            </div>
                            {!!turn.response.limitations.length && (
                              <div className="limitations">
                                <CircleHelp size={15} />
                                <div>
                                  <strong>확인할 점</strong>
                                  <ul>
                                    {turn.response.limitations.map((text, index) => (
                                      <li key={index}>{text}</li>
                                    ))}
                                  </ul>
                                </div>
                              </div>
                            )}
                            {turn.response.error && (
                              <p className="error-text" role="alert">
                                {turn.response.error.message}
                              </p>
                            )}
                            <div className="answer-actions">
                              <button onClick={() => void copyAnswer(turn)}>
                                {copiedId === turn.id ? (
                                  <Check size={14} />
                                ) : (
                                  <Clipboard size={14} />
                                )}
                                {copiedId === turn.id ? '복사됨' : '답변 복사'}
                              </button>
                              <button
                                onClick={() => {
                                  setActiveId(turn.id)
                                  setShowEvidence(true)
                                }}
                              >
                                <BookOpen size={14} /> 실행 근거 보기 <ArrowRight size={13} />
                              </button>
                            </div>
                          </>
                        ) : turn.error ? (
                          <div className="request-error">
                            <p role="alert">{turn.error}</p>
                            {turn.request.endpoint === '/query/clarify' ? (
                              <button
                                className="text-button"
                                disabled={busy}
                                onClick={() =>
                                  void run(
                                    {
                                      endpoint: '/query',
                                      body: { question: turn.effectiveQuestion, debug: true },
                                    },
                                    turn.effectiveQuestion,
                                    turn.effectiveQuestion,
                                  )
                                }
                              >
                                원 질문 다시 보내기
                              </button>
                            ) : (
                              <p className="muted">
                                입력칸의 질문을 확인한 후 다시 전송할 수 있습니다.
                              </p>
                            )}
                          </div>
                        ) : (
                          <div className="pending" role="status">
                            <LoaderCircle size={18} className="spin" />
                            <span>
                              기록과 근거를 확인하고 있습니다.
                              <small>완료된 결과를 한 번에 표시합니다. · {elapsed}초 경과</small>
                            </span>
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {needsClarification && !busy && (
                <section className="clarification-box" aria-label="추가 확인">
                  <h2>
                    <CircleHelp size={17} /> 답변을 위한 추가 확인
                  </h2>
                  {hasOptions ? (
                    <>
                      <p>조회할 운동을 선택해 주세요.</p>
                      <div className="option-list">
                        {metadata.options!.map((option) => (
                          <button
                            key={option.id}
                            onClick={() => {
                              if (latest && metadata.clarification_id)
                                void run(
                                  {
                                    endpoint: '/query/clarify',
                                    body: {
                                      clarification_id: metadata.clarification_id,
                                      selected_option: option.id,
                                      debug: true,
                                    },
                                  },
                                  option.label,
                                  latest.effectiveQuestion,
                                )
                            }}
                          >
                            {option.label}
                            <ArrowRight size={15} />
                          </button>
                        ))}
                      </div>
                    </>
                  ) : (
                    <form onSubmit={submitClarification}>
                      <p>기간이나 조건을 알려주시면 원 질문에 덧붙여 다시 확인합니다.</p>
                      <label htmlFor="clarification">추가 답변</label>
                      <textarea
                        id="clarification"
                        value={clarification}
                        onChange={(event) => setClarification(event.target.value)}
                        placeholder="예: 2018-04-01부터 2018-06-30까지 확인해 주세요."
                        rows={3}
                      />
                      <button
                        className="primary-button"
                        disabled={!clarification.trim()}
                        type="submit"
                      >
                        추가 답변 보내기 <ArrowRight size={15} />
                      </button>
                    </form>
                  )}
                  {metadata.expires_at && (
                    <p className="expiry">
                      후보 선택 유효 기한: {recordedDate(metadata.expires_at)} (한국 시간)
                      {mode === 'replay' ? ' · 녹화 재생에는 실제 만료를 적용하지 않습니다.' : ''}
                    </p>
                  )}
                </section>
              )}
              <div ref={bottomRef} />
            </div>
            <div className="composer-wrap">
              {notice && (
                <p className="notice" role="alert">
                  {notice}
                </p>
              )}
              <form className={'composer ' + (validationError ? 'invalid' : '')} onSubmit={submit}>
                <label htmlFor="question" className="sr-only">
                  질문 입력
                </label>
                <textarea
                  ref={inputRef}
                  id="question"
                  placeholder={
                    mode === 'replay'
                      ? '녹화된 질문을 입력하세요'
                      : '운동 기록이나 궁금한 훈련 원리를 질문해 보세요'
                  }
                  rows={2}
                  value={draft}
                  disabled={busy}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter' &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault()
                      if (!busy && draft.trim() && !validationError)
                        event.currentTarget.form?.requestSubmit()
                    }
                  }}
                />
                <div className="composer-bottom">
                  <span>
                    <span className="status-dot green" />{' '}
                    {mode === 'live'
                      ? '근거를 확인하는 AI'
                      : recording?.kind === 'format_example'
                        ? '형식 예시 재생'
                        : '녹화된 응답 재생'}
                  </span>
                  <div>
                    <small>{draft.length.toLocaleString()} / 4,000</small>
                    {busy ? (
                      <button
                        type="button"
                        className="send-button stop"
                        aria-label="응답 대기 중지"
                        onClick={() => control.current?.abort()}
                      >
                        <Square size={16} />
                      </button>
                    ) : (
                      <button
                        type="submit"
                        className="send-button"
                        disabled={
                          !draft.trim() || !!validationError || (mode === 'replay' && !recording)
                        }
                        aria-label="질문 보내기"
                      >
                        <Send size={17} />
                      </button>
                    )}
                  </div>
                </div>
              </form>
              {validationError && (
                <p className="error-text" role="alert">
                  {validationError}
                </p>
              )}
              <p className="composer-footnote">
                Enter 전송 · Shift + Enter 줄바꿈{' '}
                <span>질문별 독립 실행 · 답변의 근거와 제한사항을 확인하세요.</span>
              </p>
            </div>
          </main>
          <div className={'evidence-container ' + (showEvidence ? 'is-open' : '')}>
            <button
              className="close-evidence icon-button"
              aria-label="실행 근거 닫기"
              onClick={() => setShowEvidence(false)}
            >
              <X size={20} />
            </button>
            <EvidencePanel response={selected?.response} />
          </div>
          {showEvidence && (
            <button
              className="evidence-backdrop"
              aria-label="실행 근거 닫기 배경"
              onClick={() => setShowEvidence(false)}
            />
          )}
        </div>
        <footer className="app-footer">
          <span>GROUNDWORK LAB</span>
          <span>개인 기록과 연구를 잇는 작은 실험.</span>
          <ChevronDown size={12} />
        </footer>
      </div>
    </div>
  )
}
function ScanIcon() {
  return <BookOpen size={16} />
}
