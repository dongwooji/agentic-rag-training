# 평가와 한계

현재 코드의 기능 검증과 과거 코드에서 수행한 연구 평가를 구분한다. 이번 저장소 정리는 성능 실험이 아니며 검색 순위·Metric·기존 Grader·Recovery 정책을 바꾸지 않았다. `answer_ready` 비율은 accuracy가 아니다.

## 현재 코드의 기능 검증

정리 전 기준은 `17654f70781c565d8ee2434908a62008dda46674`다. 아래는 이번 정리의 Windows 로컬 회귀 검증이며 연구 성능 평가가 아니다. 실행 명령과 marker 규칙은 [SETUP.md](SETUP.md)에 있다.

| 검증 | 정리 전 | 정리 후 |
|---|---:|---:|
| 작업 폴더 공개 테스트 | 1,257 passed / 81 deselected | 1,076 passed / 81 deselected |
| 작업 폴더 로컬 전체 테스트 | 1,338 passed | 1,157 passed |
| tracked 테스트 수집 수 | 1,319 | 1,138 |
| 서비스 관련 우선 회귀 | — | 63 passed |
| 프론트엔드 tracked-only | 코드 동일 | lint·29 tests·build 통과 |

작업 폴더 수에는 기존 미추적 사용자 감사 테스트 19개가 포함되며 PR에 넣지 않았다. 감소한 181개는 삭제한 실험 전용 8개 파일의 165개와 혼합 파일의 과거 Step 2 진단 16개뿐이다. 혼합 파일의 서비스 assertion과 decorator는 유지했고, 제거한 평가기 검사와 구분했다. 서비스 테스트의 skip·marker·예상값을 변경하지 않았다.

공개 테스트는 mock·synthetic·offline 계약 검사이며 전체 실행은 로컬 frozen 자산 검사도 포함한다. 실제 OpenAI·DB 호출 테스트가 아니다. Python 실행에는 기존 Starlette deprecation warning 1건이 있다. 원래 프론트엔드 작업 폴더에서는 ignored 실제 녹화와 시간 초과로 3개가 실패했고, 해당 자료를 보존한 채 tracked 파일만 복사한 환경에서 동일 명령으로 모두 통과했다. 프론트엔드 코드·테스트·workflow는 변경하지 않았다.

`phase_a + personal_metric_v1`을 명시한 실제 Uvicorn 서버에서 `/health`는 200, `/query` 한 건은 503이었다. 서버 환경에 `PGPASSWORD`·`OPENAI_API_KEY`가 없어 초기화가 차단됐다. DB·OpenAI 호출은 0회이며 실제 E2E 완료로 표현하지 않는다. 서비스 구성·LangGraph·API·개인 Metric 경로는 합성 통합 테스트로 확인했다. GitHub Actions의 최종 head 결과와 전체 제거·유지 테스트 목록은 이 변경의 PR에서 확인한다.

## 평가 정의

- Retrieval: EGR@10은 문항별 required evidence group 충족 비율의 평균, CE@10은 모든 required group 충족 여부의 평균이다. MRR은 첫 required chunk 순위의 역수다. required group이 없는 문항은 해당 지표에서 제외한다.
- Grader: Gold CE와 충분성 판정의 불일치와 실행 실패를 구분한다. chunk Gold 일치가 의미적 충분성 전체를 대신하지 않는다.
- Acceptance: source·실제 Tool operation·최종 동작 계약을 채점한다. 최종 동작 정확도는 답변 문장 전체의 사실 정확도와 다르다.
- Gold는 실행 종료 후 평가에서만 읽는다. corpus coverage, 검색 누락, 질문 해석, Metric, Grader, Recovery, 답변, API 경계 실패를 별도로 분석한다.

## 초기 평가: Retrieval v1·Router·E2E

동일 frozen 30문항을 반복 분석한 개발·통합 비교다. Router의 Tool 집합 정확 일치는 29/30, LLM Planner는 24/30이었다. Grader v2.1은 12건 중 valid 4, INVALID 8로 exact quote·anchor 규약의 실행 안정성이 부족했다. 현재 Runtime Grader는 exact quote를 강제하지 않고 chunk membership과 deterministic finalizer를 사용한다.

| 지표 | E2E v1 | E2E v2 |
|---|---:|---:|
| answer_ready | 13/30 | 19/30 |
| execution_failure | 4/30 | 1/30 |
| Hybrid answer_ready | 0/8 | 4/8 |
| Log/Metric answer_ready | 4/6 | 6/6 |
| Unanswerable abstention | 6/6 | 6/6 |
| Gold 기준 unsafe answer | 2 | 5 |

v2는 세 가지 통합 결함 수정 후의 과거 평가다. 문헌형 18문항의 Initial CE@10은 10/18, Final CE@10은 11/18이며 EGR@10은 0.660185 → 0.715741이다. unsafe는 answer_ready이면서 Gold final CE=false라는 정의로, 답변의 모든 문장을 의미 채점한 값이 아니다. 독립 일반화 성능이나 현재 코드 성능으로 해석하지 않는다. [원본 요약](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/HISTORY.md).

