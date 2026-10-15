import { BookOpen, Check, ChevronRight, GitBranch, RotateCcw, ScanLine, Wrench } from 'lucide-react'
import type { QueryResponse } from '../lib/contracts'
import { interpretationLabel, searchChanges, usedEvidence } from '../lib/evidence'

const toolNames: Record<string, string> = {
  query_training_log: '운동 기록 조회',
  compute_metrics: '지표 계산',
  search_literature: '문헌 검색',
}
export default function EvidencePanel({ response }: { response?: QueryResponse }) {
  const trace = response?.response_metadata?.trace
  const evidence = response ? usedEvidence(response) : []
  const changes = searchChanges(trace)
  return (
    <aside className="evidence-panel" aria-label="실행 근거">
      <div className="panel-heading">
        <span className="eyebrow">BEHIND THE ANSWER</span>
        <h2>
          답변의 근거 <ScanLine size={18} />
        </h2>
        <p>어떤 자료를 확인하고, 어떻게 판단했는지.</p>
      </div>
      {!response ? (
        <div className="evidence-empty">
          <div className="orbit">
            <ScanLine size={30} />
          </div>
          <h3>근거를 함께 살펴보세요</h3>
          <p>
            질문을 보내면 사용한 도구와
            <br />
            문헌, 검증 과정을 여기에 표시합니다.
          </p>
          <div className="empty-steps">
            <span>
              <Wrench size={14} /> 도구 실행
            </span>
            <ChevronRight size={12} />
            <span>
              <BookOpen size={14} /> 근거 확인
            </span>
          </div>
        </div>
      ) : (
        <>
          <section className="panel-section">
            <h3>
              <GitBranch size={16} /> 질문 해석
            </h3>
            <dl className="data-list">
              <div>
                <dt>해석 경로</dt>
                <dd>{interpretationLabel(trace)}</dd>
              </div>
              <div>
                <dt>실행 경로</dt>
                <dd className="mono">{response.route}</dd>
              </div>
              <div>
                <dt>해석 검증</dt>
                <dd>{trace?.interpreter.validation?.status || '응답에 제공되지 않음'}</dd>
              </div>
            </dl>
          </section>
          <section className="panel-section">
            <h3>
              <Wrench size={16} /> 실행된 Tool
            </h3>
            {response.tools_used.length ? (
              <div className="tool-list">
                {response.tools_used.map((tool) => (
                  <div key={tool}>
                    <span className="tool-check">
                      <Check size={12} />
                    </span>
                    <span>
                      {toolNames[tool] || tool}
                      <small>{tool}</small>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">실행된 도구가 없습니다.</p>
            )}
            <div className="inline-metrics">
              <span>
                서버 처리 시간 <strong>{(response.latency_ms / 1000).toFixed(2)}초</strong>
              </span>
              <span>
                재시도 <strong>{response.retry_count}회</strong>
              </span>
            </div>
          </section>
          <section className="panel-section">
            <h3>
              <BookOpen size={16} /> 답변에 사용한 문헌{' '}
              <span className="count">{evidence.length}</span>
            </h3>
            {evidence.length ? (
              <ol className="paper-list">
                {evidence.map((item, index) => (
                  <li key={item.chunk_id || index}>
                    <span className="paper-number">{String(index + 1).padStart(2, '0')}</span>
                    <div>
                      <h4>{item.title || '논문 제목 미제공'}</h4>
                      <p>{item.section || '섹션 미제공'}</p>
                      <details>
                        <summary>출처 식별자</summary>
                        <code>{item.chunk_id}</code>
                      </details>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">답변에 사용된 문헌이 없습니다.</p>
            )}
          </section>
          <section className="panel-section">
            <h3>
              <ScanLine size={16} /> Grader 판정
            </h3>
            {trace?.graders.length ? (
              trace.graders.map((grade, index) => (
                <div className="grade-card" key={index}>
                  <div>
                    <span>
                      {grade.attempt === 0 ? '최초 검색' : '재검색 ' + grade.attempt + '회'}
                    </span>
                    <span
                      className={
                        'grade-tag ' +
                        (grade.execution_status === 'completed'
                          ? grade.evidence_sufficient
                            ? 'supported'
                            : 'missing'
                          : 'failed')
                      }
                    >
                      {grade.execution_status !== 'completed'
                        ? '판정 실패 / 미확인'
                        : grade.evidence_sufficient
                          ? '근거 충분'
                          : '근거 부족'}
                    </span>
                  </div>
                  {grade.components.map((component, i) => (
                    <p key={component.component_id || i}>
                      <span
                        className={
                          component.status === 'supported' ? 'status-dot green' : 'status-dot amber'
                        }
                      />
                      {component.requirement || '요구사항 미제공'}
                      <small>
                        {component.status === 'supported'
                          ? '충족'
                          : component.status === 'missing'
                            ? '미충족'
                            : '미확인'}
                      </small>
                    </p>
                  ))}
                </div>
              ))
            ) : (
              <p className="muted">
                {trace
                  ? '반환된 Grader 판정이 없습니다.'
                  : 'Grader 정보가 응답에 제공되지 않았습니다.'}
              </p>
            )}
          </section>
          <section className="panel-section">
            <h3>
              <RotateCcw size={16} /> Recovery{' '}
              <span className="count">{response.retry_count}회</span>
            </h3>
            {changes.length ? (
              changes.map((change) => (
                <div className="rewrite-card" key={change.attempt}>
                  <span className="eyebrow">
                    {change.attempt}차 재시도 ·{' '}
                    {change.status === 'query_ready' ? '검색어 생성' : change.status}
                  </span>
                  <dl>
                    <dt>전</dt>
                    <dd>{change.before || '검색어 미제공'}</dd>
                    <dt>후</dt>
                    <dd>{change.after || '검색어 미제공'}</dd>
                  </dl>
                </div>
              ))
            ) : (
              <p className="muted">
                {response.recovery_used
                  ? '재시도 상세가 응답에 제공되지 않았습니다.'
                  : '재검색을 수행하지 않았습니다.'}
              </p>
            )}
          </section>
          <details className="panel-section raw-trace">
            <summary>전체 공개 응답 확인</summary>
            <pre>{JSON.stringify(response, null, 2)}</pre>
          </details>
        </>
      )}
      <div className="panel-footer">
        <span className="status-dot green" /> 출처가 있는 답변을 향해
      </div>
    </aside>
  )
}
