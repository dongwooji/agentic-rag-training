# 실험 이력

이 문서는 현재 tree에서 제거한 과거 실험의 요약이다. 수치는 로컬 frozen 산출물과 진단 보고서의 aggregate만 옮겼다.
같은 30-case frozen 평가셋을 반복 사용한 결과이므로 독립적인 일반화 성능이 아니다.

## 과거 코드 보존 위치

과거 실험 전용 코드·설정·테스트는 Git tag **`legacy-pre-retrieval-v2`**에 보존한다.

```powershell
git show legacy-pre-retrieval-v2:src/agent/planner.py
git worktree add ../agentic_rag_legacy legacy-pre-retrieval-v2
```

| 실험 | tag에만 남은 주요 코드 |
|---|---|
| Retrieval v1 baseline과 진단 | `src/retrieval/baseline.py`, `hybrid_baseline.py`, `scripts/run_dense_baseline.py`, `run_hybrid_baseline.py`, `validate_hybrid_baseline.py`, `build_dense_failure_analysis.py`, `diagnose_retrieval_v1.py` |
| Router vs LLM Planner | `src/agent/planner.py`, `graph.py`, `baseline.py`, `src/routing/baseline.py`, `config/agent_planner_v1*` |
| Grader v1 / v2 / v2.1 | `src/grading/` 중 `runtime_*`·`evaluation.py`를 제외한 파일, `config/grader_v1*`, `grader_v2*`, `grader_v2_1*` |
| E2E v1 / v2 | `src/evaluation/end_to_end_runner.py`, `end_to_end_v2.py`, `end_to_end_v2_metrics.py`, `config/end_to_end_baseline_v1.json`, `v2.json` |

현재 tree에 남긴 공통 평가 코드(`src/evaluation/retrieval_metrics.py`, `routing_metrics.py`, `end_to_end_metrics.py`,
`grading/evaluation.py`, 평가셋 계약)는 [REPOSITORY_MAP.md](REPOSITORY_MAP.md)에 정리했다.

## 연구 순서

1. 운동 기록 전처리·eligibility 정책과 PostgreSQL 적재
2. 문헌 22편 / 488 chunks corpus와 frozen 평가 30 cases
3. Dense, BM25, Dense+BM25+RRF 검색 비교 (Retrieval v1)
4. typed Training Log / Metric / Literature Tool
5. 규칙 기반 Router와 LLM Planner의 Tool 선택 비교
6. Grader v1 / v2 / v2.1 실험과 실패 원인 분석
7. Runtime Grader, Recovery, Top-10 Fusion, LangGraph, 답변/보류 연결
8. E2E v1 → 허용된 integration 결함 3건 수정 → 사전 등록된 E2E v2
9. FastAPI, Tool Input Resolver, 규칙 우선 질문 해석, 운동 후보 추가 확인
10. Retrieval v1 구조 진단 → Retrieval v2 재설계 결정

상세 aggregate는 [EVALUATION.md](EVALUATION.md)에 있다.

## 주요 결과

- **Router vs LLM Planner:** Tool 집합 정확 일치 Router 29/30, LLM Planner 24/30. LLM Planner가 Router를 대체할 근거가 되지 않았다.
- **Grader v2.1:** 12 cases 중 valid 4, INVALID 8. exact quote·anchor 출력 규약의 실행 안정성이 부족했다. 현재 Runtime Grader는 exact quote를 쓰지 않고 제공된 chunk ID 검증과 deterministic finalizer를 사용한다.
- **E2E v1 / v2:**

| 지표 | E2E v1 | E2E v2 |
|---|---:|---:|
| answer_ready | 13/30 | 19/30 |
| execution_failure | 4/30 | 1/30 |
| Hybrid answer_ready | 0/8 | 4/8 |
| Log/Metric answer_ready | 4/6 | 6/6 |
| Unanswerable abstention | 6/6 | 6/6 |
| Gold 기준 unsafe answer | 2 | 5 |