## Retrieval v2: 선택과 별도 평가

corpus v1의 dev/dev-ko는 같은 18문항의 언어별 표현이다. 사전 기준에서는 Step 2를 채택하고 Step 3a·3b는 제외했다. 이후 사용자 승인 사후 결정으로 Step 3b를 Final V2 비교 기준으로 선택했다. 이 사후 선택을 사전 기준 통과로 바꾸어 설명하지 않는다. 현재 서비스에는 corpus v1에 결합한 Step 3b를 사용하며, 과거 보고서의 당시 기본값 legacy/H0와 구분한다. [선택 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_STEP23_RESULTS.md), [최종 결정 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/decisions/RETRIEVAL_V2_FINAL.md).

별도 corpus v2의 held-out은 24문항 중 required evidence가 있는 20문항을 분모로 한다. 설정·검색어·네 조건의 순위를 모두 동결한 뒤 Gold로 평가했다.

| 설정 | EGR@10 | CE@10 | MRR |
|---|---:|---:|---:|
| H0 | 0.188333 | 0.050000 | 0.196389 |
| H1 | 0.200833 | 0.050000 | 0.201389 |
| Step 2 | 0.200833 | 0.050000 | 0.201389 |
| Final V2 | 0.208333 | 0.050000 | 0.211865 |

CE는 모두 1/20이다. corpus가 다른 dev 수치를 같은 조건의 전후 비교로 읽지 않는다. 이 결과만으로 서비스 전환의 충분한 성능 근거가 되지 않으며, 답변 생성·Grader·Recovery·실제 REST는 이 검색 평가에서 실행하지 않았다. [원본 결과](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_FINAL_RESULTS.md).

동결 순위 사후 진단에서 Final V2는 71개 required group 중 16개를 회수하고 55개를 놓쳤다. 정답 논문 하나 이상 적중은 18/20, 모든 정답 논문 적중은 17/20이지만, 16문항은 정답 논문을 모두 찾고도 필요한 근거가 누락됐다. 저장된 후보 범위에서 누락 3개는 포함을 확인했고 나머지 52개는 판정 불가다. unknown을 후보 밖 또는 corpus gap으로 처리하지 않는다. 새 검색 없이 한 진단이며 원인·개선 효과를 확정하지 않는다. [진단 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_HELDOUT_DIAGNOSIS.md).

별도의 운영 재현성 검사(2026-10-12)는 corpus v1의 동일 동결 query에서 offline ↔ pgvector Top-10 ID·순서가 36/36 일치했다. corpus v2 held-out 재현이나 성능 재평가가 아니다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/SERVICE_RETRIEVAL_V2_REPRODUCIBILITY.md).

## Acceptance v1

baseline `685e2b9194419fbf757df2df2156299b77e4afe3`, changed `6c68ea996f967bcdda47bfed2671aae1ae583969`에서 Phase A로 각 20문항을 실제 REST 실행했다. 원시 결과를 동결한 뒤 Gold를 열었다.

| 필수 기준 | baseline | changed |
|---|---:|---:|
| required-channel omission | 6 | 0 |
| expected-answer의 부당한 clarification/abstain | 12 | 9 |
| final behavior accuracy | 5/20 (25%) | 10/20 (50%) |
| graph-state execution_failure | 0 | 0 |

고정 scorer 기준 PASS지만 서비스 REST 안정성 통과는 아니다. HTTP 500은 0→3건으로 늘었다. graph observer가 공개 응답 변환 실패를 집계하지 못한 한계를 별도로 보존한다. 원인은 최근 8주 metadata와 공개 schema의 충돌이었다. 이후 수정한 현재 코드의 성능 수치로 재사용하지 않는다. optional 문헌의 blocking, 불필요한 운동·기간·Metric 확인이 남았다. 단일 20문항 실행이며 독립 사람의 답변 전체 평가가 아니다. [원본 보고서](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_evidence_acceptance_v1/REPORT.md), [상세 판정](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_evidence_acceptance_v1/ACCEPTANCE.md).

## Grader v1·v1.1 비채택과 Acceptance v2

고정 요구사항 Grader v1은 `454177751180e4a88853e1a3e09e40899e6ef0c3`에서 dev/dev-ko 36입력을 평가했다. A=CE=1인데 부족, B=CE=0인데 충분이다. A+B는 12→7로 줄었지만 B는 4→6, 실행 실패는 0→11로 늘어 비채택이다. 그중 7건은 문헌 질문 출처 검증 실패로 LLM을 호출하지 않았다. 새 Recovery 켬/끔은 최종 EGR/CE가 모두 0.752778/0.666667로 같아 기존 비감소 기준을 충족했지만 개선 효과를 입증하지 않았다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/grader_fixed_requirements_v1/REPORT.md).

