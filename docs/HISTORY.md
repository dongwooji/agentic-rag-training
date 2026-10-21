# 개발 이력

현재 실행 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 설치는 [SETUP.md](SETUP.md), 수치와 한계는 [EVALUATION.md](EVALUATION.md)에 정리합니다.

| 단계 | 구현·검증과 결정 |
|---|---|
| 기록·corpus 준비 | 공개 장기 운동 기록 정규화·eligibility 정책, PostgreSQL 스키마·적재, PubMed/PMC/JATS 문헌 수집과 corpus v1 구축 |
| Retrieval v1·실행 제어 | Dense·BM25·RRF, typed Tool, Router 대 LLM Planner 비교. 규칙 Router 유지 |
| Grader·E2E | quote·anchor 규약의 불안정성 확인, chunk membership·finalizer, bounded Recovery·Top-10 Fusion·LangGraph·Answer/Abstain 연결 |
| API·질문 해석 | FastAPI·Tool Input Resolver, 한국어 입력, 규칙 우선 Phase A, canonical catalog·운동 후보 선택, 날짜 grounding·관측 로그 |
| Retrieval v2 | zero-match BM25 보정, 검색어 생성·번역, child·입력 길이·후보 깊이 실험. 사전 기준의 Step 2와 사후 선택 Step 3b를 구분 |
| 별도 검색 검증 | corpus v2 held-out 평가·동결 순위 진단, corpus v1 offline ↔ pgvector 순위 재현성 확인. 서비스는 corpus v1 Step 3b 사용 |
| 비채택 실험 | Reranker CPU 성능·지연 평가, fixed-requirements Grader v1·v1.1 평가. 기존 Grader 유지 |
| Acceptance·후속 수정 | Acceptance v1의 API 경계 실패, operation 인자·세션 범위 수정, Acceptance v2의 비채택 판정. 이후 실행 정규화·personal_metric_v1 추출 규칙·first/last 기록 계약 수정은 과거 평가 이후의 변경 |
| 프론트엔드·공개 정리 | React 질문·추가 확인·근거 패널과 녹화 재생. 상세 산출물·전용 실험 경로 제거, 공개 문서를 네 개로 통합 |

과거 E2E v1/v2는 같은 30문항의 반복 통합 비교입니다. Retrieval v1 진단에서 488 chunks 중 462개(94.67%)가 encoder의 128토큰을 넘었고, zero-match BM25도 RRF에 기여하는 문제가 확인됐습니다. 이후 실험을 핵심 변수 하나씩 나누었으며, 개발셋과 별도 평가를 구분했습니다. 후속 정확성 수정의 단위·연결 검증을 과거 평가 성능 향상으로 해석하지 않습니다.

## 보존 위치

이번 정리 전 master는 annotated tag [archive-pre-cleanup](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup)에 보존합니다. 가리키는 commit은 `17654f70781c565d8ee2434908a62008dda46674`입니다. tag는 tracked 파일만 보존하며 ignored `data/`, `reports/`, corpus·모델·embedding·`eval_frozen/`을 포함하지 않습니다. ZIP 백업이나 이력 재작성·force push는 하지 않았습니다.

| 자료 | 고정 보존 링크 |
|---|---|
| Acceptance v1/v2, Grader 평가, 추출 규칙 확인 | [docs/evaluations](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/docs/evaluations) |
| Recovery 저장 trace 분석 | [docs/analysis](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/docs/analysis) |
| 상세 설계·비채택 결정 | [docs](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/docs), [decisions](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/docs/decisions) |
| tracked 평가 질문·Gold | [data/evaluation](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/data/evaluation) |
| fixed-requirements v1/v1.1 | [src/grading](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/src/grading) |
| Reranker·과거 Step 2 진단 | [src/retrieval/reranker.py](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/src/retrieval/reranker.py), [first_literature_query.py](https://github.com/dongwooji/agentic-rag-training/blob/archive-pre-cleanup/src/graph/first_literature_query.py) |
| 일회성 CLI·평가 실행기·전용 테스트 | [scripts](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/scripts), [tests](https://github.com/dongwooji/agentic-rag-training/tree/archive-pre-cleanup/tests) |

더 이전의 Retrieval v1 baseline, LLM Planner, Grader v1/v2/v2.1, E2E v1/v2 전용 소스는 [legacy-pre-retrieval-v2](https://github.com/dongwooji/agentic-rag-training/tree/legacy-pre-retrieval-v2)에 보존합니다. 예를 들어 역사적 Dense 생성 코드는 [run_dense_baseline.py](https://github.com/dongwooji/agentic-rag-training/blob/legacy-pre-retrieval-v2/scripts/run_dense_baseline.py)에 있습니다.

```powershell
git show archive-pre-cleanup:docs/RETRIEVAL_V2_RERANKER_RESULTS.md
git show archive-pre-cleanup:src/grading/fixed_requirements_v1/grader.py
```

tag의 코드가 모든 역사적 manifest의 소스 해시와 일치하는 것은 아닙니다. 일부 소스는 tag 이전부터 바뀌었고 ignored 자료도 필요하므로, tag만으로 과거 평가의 정확한 재실행을 보장하지 않습니다. 역사적 문서의 당시 기본값·미수행 단계·PR 상태는 당시 기록이며 현재 상태 안내는 이 네 문서를 따릅니다.
