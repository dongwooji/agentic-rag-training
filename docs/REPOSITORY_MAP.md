# 저장소 구성 지도

기본 브랜치에는 현재 실행 구조(runtime), 앞으로 다시 사용할 평가 인프라, 데이터 준비·DB·API·테스트 코드만 둔다.
과거 버전의 실험(Retrieval v1 baseline, Router vs LLM Planner, Grader v1/v2/v2.1, E2E v1/v2)을 재현하기 위한
전용 코드·설정·테스트는 Git tag **`legacy-pre-retrieval-v2`** 에 보존하고 현재 tree에서는 제거했다.
과거 실험의 요약은 [HISTORY.md](HISTORY.md)를 참고한다.

분류 근거는 이름이 아니라 실제 import 관계다. 실행 구조의 기준 진입점은 `src.api.app`이며,
`tests/test_runtime_import_boundary.py`가 실행 구조가 평가·데이터 준비 코드를 로드하지 않는지 검사한다.

| 표기 | 의미 |
|---|---|
| **실행** | `src.api.app`이 사용하는 현재 실행 구조 |
| **평가** | Retrieval / Router / Grader / E2E를 다시 평가할 때 재사용하는 지표·평가셋 계약·실행기 |
| **데이터 준비** | DB 적재·전처리·문헌 corpus 구축 등 실행 전 단계 |

## src/

| 경로 | 분류 | 역할 |
|---|---|---|
| `api/` | 실행 | FastAPI endpoint, 의존성 구성, 공개 응답 계약 |
| `graph/` | 실행 | LangGraph workflow, node, state, Tool Input Resolver, Phase A 해석 workflow |
| `interpretation/` | 실행 | 규칙 우선 질문 해석, 운동 후보 검증, 추가 확인 흐름 |
| `routing/` | 실행 | 규칙 기반 Router(`deterministic.py`)와 한국어 입력 처리 |
| `agent/contracts.py`, `agent/executor.py` | 실행 | Tool 계획 schema와 결정론적 Tool 실행기 |
| `tools/` | 실행 | Training Log / Metric / Literature Tool |
| `retrieval/` | 실행 | Dense(`dense.py`), BM25(`bm25.py`), RRF(`rrf.py`), pgvector(`postgres.py`), Hybrid 검색(`hybrid.py`), 실행용 식별자(`runtime_config.py`) |
| `grading/runtime_*.py` | 실행 | Runtime Evidence Grader (계약, provider, deterministic finalizer) |
| `grading/evaluation.py` | 평가 | Grader 판정 대 Gold 판정 혼동행렬 지표 |
| `recovery/` | 실행 | Recovery 질의 생성, 검증, 근거 결합 |
| `answer/` | 실행 | 최종 답변과 답변 보류 |
| `llm_support.py` | 실행 | 실행 provider 공용 OpenAI 응답 처리 함수 |
| `database/config.py` | 실행 | DB 접속 설정 |
| `database/bootstrap.py`, `loader.py`, `psql.py` | 데이터 준비 | 스키마 설치와 운동 기록 적재 |
| `metrics/definitions.py` | 실행 | e1RM·plateau 등 지표 정의 |
| `preprocessing/` | 데이터 준비 | 운동 기록 정규화와 eligibility 정책. `policy.py`·`clean_workouts.py`는 지표 정의를 통해 실행 중에도 로드된다 |
| `literature/` | 데이터 준비 | PubMed/JATS 수집, chunk 구성, 문헌 DB 적재 |
| `evaluation/retrieval_metrics.py` | 평가 | EvidenceGroupRecall, CompleteEvidence, ChunkRecall, MRR, Gold 근거 묶음 처리 |
| `evaluation/routing_metrics.py` | 평가 | Router Tool 선택 지표 |
| `evaluation/end_to_end_metrics.py` | 평가 | 완료된 graph state와 평가 계약을 비교하는 E2E 지표 |
| `evaluation/validation.py`, `reference.py`, `report.py` | 평가 | 평가셋 계약 검증, 기록 기반 기준값 계산, 사람 검토용 보고서 |
| `smoke/e2e.py` | 평가 | 실제 구성요소 E2E smoke 실행기 (실행 코드만 사용) |