입력 경계를 수정한 v1.1은 **별도 SHA `992b495024e2f3a5b2db2abd3890e9a4f356a094`**에서 Acceptance v2로 비교했다. 2026-10-19, 같은 DB·corpus v1에서 Phase A A=legacy Grader / B=fixed_requirements_v1_1, 21문항씩 실제 REST 단일 실행이다.

| 고정 채택 조건 | A | B | 통과 |
|---|---:|---:|---|
| answerable 보류 수 엄격 감소 (분모 10) | 0 | 1 | 아니오 |
| unanswerable 답변 수 비증가 (분모 4) | 0 | 1 | 아니오 |
| HTTP 포함 최종 실행 실패 비증가 (분모 21) | 0 | 0 | 예 |
| 최종 동작 정확도 비감소 | 14/21 (66.67%) | 12/21 (57.14%) | 아니오 |

`eligible_for_adoption=false`, `service_default_changed=false`다. 양쪽 HTTP 200은 21/21, 답변/보류/되묻기는 각각 9/4/8이다. 부당한 되묻기·보류는 6→7건이며, answerable 보류 지표에는 clarification이 포함되지 않는다. A의 0을 모든 답변 가능 요청 처리로 해석하지 않는다.

V2-07은 B만 unanswerable에 답변했고, V2-10은 B만 answerable을 보류했다. 7문항은 양쪽 모두 Grader 전 확인 요청에서 멈췄다. 영어 query·Top-10은 10개 중 8개만 같아 순수 Grader의 인과 효과를 뜻하지 않는다. B 판정 16회가 모두 completed였어도 과거 실패 11건의 재평가는 아니다. 전체 token·비용은 observer 미기록으로 N/A이며 0으로 채우지 않는다. [v2 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/evaluations/phase_a_acceptance_v2_20261019/REPORT.md).

## Reranker 비채택

2026-10-10 corpus v1 dev/dev-ko 36회 측정이며, 같은 18문항의 언어 쌍이다. CPU에서 동결 후보를 재정렬했고 held-out은 사용하지 않았다.

| 조건 | EGR@10 | CE@10 | MRR | CPU 평균(초) |
|---|---:|---:|---:|---:|
| Step 3b / RRF10 | 0.752778 | 0.666667 | 0.432011 | — |
| RRF50 | 0.787500 | 0.666667 | 0.494709 | — |
| A reranker10 | 0.715741 | 0.611111 | 0.503704 | 4.443 |
| B reranker50 | 0.759259 | 0.694444 | 0.531779 | 21.252 |

A는 성능·지연 기준 모두 미충족, B는 Step 3b 대비 일부 성능 기준을 충족했지만 CPU 평균 3초 제한을 넘어 모두 비채택이다. B는 같은 RRF50 후보 대비 평균 EGR이 낮아 후보 확대 효과와 재정렬 효과를 구분한다. [원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/RETRIEVAL_V2_RERANKER_RESULTS.md).

## Recovery 저장 trace 관찰

2026-10-16에 서로 다른 버전·조건의 저장 실행 230건을 사후 분석했다. 문헌 검색 55건, Recovery 발동 17건, 시도 32회, 재검색·Grader 재검사 26회였다. Grader 부족→충분은 2건이지만 최종 answer_ready는 1건이다. 나머지 전환 1건은 답변 grounding 검증에서 실패했다.

문헌 Gold가 있는 과거 E2E에서 최종 EGR/CE가 개선된 실행은 v2 HYB-006 한 건이다. 해당 실행도 최종 Grader는 부족으로 보류했다. v1에서는 같은 문항의 중간 근거가 다음 Top-10 결합에서 사라졌다. Grader 판정 전환·Gold 근거 개선·최종 답변 성공은 서로 다른 지표이며, 이 혼합 자료를 현재 Recovery의 일반 성공률로 표현하지 않는다. [관찰 원본](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/docs/analysis/recovery_saved_traces_v1/REPORT.md).

## 남은 한계

MiniLM 입력 한도에 비해 긴 chunk, 문헌 내 필요한 문단 누락, 운동·기간·Metric 의미 해석, optional 문헌의 blocking이 남아 있다. 답변 문장 전체의 과학적 정확성·개인 조언 적절성에 대한 독립 사람 평가는 수행하지 않았다. 작은 평가셋, 반복 분석, 단일 확률적 실행과 cache·순서 영향을 고려해야 한다. 인증·사용자 격리와 배포 운영 검증도 별도 과제다.

상세 평가 질문·Gold·원본 결과·결정은 [archive-pre-cleanup](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup)에 보존한다. 이 tag는 정리 전 tracked master만 보존하며 로컬 ignored 자산을 포함하지 않는다. 기존 frozen 자산과 역사적 결과를 덮어쓰지 않았고 새 연구 성능은 측정하지 않았다.
