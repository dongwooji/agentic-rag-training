# 저장소 구성 지도

현재 실행 구조(runtime) 코드와 과거 실험(historical experiment) 코드는 같은 `src/`, `config/`, `scripts/`, `tests/`
계층에 함께 있다. 과거 실험 코드는 frozen 산출물·manifest·테스트가 소스 경로와 SHA-256을 기록하고 있어
경로를 옮기지 않고 보존한다. 이 문서는 파일 위치 대신 **역할**을 명시한다.

분류 근거는 이름이 아니라 실제 import 관계다. 실행 구조의 기준 진입점은 `src.api.app`이며,
`tests/test_runtime_import_boundary.py`가 이 경계를 검사한다.

| 표기 | 의미 |
|---|---|
| **실행** | `src.api.app`이 사용하는 현재 실행 구조 |
| **데이터 준비** | DB 적재·전처리·문헌 corpus 구축 등 실행 전 단계 |
| **과거 실험** | frozen baseline 재현과 연구 이력 보존용. 수정·이동하지 않는다 |

## src/

| 경로 | 분류 | 역할 |
|---|---|---|
| `api/` | 실행 | FastAPI endpoint, 의존성 구성, 공개 응답 계약 |
| `graph/` | 실행 | LangGraph workflow, node, state, Tool Input Resolver, Phase A 해석 workflow |
| `interpretation/` | 실행 | 규칙 우선 질문 해석, 운동 후보 검증, 추가 확인 흐름 |
| `routing/contracts.py`, `deterministic.py`, `runtime.py`, `runtime_language.py`, `interpretation_router.py` | 실행 | 규칙 기반 Router와 한국어 입력 처리. `deterministic.py`는 frozen Router baseline이면서 현재 Router다 |
| `routing/baseline.py` | 과거 실험 | Router baseline v1 runner |
| `tools/` | 실행 | Training Log / Metric / Literature Tool |
| `agent/contracts.py`, `agent/executor.py` | 실행 | Tool 인자 schema와 결정론적 Tool 실행기. 패키지 이름과 달리 현재 실행 구조가 사용한다 |
| `agent/planner.py`, `agent/graph.py`, `agent/baseline.py` | 과거 실험 | Router vs LLM Planner 비교 |
| `retrieval/bm25.py`, `dense.py`, `hybrid.py`, `postgres.py`, `rrf.py` | 실행 | Dense + BM25 + RRF 검색 |
| `retrieval/runtime_config.py` | 실행 | 실행 구조가 읽는 frozen embedding run 식별자 |
| `llm_support.py` | 실행 | 실행 provider 공용 OpenAI 응답 처리 함수 (과거 실험 원본의 동작 동일 사본) |
| `retrieval/baseline.py`, `hybrid_baseline.py` | 과거 실험 | Dense / Hybrid baseline v1 runner |
| `grading/runtime_*.py` | 실행 | Runtime Evidence Grader (계약, provider, deterministic finalizer) |
| `grading/` 그 밖의 파일 | 과거 실험 | Grader v1 (`baseline`, `contracts`, `evaluation`, `provider`), v2 (`*_v2`, `v2_*`), v2.1 (`*_v21`, `v21_*`) |
| `recovery/` | 실행 | Recovery 질의 생성, 검증, 근거 결합 |
| `answer/` | 실행 | 최종 답변과 답변 보류 |
| `database/config.py` | 실행 | DB 접속 설정 |
| `database/bootstrap.py`, `loader.py`, `psql.py` | 데이터 준비 | 스키마 설치와 운동 기록 적재 |
| `metrics/definitions.py` | 실행 | e1RM·plateau 등 지표 정의 |
| `preprocessing/` | 데이터 준비 | 운동 기록 정규화와 eligibility 정책. `policy.py`는 지표 정의를 통해 실행 중에도 로드된다 |
| `literature/` | 데이터 준비 | PubMed/JATS 수집, chunk 구성, 문헌 DB 적재 |
| `evaluation/retrieval_metrics.py`, `routing_metrics.py` | 과거 실험 | 검색 / Router 평가 지표 |
| `evaluation/end_to_end_*.py`, `smoke/` | 과거 실험 | E2E v1/v2 평가와 smoke harness |
| `evaluation/validation.py`, `reference.py`, `report.py` | 과거 실험 | frozen 평가셋 구축·검증 |

## 현재 남아 있는 실행 구조 ↔ 과거 실험 결합