v2는 session→metric adapter, Hybrid 문헌 하위 질문 분리, Final Answer payload 제한이라는 세 integration 결함을 수정한 뒤 평가했다.
answer_ready는 정확도가 아니다. v2의 answer_ready 19건 중 5건은 Gold 최종 CompleteEvidence=false였다.

## Retrieval v1 구조 진단

기존 frozen 평가를 재사용한 읽기 전용 진단이며, 검색·모델·청킹·Gold·baseline은 변경하지 않았다.

| 항목 | 관측 |
|---|---|
| 청크 길이 vs 임베딩 입력 한도 | `paraphrase-multilingual-MiniLM-L12-v2`의 실제 입력 한도는 128토큰. 청크 488개의 토큰 수 평균 419.17, 중앙값 396.5, 최대 1435. **462개(94.67%)가 128토큰을 넘어 뒷부분이 잘린다.** 전체 토큰 가중 보존 비율 30.32% |
| BM25 zero-match | 문헌 포함 18문항 중 1문항(순수 한글 질문). 모든 문서 점수가 0인데도 chunk ID 순서로 Top-10이 반환된다 |
| zero-match의 RRF 영향 | 점수 0인 BM25 순위도 1/(60+rank)를 받아, 해당 문항에서 Dense 밖 청크 5개가 Hybrid Top-10에 들어왔다 |
| 검색 질의 | 초기 검색은 문헌 전용 하위 질문이 아니라 원문 질문을 사용한다. 하위 질문이 달라지는 7문항에서 Hybrid EGR@10은 원문 0.2857, 문헌 전용 질의 0.7857 (진단 비교이며 일반화 주장 아님) |
| metadata | title·section·population·study_type·topics는 저장되지만 Dense/BM25 검색 입력은 text만 사용한다 |
| 문헌 다양성 | Top-10 고유 논문 수 평균 4.28. 18문항 중 9문항에서 한 논문이 5개 이상 차지 |
| 후보 depth | Dense 10 + BM25 10 → RRF 10. 11~50위에 유효 Gold가 있는 문항 12개, depth 10 합집합 밖이라 진입 기회를 잃은 required group 9개 |

확인된 문제는 청크 truncation, zero-match BM25의 RRF 기여, 원문 질의 사용, metadata 미사용, depth 10 밖 근거다.
truncation이 검색 실패의 어느 정도를 설명하는지, 한국어 Dense 열세, metadata·다양성·depth 변경의 이득은 검증하지 않았다.

이 진단에 따라 **Retrieval v2는 변수 하나씩 독립 비교**한다: 토큰 한도에 맞춘 청킹 → BM25 zero-match 처리 →
문헌 질문 추출과 영어 질의 변환 → metadata·depth·다양성·모델 교체. 개발용 평가와 최종 별도 평가를 분리하고,
기존 30문항으로 반복 튜닝하지 않는다.

## frozen 기록 해시와 재현 범위

E2E v1/v2 사전 등록 manifest와 Retrieval v1 진단의 보호 해시 기록은 당시 소스 파일의 SHA-256을 고정한다.
이후 실행 구조가 바뀌어 일부 소스가 기록과 일치하지 않으며, manifest와 해시 기록은 수정하지 않는다.

- 현재 tree에는 E2E v1/v2 실행기와 Retrieval v1 진단 스크립트가 없다.
- `legacy-pre-retrieval-v2` tag에서도 E2E v1/v2 사전 등록 해시가 전부 일치하지는 않는다. tag 이전부터 `graph/nodes.py`,
  `graph/state.py`, `graph/workflow.py` 등이 기록과 달랐고, tag 직전 직접 의존성 분리로 실행 provider 파일이 추가로 달라졌다.
- manifest에는 git에 포함되지 않은 로컬 자료도 있다. 과거 실험의 정확한 재실행에는 기록 당시 상태를 별도로 보존한 작업 디렉터리가 필요하다.