## config/

| 파일 | 분류 |
|---|---|
| `final_answer*`, `question_interpreter*`, `recovery_agent*`, `runtime_evidence_grader*`, `tool_input_resolver*` | 실행 (provider 설정·prompt) |
| `runtime_exercise_relations_v1.json`, `runtime_input_language_v1.json`, `exercise_selection_v1.json` | 실행 |
| `router_baseline_v1.json` | 실행. 현재 Router가 로드하는 설정 (이름은 최초 동결 당시 버전) |
| `exercise_aliases_v1.csv`, `preprocessing_v1.json` | 실행 + 데이터 준비 (frozen) |
| `literature_corpus_v1.json`, `literature_selection_v1.json` | 데이터 준비 |

## scripts/

| 분류 | 파일 |
|---|---|
| 실행 확인 | `run_api_integration_smoke.py`, `run_end_to_end_smoke.py` |
| 평가셋 구축·검증 | `freeze_evaluation_set.py`, `validate_evaluation_set.py`, `build_evaluation_evidence_human_review.py`, `migrate_evidence_groups.py` |
| 데이터 준비 | `setup_postgres.py`, `load_postgres.py`, `load_literature_postgres.py`, `setup_dense_retrieval.py`, `preprocess_workouts.py`, `build_literature_corpus.py`, `search_literature.py`, `eda_workouts.py`, `test_postgres_integration.py` |

## tests/

| 분류 | 파일 |
|---|---|
| 실행 | `test_api_*`, `test_answer_integration`, `test_final_answer`, `test_abstention`, `test_graph_*`, `test_tool_input_resolver`, `test_tool_plan_contracts`, `test_question_interpretation`, `test_exercise_*`, `test_runtime_*`, `test_recovery_*`, `test_phase8_tools`, `test_routing`, `test_dense_retrieval`, `test_hybrid_retrieval`, `test_post_baseline_v1_remediation` |
| 평가 | `test_retrieval_metrics`, `test_grader_evaluation`, `test_end_to_end_metrics`, `test_evaluation`, `test_evidence_human_review_v2`, `test_e2e_smoke_harness` (`test_routing`은 Router 평가 지표도 검사) |
| 데이터 준비 | `test_database`, `test_literature`, `test_preprocessing`, `test_eda_workouts` |
| 공용 fixture | `answer_test_support.py`, `graph_test_support.py` |

일부 테스트는 로컬 비공개 자료(`data/`, `reports/`)를 읽는다. 예를 들어 `test_post_baseline_v1_remediation`은
frozen `eval_dataset_v1`의 질문 문장만 읽어 실행 graph에 넣는다(Gold는 사용하지 않는다).

## 실행 구조가 읽는 frozen 자료

`retrieval/hybrid.py`는 실행 시작 시 frozen 문헌 corpus와 최초 Hybrid baseline(`reports/baselines/hybrid_baseline_v1/`)
산출물의 SHA-256을 검증한 뒤 BM25 색인을 만든다. 이 산출물은 로컬 자료이며 현재 tree에서 삭제한 실행기 코드와는 별개다.

## 새 코드를 추가할 때

- 실행 구조 코드는 평가·데이터 준비 모듈을 import하지 않는다. 공용 기능이 필요하면 실행 전용 위치에 둔다.
- 새 실험은 기존 파일을 수정하지 않고 새 버전 파일·설정·보고서로 추가한다 (`AGENTS.md` 참조).
- 과거 실험 코드를 현재 tree로 다시 복사하지 않는다. 필요하면 `legacy-pre-retrieval-v2` tag에서 참조한다.
