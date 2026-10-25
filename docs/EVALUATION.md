# 평가와 한계

검색이 필요한 근거를 찾는지, 실행 제어가 적절한 Tool과 최종 동작을 선택하는지, 문헌 근거 검사가 답변과 보류를 올바르게 구분하는지를 평가했습니다. 아래 결과는 각 절에 명시한 과거 코드·데이터·설정의 결과입니다. 이후 수정된 현재 코드의 성능으로 일괄 적용하지 않습니다.

| 평가 | 조건과 주요 결과 | 결정과 해석 범위 |
|---|---|---|
| Router·초기 E2E | 반복 분석한 30문항에서 Tool 집합 정확 일치: 규칙 Router 29/30, LLM Planner 24/30 | 규칙 Router를 유지했습니다. 독립 일반화 평가가 아닙니다. |
| Retrieval v2 | corpus v2의 24문항 중 근거가 필요한 20문항에서 H0 / Final V2 EGR@10은 0.188333 / 0.208333, CE@10은 모두 1/20 | 일부 검색 지표 차이는 있으나 근거 전체 회수는 제한적입니다. corpus v1 개발 평가와 직접 비교하지 않습니다. |
| Acceptance v1 | 같은 Phase A에서 수정 전후 각 20문항의 최종 동작 정확도 5/20 → 10/20, HTTP 500은 0 → 3건 | 동작 계약 개선과 공개 API 실패를 구분합니다. REST 안정성 통과는 아닙니다. |
| Grader Acceptance v2 | 같은 Phase A에서 기존 / 신규 Grader 각 21문항의 최종 동작 정확도 14/21 / 12/21 | 고정 채택 기준 미충족으로 신규 Grader v1.1을 채택하지 않았습니다. |
| Reranker | corpus v1의 같은 18문항을 두 언어로 평가한 36회 측정 | 두 후보 모두 비채택했습니다. 후보 확대 효과·재정렬 효과·CPU 지연을 구분했습니다. |

각 실험의 지표 정의, 버전, 원본 링크와 실패 사례는 아래 상세 절에 유지했습니다. **`answer_ready`는 답변 정확도가 아니며, 검색 EGR/CE와 최종 동작 정확도도 서로 다른 지표입니다.** 답변 문장 전체에 대한 독립적인 사람 평가는 수행하지 않았습니다.

이 문서는 세 종류의 검증을 구분합니다.

- **연구·성능 평가:** 아래 Router·Retrieval·Grader·E2E 실험입니다.
- **코드 검증:** 단위·통합·회귀 테스트와 공개 CI입니다. 테스트 통과 수는 답변 품질을 나타내지 않습니다.
- **실제 연결 검증:** PostgreSQL·LLM을 연결한 실행 확인입니다. 하단에 실행 시점과 범위를 별도로 기록했습니다.

