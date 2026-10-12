# Retrieval V2 최종 설정과 held-out 실행 결정

결정일: 2026-10-12. 근거: 사용자 명시 승인. 검색 결과를 보기 전에 이 문서와 설정을 고정한다.

## Gold 사람 검토 결정

AI 원문 대조 18/18 확인 후 사람이 18개 전체와 의미 수정 이력이 있는
003-E03, 006-O01, 009-E02, 020-E01을 직접 확인했다. 추가 수정이 필요하지 않다는 사용자 결정을 기록한다.
Gold version은 `heldout_v2_v1_1_final`, `gold_evidence.json` SHA-256은
`7c491a55e96b70b26b10ce7625c535e1dc521b4d83ab9d4e984d5d2c84df4b42`다.
현 Gold freeze와 과거 검토 자료를 변경하지 않는다.

AI 검토의 경미한 표현 지적 5건은 현재 검색 지표에 영향을 주지 않는다는 검토 결정을 수용하고
차기 Gold version 후보로만 기록한다. 기존 `reports/diagnostics/heldout_gold_v2_1_final/human_review_checklist_reviewed.md`를 보존한다.

| 차기 version 후보 | 검토에서 지적한 범위 |
|---|---|
| HOV2-003-E03 | notes의 Experiment/MDC 설명 |
| HOV2-006-O01 | 보조 근거의 한계·완화 표현 |
| HOV2-009-E02 | p값이 지칭하는 비교의 표현 |
| HOV2-009-E04 | qualification의 p값 경계 표현(원 p값은 notes에 보존됨) |
| HOV2-010-E05 | 일반화 제한 표현의 강도 |

이는 이번 Gold 변경 요청이나 새 freeze가 아니다. 후보 상세의 source of truth는 보존된 AI 검토 기록이다.

## 최종 설정 선택 이력

- 원래 사전 기준에서는 Step 2 채택, Step 3a·3b 제외였다. Step 3b는 한국어 사용 목적과 dev-ko 결과를
  고려한 사용자 **사후 결정**으로 이후 누적 기준선에 선택됐다. 사전 채택으로 소급 표현하지 않는다.
- Step 4 parent-child는 최초 RRF 입력 parent 수가 명세와 달랐다. parent 10개로 보정한 새 version의
  재실험에서도 미채택이었다. 최초 결과와 보정 결과를 모두 보존한다.
- Step 5 후보 깊이 20/50 미채택.
- MiniLM parent 길이 256/512는 truncation을 줄였지만 성능이 하락해 미채택.
- reranker A는 성능 기준 미달. B는 성능 기준을 통과했지만 CPU 평균 약 21.25초로 사전 3초 제한을 초과했다.
  Step 3b 대비 CompleteEvidence 24/36→25/36은 1문항 순증이며, 운영 비용을 감수할 정도의 이득으로 보지 않아 미채택.
- 기존 사전 기준·결정·결과를 변경하지 않는다. 개별 실험 문서와 동결 결과가 상세 근거다.

## 누락 근거 진단

Step 3b Top-10 누락은 14 required evidence groups였다. Dense10+BM25_10의 union 후보 pool(최대 20)에
3 groups가 이미 있었다. depth50+50 union 후보 pool(최대 100)에서 7 groups를 추가로 발견했고 4 groups는 그 밖이었다.
최대 100 후보 전체의 CompleteEvidence 포함 상한은 88.9%(32/36)다. 최종 Top-10 성능 보장이 아니며,
후보 밖 근거를 corpus coverage 부족으로 단정하지 않는다.

## Step 7 설정과 Step 8 절차

최종 V2: Step 2 생성 및 사전 원문 보완 → Step 3 번역 및 사전 번역 실패 보완 → parent Dense/BM25 →
MiniLM 길이 128 → Dense10 + BM25 positive_only10 → 동일 가중 RRF(k=60) → Top-10.
Dense/BM25 모두 Step 3 실제 query를 사용한다. child·reranker·metadata·약어+숫자 정규화는 적용하지 않는다.
서비스 기본값은 legacy/H0로 유지한다. corpus 경로 선택은 명시 설정에서만 가능하다.

설정 및 실행 소스 manifest를 먼저 freeze한 뒤 held-out 24문항을 읽는다. 기존 prompt·schema·validator·provider/model을
그대로 사용하여 원 질문에서 Step 2를 각 1회 생성·freeze하고, 동결된 실제 query를 각 1회 번역·freeze한다.
생성기 입력은 original_question 문자열 하나, 번역기 입력은 실제 Step 2 query 하나다. ID·분류·Gold·근거·검색 결과는
provider 입력과 query 선택에 사용하지 않는다. 사람이 검색어를 검토·수정하지 않고 검증 실패는 기존 dev 보완 규칙을 따른다.
자동 재시도·추가 생성·검색 결과를 본 뒤의 변경은 없다.

corpus v2에서 H0/H1은 원 질문, Step 2는 실제 생성 query, Final V2는 실제 번역 query로 각각 1회 검색한다.
동일한 Dense 설정은 별도 새 version의 단일 parent embedding을 공유하고, BM25의 두 정책을 독립 설정으로 기록한다.
각 설정의 인덱스 참조·정의·실제 query와 모든 순위를 저장한 뒤에만 Gold를 열어 평가한다.
평가 성공 여부와 무관하게 검색 기록은 덮어쓰거나 재실행하지 않는다.

사전 정의된 task_type, scope_class, question_family_id 및 명시 언어 그룹이 있으면 집계한다.
required group이 없는 문항은 recall의 분모에서 제외하고 실제 수를 보고한다. boundary는 별도 보고한다.
Retrieval만 실행하므로 abstention correctness, Grader/Recovery, 답변 정확도는 측정하지 않는다.
문항의 expected_behavior 분포는 설명용이며 실제 abstention 실행 결과가 아니다.

설정은 corpus v1 dev에서 선택하고 최종 평가는 corpus v2 held-out에서 수행한다. 코퍼스가 다른 개발 수치와
최종 수치를 직접 같은 조건의 전후 개선으로 해석하지 않는다. held-out 결과에 따른 추가 채택·튜닝을 하지 않는다.
pgvector 운영 재현은 명세의 별도 후속 확인으로 남긴다. 이번 승인 범위는 메모리 exact cosine 최종 검색 평가다.

## 향후 과제 (현재 V2 미포함)

- 더 강한 reranker: 긴 parent를 조각별 scoring 후 max/aggregation, RRF score와 결합, diversity 고려.
- BGE-M3 등 long-context multilingual embedding model.
- pgvector 운영 재현, 새 검색 결과에서 Grader/Recovery·E2E 재평가.
