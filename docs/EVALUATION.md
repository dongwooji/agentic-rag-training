# 평가 이력과 현재 한계

이 문서는 로컬 frozen 결과 및 분석 문서에서 aggregate만 옮긴 공개 요약입니다. 원문 논문·개별 운동 기록·provider 응답은 재배포하지 않습니다.

## 연구 과정

1. 운동 기록 전처리 및 eligibility 정책, PostgreSQL 적재
2. 문헌 22편 / 488 chunks 구성과 frozen evaluation 30 cases
3. Dense, BM25, Dense+BM25+RRF 검색 비교
4. typed Training Log / Metric / Literature Tool 구현
5. deterministic Router와 LLM Planner의 Tool selection 비교
6. Grader v1/v2/v2.1 실험 및 failure analysis
7. Runtime Grader, Recovery query, Top-10 Fusion, LangGraph, Answer/Abstain 연결
8. E2E v1 → 허용된 integration defect 3건 수정 → 사전등록된 E2E v2
9. FastAPI 및 Tool Input Resolver 구현, 실제 REST smoke 검증

## 주요 관측

- Router exact Tool set match: 29/30; LLM Planner: 24/30. 이번 결과는 LLM Planner가 Router를 대체해야 한다는 근거가 되지 않았습니다.
- Grader v2.1: 12 cases 중 valid 4, INVALID 8. 정확한 quote·anchor 출력 규약의 실행 안정성이 부족했습니다. Runtime에서는 exact quote를 제거하고 supplied chunk ID 검증과 deterministic finalizer를 사용합니다.
- E2E v2: answer_ready 19, abstain_ready 10, execution_failure 1.
- 문헌형 18 cases의 Initial CE@10은 10/18, Final CE@10은 11/18. EGR@10은 0.660185 → 0.715741입니다.
- v2 unsafe answer 5건, unnecessary abstention 2건. unsafe는 answer_ready이면서 Gold final CE=false인 경우이며, 답변 텍스트의 모든 주장을 의미적으로 채점한 값이 아닙니다.
- v2 Unanswerable 6/6은 Tool/API 요청 없이 abstain했습니다.
- v2 비용 기록: API requests 52, total tokens 440,345, 실행 당시 설정 단가로 추정 $0.371081. 현재 가격 견적이 아닙니다.
- v2 recovery success 1/3: initial CE=false인 recovery-trigger cases 중 final CE=true로 변한 비율입니다. HYB-006에서 근거 개선은 있었지만 최종 abstain으로 남았습니다.

## 실제 REST smoke (2026-09-21)

| 경로 | HTTP / final status | Resolver | Recovery | 관측된 Tool 성공 |
|---|---|---|---:|---|
| Literature | 200 / answer_ready | bypass | 1 | Literature initial + recovery |
| Log/Metric | 200 / answer_ready | deterministic | 0 | Training Log + Metric |
| Hybrid | 200 / answer_ready | deterministic | 0 | Training Log + Metric + Literature |

추가 `/health`, `/version` 요청도 200이었습니다. 이는 별도 개발용 질문 3개의 연결 검증이며 baseline 성능과 합치지 않습니다. 전체 테스트 기록은 309 passed입니다.

Smoke 보고서의 API 요청 수(4/1/2)는 graph state에서 유도한 집계입니다. 네트워크 전송 단위의 독립적인 audit log는 아니므로 실제 wire 요청 개수의 엄밀한 증명으로 표현하지 않습니다. 이 관측 한계를 README/포트폴리오 설명에서도 유지해야 합니다.

## 로컬 동결 산출물 식별

| baseline | manifest SHA-256 |
|---|---|
| E2E v1 | `fd6d45414010033d61971fbddbea26fcd19c485aa51b2cb64c2ef0caff3478e6` |
| E2E v2 | `c1e5c4c2defee539edb9980c62b79da003b33b4db2e1186314fb884706904225` |

전체 자료가 없는 공개 clone에서는 이 해시를 재검증할 수 없습니다. 두 baseline은 같은 30-case set의 반복 integration 비교이며 독립 held-out generalization, causal ablation, 최종 답변 의미 정확도 검증을 주장하지 않습니다. 현재 코드는 API/Resolver가 추가된 post-baseline runtime입니다.