현재 서버의 질문 해석 기본값은 `legacy`이며 `phase_a`는 선택형입니다. 두 모드는 [실행 설정](SETUP.md#api-실행)으로 구분합니다. Acceptance v1은 Phase A 내 수정 전후 비교, v2는 Phase A 내 Grader 비교입니다. **Legacy Router/Resolver 경로와 Phase A 경로의 직접 비교 결과를 뜻하지 않습니다.** 현재 검색은 corpus v1의 Step 3b 설정이며, 사후 선택 과정은 Retrieval v2 절에 설명합니다.

## 평가 정의

- Retrieval: EGR@10은 문항별 required evidence group 충족 비율의 평균, CE@10은 모든 required group 충족 여부의 평균입니다. MRR은 첫 required chunk 순위의 역수입니다. required group이 없는 문항은 해당 지표에서 제외합니다.
- Grader: Gold CE와 충분성 판정의 불일치와 실행 실패를 구분합니다. chunk Gold 일치가 의미적 충분성 전체를 대신하지 않습니다.
- Acceptance: source·실제 Tool operation·최종 동작 계약을 채점합니다. 최종 동작 정확도는 답변 문장 전체의 사실 정확도와 다릅니다.
- Gold는 실행 종료 후 평가에서만 읽습니다. corpus coverage, 검색 누락, 질문 해석, Metric, Grader, Recovery, 답변, API 경계 실패를 별도로 분석합니다.

## 초기 평가: Retrieval v1·Router·E2E

동일 frozen 30문항을 반복 분석한 개발·통합 비교입니다. Router의 Tool 집합 정확 일치는 29/30, LLM Planner는 24/30이었습니다. Grader v2.1은 12건 중 valid 4, INVALID 8로 exact quote·anchor 규약의 실행 안정성이 부족했습니다. 현재 Runtime Grader는 exact quote를 강제하지 않고 chunk membership과 deterministic finalizer를 사용합니다.

| 지표 | E2E v1 | E2E v2 |
|---|---:|---:|
| answer_ready | 13/30 | 19/30 |
| execution_failure | 4/30 | 1/30 |
| Hybrid answer_ready | 0/8 | 4/8 |
| Log/Metric answer_ready | 4/6 | 6/6 |
| Unanswerable abstention | 6/6 | 6/6 |
| Gold 기준 unsafe answer | 2 | 5 |

v2는 세 가지 통합 결함 수정 후의 과거 평가입니다. 문헌형 18문항의 Initial CE@10은 10/18, Final CE@10은 11/18이며 EGR@10은 0.660185 → 0.715741입니다. unsafe는 answer_ready이면서 Gold final CE=false라는 정의로, 답변의 모든 문장을 의미 채점한 값이 아닙니다. 독립 일반화 성능이나 현재 코드 성능으로 해석하지 않습니다. [원본 요약](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/HISTORY.md).

## Retrieval v2: 선택과 별도 평가

초기 Retrieval v1 진단에서는 488개 청크 중 462개(94.67%)가 MiniLM의 128토큰 입력 한도를 넘었습니다. 또 문헌 포함 18문항 중 순수 한글 질문 1건에서 BM25 점수가 모두 0인데도 Top-10이 반환되어, Dense 밖 청크 5개가 Hybrid Top-10에 들어왔습니다. [당시 진단](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/HISTORY.md)

현재 [서비스 검색 설정](../config/retrieval_step3b_translation_v1.json)은 BM25 `positive_only`를 사용합니다. [BM25 구현](../src/retrieval/bm25.py)은 점수가 양수인 후보만 반환하므로 0점 후보의 RRF 기여를 막습니다. 다만 parent 청크와 입력 한도 128은 유지합니다. 이 조치를 긴 청크 문제의 해결이나 전체 검색 품질 개선으로 확대 해석하지 않습니다.

corpus v1의 dev/dev-ko는 같은 18문항의 언어별 표현입니다. 사전 기준에서는 Step 2를 채택하고 Step 3a·3b는 제외했습니다. 이후 별도의 사후 결정으로 Step 3b를 Final V2 비교 기준으로 선택했습니다. 이 사후 선택을 사전 기준 통과로 바꾸어 설명하지 않습니다. 현재 서비스에는 corpus v1에 결합한 Step 3b를 사용하며, 과거 보고서의 당시 기본값 legacy/H0와 구분합니다. [선택 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_STEP23_RESULTS.md), [최종 결정 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/decisions/RETRIEVAL_V2_FINAL.md).

별도 corpus v2의 held-out은 24문항 중 required evidence가 있는 20문항을 분모로 합니다. 설정·검색어·네 조건의 순위를 모두 동결한 뒤 Gold로 평가했습니다.

| 설정 | EGR@10 | CE@10 | MRR |
|---|---:|---:|---:|
| H0 | 0.188333 | 0.050000 | 0.196389 |
| H1 | 0.200833 | 0.050000 | 0.201389 |
| Step 2 | 0.200833 | 0.050000 | 0.201389 |
| Final V2 | 0.208333 | 0.050000 | 0.211865 |

CE는 모두 1/20입니다. corpus가 다른 dev 수치를 같은 조건의 전후 비교로 읽지 않습니다. 이 결과만으로 서비스 전환의 충분한 성능 근거가 되지 않으며, 답변 생성·Grader·Recovery·실제 REST는 이 검색 평가에서 실행하지 않았습니다. [원본 결과](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_FINAL_RESULTS.md).

동결 순위 사후 진단에서 Final V2는 71개 required group 중 16개를 회수하고 55개를 놓쳤습니다. 정답 논문 하나 이상 적중은 18/20, 모든 정답 논문 적중은 17/20이지만, 16문항은 정답 논문을 모두 찾고도 필요한 근거가 누락됐습니다. 저장된 후보 범위에서 누락 3개는 포함을 확인했고 나머지 52개는 판정 불가입니다. unknown을 후보 밖 또는 corpus gap으로 처리하지 않습니다. 새 검색 없이 한 진단이며 원인·개선 효과를 확정하지 않습니다. [진단 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_HELDOUT_DIAGNOSIS.md).

별도의 운영 재현성 검사(2026-10-12)는 corpus v1의 동일 동결 query에서 offline ↔ pgvector Top-10 ID·순서가 36/36 일치했습니다. corpus v2 held-out 재현이나 성능 재평가가 아닙니다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/SERVICE_RETRIEVAL_V2_REPRODUCIBILITY.md).