현재 분리 상태는 **직접 의존성 일부 분리**다. 실행 provider와 Literature Tool이 과거 실험 모듈을
직접 import하던 부분만 분리했고, 다음 과거 실험 모듈은 `import src.api.app` 시 여전히 함께 로드된다.

| 로드되는 과거 실험 모듈 | 로드 경로 | 실행 동작 관여 |
|---|---|---|
| `src.agent.planner`, `src.agent.graph` | `src/agent/__init__.py` (`agent.contracts`/`agent.executor` import 시) | 없음 (패키지 import 부수효과) |
| `src.grading.contracts`, `src.grading.v2_contracts` | `src/grading/__init__.py` (`grading.runtime_*` import 시) | 없음 (패키지 import 부수효과) |
| `src.retrieval.baseline` | `src/retrieval/__init__.py`, `retrieval/hybrid.py` (`CORPUS_VERSION`, `EMBEDDING_RUN_ID`, `sha256_file`) | 있음 (`hybrid.py`가 frozen 문헌 자산 검증에 사용) |
| `src.evaluation.retrieval_metrics` | `src/retrieval/baseline.py` | 없음 (`baseline.py` import 부수효과) |

`__init__.py` 정리와 `retrieval/hybrid.py` 분리는 이번 작업 범위에서 제외했으며 별도 작업으로 진행한다.

실행 provider가 쓰는 OpenAI 응답 처리 함수(`estimate_cost_usd`, JSON 추출)는 `src/llm_support.py`의
동작 동일 사본을, Literature Tool의 `EMBEDDING_RUN_ID`는 `src/retrieval/runtime_config.py`를 사용한다.
과거 실험 쪽 원본은 그대로 두며, `tests/test_runtime_llm_support.py`가 사본과 원본의 동작 일치를 검사한다.

결합 목록이 바뀌면 이 표와 `tests/test_runtime_import_boundary.py`를 함께 수정한다.

## frozen 기록 해시와 현재 소스의 불일치

일부 frozen manifest는 기록 당시 실행 소스 파일의 SHA-256을 함께 고정한다. 실행 구조가 계속 바뀌므로
현재 소스와 일치하지 않는 항목이 있다. **manifest와 해시 검사는 수정하거나 우회하지 않는다.** 과거 해시를
현재 파일에 맞춰 다시 쓰면 frozen 기록의 의미가 달라지기 때문이다.

아래 표는 직접 의존성 분리 작업 직전(커밋 `dcc7bb2`)과 직후를 비교한 것이다.

| manifest | 작업 전부터 불일치 | 직접 의존성 분리로 추가된 불일치 |
|---|---|---|
| `reports/baselines/end_to_end_baseline_v1_preregistration/manifest.json` | `agent/executor.py`, `answer/generator.py`, `evaluation/end_to_end_runner.py`, `graph/nodes.py`, `graph/state.py`, `graph/workflow.py` | `answer/provider.py`, `grading/runtime_provider.py`, `recovery/provider.py`, `tools/literature.py` |
| `reports/baselines/end_to_end_baseline_v2_preregistration/manifest.json` | `graph/nodes.py`, `graph/state.py`, `graph/workflow.py`, `tests/test_end_to_end_v2_preregistration.py` | `answer/provider.py`, `grading/runtime_provider.py`, `recovery/provider.py`, `tools/literature.py` |
| `reports/diagnostics/retrieval_v1/protected_hashes_before.json`, `protected_hashes_after.json` | 없음 | `answer/provider.py`, `grading/runtime_provider.py`, `graph/tool_input_provider.py`, `interpretation/provider.py`, `recovery/provider.py`, `tools/literature.py` |

(`tests/`로 시작하지 않는 경로는 `src/` 생략)

영향:

- **E2E v1/v2 재실행은 현재 코드에서 차단된다.** `src/evaluation/end_to_end_runner.py`의
  `verify_preregistration`이 모든 기록 해시를 검사하고 불일치 시 `Preregistered artifact changed`로 중단한다.
  이 차단은 작업 전부터 있었고(위 "작업 전부터 불일치" 열), 이번 변경은 불일치 파일을 추가했을 뿐이다.
- **Retrieval v1 진단을 같은 출력 디렉터리에 다시 실행하면** `scripts/diagnose_retrieval_v1.py`의 시작 해시 검사에서
  중단된다. `protected_hashes_*.json`은 진단 실행 당시의 기록으로 보존한다.
