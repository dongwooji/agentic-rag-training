# Interpreter 개인 Metric 추출: 실제 synthetic 동작 확인

이 결과는 새 synthetic 8문항의 설정별 단일 실행 동작 확인이다. acceptance 또는 일반화 성능 평가가 아니다. 결과를 보고 코드·prompt·질문·기준을 수정하거나 재실행하지 않았다. 기존 acceptance v2는 재실행/수정하지 않았으며 기존 수치를 이 코드의 성능으로 해석하지 않는다.

## 실행 조건과 무결성

- PR 1: `e0718ce67ca3faed39312b5caac4e1c162cfdfbe` (기존 추출 규칙).
- PR 2 실행: `6e08fdbf806a9f34c0a688f085dde1359c0a30f5` (`personal_metric_v1` 명시 선택). 이후 commit은 결과 문서만 추가한다.
- 질문 SHA-256: `6d07cb74db25fc88b7e7577845fede8fe5f1f8ba6735b80c2d779aedc1c76c99`.
- 새 prompt SHA-256: `c87eee554942463eeeab5b70ce1fe551a48565573cb731651c79ac2370ddb73a`.
- 질문은 첫 실제 호출 전에 고정했고, 새 추출 규칙은 실제 결과를 열기 전에 고정했다. 기존 prompt/config는 보존했다.
- 동일 모델 `gpt-5.4-mini-2026-03-17`, 동일 DB/12개 테이블 fingerprint, corpus v1, Final V2 검색 조건, 기존 Grader, Phase A, `/query` debug=true, 단일 턴.
- PR 1 전체 8문항 후 PR 2 전체 8문항. 설정별 문항당 1회, HTTP/SDK 자동 재시도 없음. 모든 응답 HTTP 200, 실행 실패 0.
- 원시 응답·Interpreter JSON·로그·환경/DB hash·RAW_FREEZE는 로컬 `reports/interpreter_extraction_smoke_v1_20261019/`에 보존한다. 공개 비교 JSON에는 개인 기록 값과 최종 답변 전문을 포함하지 않았다.

## 문항별 비교

| 문항 | PR 1 추출 requested_analyses | PR 2 추출 requested_analyses | PR 1 최종 동작 | PR 2 최종 동작 |
|---|---|---|---|---|
| S01 | weekly_volume | weekly_volume | 답변 | 답변 |
| S02 | weekly_frequency | weekly_frequency | 답변 | 답변 |
| S03 | training_gap | training_gap | 답변 | 답변 |
| S04 | estimated_1rm, plateau_candidates | estimated_1rm | 되묻기 | 답변 |
| S05 | first_last_n_session_median_e1rm(N=3) | first_last_n_session_median_e1rm(N=3) | 되묻기 | 되묻기 |
| S06 | weekly_volume, weekly_frequency | weekly_volume, weekly_frequency | 되묻기 | 되묻기 |
| S07 | estimated_1rm, weekly_frequency | estimated_1rm, weekly_frequency | 되묻기 | 되묻기 |
| S08 | training_gap | training_gap | 답변 | 답변 |

- S04: 기존 규칙은 문헌 설명 절을 `plateau_candidates`로 추가 추출했다. 새 규칙은 개인 계산인 `estimated_1rm`만 추출하여 hybrid Tool 실행과 답변까지 진행했다.
- S06·S07: 실제 복수 Metric을 두 설정 모두 보존했다. 기존 단일 Metric 제한에 따른 되묻기를 유지했다.
- S05: 두 설정 모두 N=3과 세션 비교 Metric을 추출했지만 `record_operation=exercise_first_last`가 validator의 Phase A 허용 목록에 걸려 되물었다. binding은 Metric에서 upstream record operation을 파생하지만 그 전에 차단되는 기존 contract 충돌이다. 이번 결과 후에는 수정하지 않았다.
- 요청 종료는 PR 1 답변 4/되묻기 4, PR 2 답변 5/되묻기 3이었다. 이 비율은 정확도나 일반화 성능이 아니다.

## 검증

| 구분 | PR 1 | PR 2 |
|---|---:|---:|
| 관련 단위·통합 | 183 passed; 최종 관련 회귀 126 passed | 152 passed |
| 공개 테스트 | 1,236 passed / 81 deselected | 1,245 passed / 81 deselected |
| 로컬 전체 테스트 | 1,317 passed | 1,326 passed |
| 기존 보호 파일 | 2,081개 변경 없음 | 2,081개 변경 없음 |
| GitHub Actions | Public code CI #61 성공 | 최종 head의 workflow_dispatch 결과는 PR 설명에 기록 |

로컬 수에는 미커밋 사용자 테스트 19개가 포함된다. PR 1 CI는 1,217 passed / 81 deselected / 1 warning이었다. PR 2는 stacked base이므로 master 대상 PR 트리거 대신 기존 CI의 workflow_dispatch를 사용한다. 최종 PR head/CI URL은 PR 설명에서 확인한다.

## LLM·지연 관찰과 제한

- pr1: Interpreter 8회, input 26,941 / cached input 21,504 / output 2,187 token, Interpreter 추정 비용 USD 0.01553205. 전체 요청 지연 합 125.121초.
- pr2: Interpreter 8회, input 29,437 / cached input 23,296 / output 2,179 token, Interpreter 추정 비용 USD 0.01615845. 전체 요청 지연 합 126.265초.

위 token/비용은 저장된 Interpreter 사용량만 포함한다. 후속 query 생성·번역·Grader·Recovery·답변 LLM도 실제 실행되지만 전체 token/비용은 이 관찰기에서 수집하지 않았으므로 합계로 주장하지 않는다. 비용은 기존 config 가격 스냅샷의 추정값이다.
첫 요청에는 초기 runtime/embedding 초기화 지연이 포함된다. 순차 단일 실행이며 후속 LLM도 독립적으로 실행되므로 지연이나 답변 차이를 추출 규칙만의 효과로 단정하지 않는다. 최종 답변의 사실성/수치 해석 품질을 별도로 채점하지 않았다.
S05의 남은 record operation 문제와 최종 답변 품질 문제는 후속 검토 대상이다. 이번 작업에서 함께 고치지 않는다. 실제 복수 Metric 실행, Grader·Recovery 변경, corpus 교체, 서비스 기본값 변경은 시작하지 않았다.

## V2-09 녹화 변환

`reports/frontend_recordings/acceptance_v2_A_V2-09.json`은 저장된 A/V2-09 응답만 변환한 로컬 파일이다. 커밋하지 않았다. 원래 응답과 JSON 내용이 동일하며 프론트엔드 `recordingSchema` 검증을 통과했다. `kind=recorded`, `schema_version=agentic_rag_recording_v1`이다.
`recorded_at=2026-10-19T08:26:55.226643Z`는 원래 응답 파일의 실제 저장/완료 시각이다. 당시 요청 시작 UTC는 저장되지 않아 새 변환 시각을 실행 시각으로 넣거나 시작 시각을 만들어내지 않았다. 출처 hash와 시각 근거는 옆의 `.provenance.json`에 남겼다.