## Acceptance v1

baseline `685e2b9194419fbf757df2df2156299b77e4afe3`, changed `6c68ea996f967bcdda47bfed2671aae1ae583969`에서 Phase A로 각 20문항을 실제 REST 실행했습니다. 원시 결과를 동결한 뒤 Gold를 열었습니다.

| 필수 기준 | baseline | changed |
|---|---:|---:|
| required-channel omission | 6 | 0 |
| expected-answer의 부당한 clarification/abstain | 12 | 9 |
| final behavior accuracy | 5/20 (25%) | 10/20 (50%) |
| graph-state execution_failure | 0 | 0 |

고정 scorer 기준 PASS지만 서비스 REST 안정성 통과는 아닙니다. HTTP 500은 0→3건으로 늘었습니다. graph observer가 공개 응답 변환 실패를 집계하지 못한 한계를 별도로 보존합니다. 원인은 최근 8주 metadata와 공개 schema의 충돌이었습니다. 이후 수정한 현재 코드의 성능 수치로 재사용하지 않습니다. optional 문헌의 blocking, 불필요한 운동·기간·Metric 확인이 남았습니다. 단일 20문항 실행이며 독립 사람의 답변 전체 평가가 아닙니다. [원본 보고서](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_evidence_acceptance_v1/REPORT.md), [상세 판정](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_evidence_acceptance_v1/ACCEPTANCE.md).

## Grader v1·v1.1 비채택과 Acceptance v2

고정 요구사항 Grader v1은 `454177751180e4a88853e1a3e09e40899e6ef0c3`에서 dev/dev-ko 36입력을 평가했습니다. A=CE=1인데 부족, B=CE=0인데 충분입니다. A+B는 12→7로 줄었지만 B는 4→6, 실행 실패는 0→11로 늘어 비채택입니다. 그중 7건은 문헌 질문 출처 검증 실패로 LLM을 호출하지 않았습니다. 새 Recovery 켬/끔은 최종 EGR/CE가 모두 0.752778/0.666667로 같아 기존 비감소 기준을 충족했지만 개선 효과를 입증하지 않았습니다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/grader_fixed_requirements_v1/REPORT.md).