- **과거 실험 재현**에는 기록된 모든 해시가 일치하는 별도 체크아웃이 필요하다. 현재 git 이력의 어떤 커밋도
  E2E v1/v2 manifest의 기록 해시 전부와 일치하지 않으며(manifest에는 git에 포함되지 않은 로컬 파일도 있다),
  따라서 재현에는 기록 당시 상태를 별도로 보존한 작업 디렉터리가 필요하다. 현재 작업 트리에서 해시를 맞추려 하지 않는다.

## config/

| 파일 | 분류 |
|---|---|
| `final_answer*`, `question_interpreter*`, `recovery_agent*`, `runtime_evidence_grader*`, `tool_input_resolver*` | 실행 (provider 설정·prompt) |
| `runtime_exercise_relations_v1.json`, `runtime_input_language_v1.json`, `exercise_selection_v1.json` | 실행 |
| `router_baseline_v1.json` | 실행 + 과거 실험. 현재 Router가 로드하는 frozen 설정 |
| `exercise_aliases_v1.csv`, `preprocessing_v1.json` | 실행 + 데이터 준비 (frozen) |
| `literature_corpus_v1.json`, `literature_selection_v1.json` | 데이터 준비 |
| `agent_planner_v1*` | 과거 실험 (Router vs LLM Planner) |
| `grader_v1*`, `grader_v2*`, `grader_v2_1*` | 과거 실험 (Grader). `grader_v2_1_design.json`은 코드 참조가 없는 설계 기록 |
| `end_to_end_baseline_v1.json`, `end_to_end_baseline_v2.json` | 과거 실험 (E2E) |

## scripts/

| 분류 | 파일 |
|---|---|
| 실행 확인 | `run_api_integration_smoke.py` |
| 데이터 준비 | `setup_postgres.py`, `load_postgres.py`, `load_literature_postgres.py`, `setup_dense_retrieval.py`, `preprocess_workouts.py`, `build_literature_corpus.py`, `search_literature.py`, `eda_workouts.py`, `test_postgres_integration.py` |
| 과거 실험: Retrieval | `run_dense_baseline.py`, `build_dense_failure_analysis.py`, `run_hybrid_baseline.py`, `validate_hybrid_baseline.py`, `diagnose_retrieval_v1.py` (읽기 전용 진단) |
| 과거 실험: Router vs Planner | `run_router_baseline.py`, `validate_router_baseline.py`, `run_agent_baseline.py`, `validate_agent_baseline.py` |
| 과거 실험: Grader | `run_grader_baseline.py`, `validate_grader_baseline.py`, `*grader_v2*`, `*grader_v21*`, `*grader_v2_1*` |
| 과거 실험: E2E | `run_end_to_end_baseline.py`, `run_end_to_end_baseline_v2.py`, `run_end_to_end_smoke.py` |
| 과거 실험: 평가셋 | `freeze_evaluation_set.py`, `validate_evaluation_set.py`, `build_evaluation_evidence_human_review.py`, `migrate_evidence_groups.py` |

## tests/

| 분류 | 파일 |
|---|---|
| 실행 | `test_api_*`, `test_answer_integration`, `test_final_answer`, `test_abstention`, `test_graph_*`, `test_tool_input_resolver`, `test_question_interpretation`, `test_exercise_*`, `test_runtime_*`, `test_recovery_*`, `test_phase8_tools` |
| 데이터 준비 | `test_database`, `test_literature`, `test_preprocessing`, `test_eda_workouts` |
| 과거 실험 | `test_routing`, `test_phase10_agent`, `test_phase11_grader`, `test_phase11b_*`, `test_dense_retrieval`, `test_hybrid_retrieval`, `test_retrieval_metrics`, `test_retrieval_v1_diagnostics`, `test_evaluation`, `test_evidence_human_review_v2`, `test_end_to_end_*`, `test_e2e_smoke_harness`, `test_post_baseline_v1_remediation` |
| 공용 fixture | `answer_test_support.py`, `graph_test_support.py` |

일부 테스트는 실행 코드와 과거 실험 코드를 함께 검사한다(예: `test_dense_retrieval`, `test_hybrid_retrieval`은
실행 검색 모듈도 사용한다). frozen 무결성 테스트는 소스·설정 파일의 경로와 SHA-256을 고정하므로
해당 파일은 이동하거나 수정하지 않는다.

## 새 코드를 추가할 때

- 실행 구조 코드는 과거 실험 모듈을 새로 import하지 않는다. 공용 기능이 필요하면 실행 전용 위치에 둔다.
- 새 실험은 기존 파일을 수정하지 않고 새 버전 파일·설정·보고서로 추가한다 (`AGENTS.md` 참조).