입력 경계를 수정한 v1.1은 **별도 SHA `992b495024e2f3a5b2db2abd3890e9a4f356a094`**에서 Acceptance v2로 비교했습니다. 2026-10-19, 같은 DB·corpus v1에서 Phase A A=legacy Grader / B=fixed_requirements_v1_1, 21문항씩 실제 REST 단일 실행입니다. 여기서 legacy는 Grader 버전의 이름이며, 질문 해석 경로를 비교한 실험이 아닙니다.

| 고정 채택 조건 | A | B | 통과 |
|---|---:|---:|---|
| answerable 보류 수 엄격 감소 (분모 10) | 0 | 1 | 아니오 |
| unanswerable 답변 수 비증가 (분모 4) | 0 | 1 | 아니오 |
| HTTP 포함 최종 실행 실패 비증가 (분모 21) | 0 | 0 | 예 |
| 최종 동작 정확도 비감소 | 14/21 (66.67%) | 12/21 (57.14%) | 아니오 |

`eligible_for_adoption=false`, `service_default_changed=false`입니다. 양쪽 HTTP 200은 21/21, 답변/보류/되묻기는 각각 9/4/8입니다. 부당한 되묻기·보류는 6→7건이며, answerable 보류 지표에는 clarification이 포함되지 않습니다. A의 0을 모든 답변 가능 요청 처리로 해석하지 않습니다.

V2-07은 B만 unanswerable에 답변했고, V2-10은 B만 answerable을 보류했습니다. 7문항은 양쪽 모두 Grader 전 확인 요청에서 멈췄습니다. 영어 query·Top-10은 10개 중 8개만 같아 순수 Grader의 인과 효과를 뜻하지 않습니다. B 판정 16회가 모두 completed였어도 과거 실패 11건의 재평가는 아닙니다. 전체 token·비용은 observer 미기록으로 N/A이며 0으로 채우지 않습니다. [v2 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_acceptance_v2_20261019/REPORT.md).

## Reranker 비채택

2026-10-10 corpus v1 dev/dev-ko 36회 측정이며, 같은 18문항의 언어 쌍입니다. CPU에서 동결 후보를 재정렬했고 held-out은 사용하지 않았습니다.

| 조건 | EGR@10 | CE@10 | MRR | CPU 평균(초) |
|---|---:|---:|---:|---:|
| Step 3b / RRF10 | 0.752778 | 0.666667 | 0.432011 | — |
| RRF50 | 0.787500 | 0.666667 | 0.494709 | — |
| A reranker10 | 0.715741 | 0.611111 | 0.503704 | 4.443 |
| B reranker50 | 0.759259 | 0.694444 | 0.531779 | 21.252 |

A는 성능·지연 기준 모두 미충족, B는 Step 3b 대비 일부 성능 기준을 충족했지만 CPU 평균 3초 제한을 넘어 모두 비채택입니다. B는 같은 RRF50 후보 대비 평균 EGR이 낮아 후보 확대 효과와 재정렬 효과를 구분합니다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_RERANKER_RESULTS.md).

## Recovery 저장 trace 관찰

2026-10-16에 서로 다른 버전·조건의 저장 실행 230건을 사후 분석했습니다. 문헌 검색 55건, Recovery 발동 17건, 시도 32회, 재검색·Grader 재검사 26회였습니다. Grader 부족→충분은 2건이지만 최종 answer_ready는 1건입니다. 나머지 전환 1건은 답변 grounding 검증에서 실패했습니다.

문헌 Gold가 있는 과거 E2E에서 최종 EGR/CE가 개선된 실행은 v2 HYB-006 한 건입니다. 해당 실행도 최종 Grader는 부족으로 보류했습니다. v1에서는 같은 문항의 중간 근거가 다음 Top-10 결합에서 사라졌습니다. Grader 판정 전환·Gold 근거 개선·최종 답변 성공은 서로 다른 지표이며, 이 혼합 자료를 현재 Recovery의 일반 성공률로 표현하지 않습니다. [관찰 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/analysis/recovery_saved_traces_v1/REPORT.md).

## 남은 한계

MiniLM 입력 한도에 비해 긴 chunk, 문헌 내 필요한 문단 누락, 운동·기간·Metric 의미 해석, optional 문헌의 blocking이 남아 있습니다. 답변 문장 전체의 과학적 정확성·개인 조언 적절성에 대한 독립 사람 평가는 수행하지 않았습니다. 작은 평가셋, 반복 분석, 단일 확률적 실행과 cache·순서 영향을 고려해야 합니다. 인증·사용자 격리와 배포 운영 검증도 별도 과제입니다.

상세 평가 질문·Gold·원본 결과·결정은 [archive-pre-cleanup](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup)에 보존합니다. 이 tag는 정리 전 tracked master만 보존하며 로컬 ignored 자산을 포함하지 않습니다. 기존 frozen 자산과 역사적 결과를 덮어쓰지 않았고 새 연구 성능은 측정하지 않았습니다.

## 코드 검증과 실제 연결 검증 기록

공개 CI는 실제 DB·OpenAI 없이 통제된 응답과 합성 입력으로 기능·계약을 검사합니다. 로컬 전체 테스트는 동결 자산 검사도 포함합니다. 실행 방법은 [SETUP.md](SETUP.md#테스트와-프론트엔드)에 있습니다. 아래는 특정 시점의 검증 기록이며 현재 실행 환경의 연결 상태를 보장하지 않습니다.

### 저장소 정리 당시의 회귀 검증

2026년 10월 21일 병합된 [PR #33](https://github.com/dongwooji/agentic-rag-training/pull/33)은 정리 전 기준 `17654f70781c565d8ee2434908a62008dda46674`와 비교했습니다. Windows 작업 폴더의 공개 테스트는 1,257 → 1,076개, 로컬 전체 테스트는 1,338 → 1,157개가 통과했습니다. 이 수에는 미추적 감사 테스트 19개가 포함되며, tracked 테스트 수집 수는 1,319 → 1,138개였습니다. 감소한 181개는 제거한 과거 실험 전용 검사입니다. 상세 삭제·보존 목록과 CI 결과는 해당 PR에서 확인할 수 있습니다.

프론트엔드 작업 폴더에서는 ignored 실제 녹화와 시간 초과로 3개 테스트가 실패했습니다. 해당 자료를 보존한 채 tracked 파일만 복사한 환경에서는 lint·29개 테스트·build가 통과했습니다. Python 검사에는 Starlette deprecation warning 1건이 있었습니다. 이 결과는 특정 환경에서의 회귀 검증이며 연구 성능 평가가 아닙니다.

같은 정리 작업 당시 `phase_a + personal_metric_v1` 서버의 `/health`는 HTTP 200, `/query` 한 건은 HTTP 503이었습니다. `PGPASSWORD`·`OPENAI_API_KEY` 누락으로 초기화가 차단됐고 DB·OpenAI 호출은 없었습니다. 이는 당시 환경 설정 실패이며 현재 버전의 모든 질의가 실패한다는 의미는 아닙니다.

### 실제 웹 데모 연결 확인 — 2026년 10월 22일

이후 별도 환경 설정을 적용한 실제 React·FastAPI·PostgreSQL·OpenAI 실행에서, 데드리프트 처음 3세션과 최근 3세션 e1RM 비교 및 문헌 설명을 요청한 한 건이 `answer_ready`로 완료됐습니다. 기록 조회·Metric 계산·문헌 검색이 실행됐고 최초 근거 판정이 충분하여 Recovery는 실행되지 않았습니다. [실행 결과와 녹화 범위](assets/hybrid-live-demo.md), [저장 응답](assets/hybrid-live-demo-source.json)

이 기록은 README의 실제 웹 화면 GIF를 뒷받침하는 단일 연결 확인입니다. 전체 질문의 성공률, 현재 시점의 서버 가동 여부, 독립적인 답변 품질 평가를 뜻하지 않습니다.
