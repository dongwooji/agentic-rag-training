# Phase A 근거 필요성 분리 계약 v1

기준선: `685e2b9194419fbf757df2df2156299b77e4afe3` (PR #16~#19 merge 후 master).
변경 후 SHA와 검증 결과는 구현 보고서에 기록한다. 이 문서는 구현 전에 작성한 계약이다.
서비스 기본값은 `legacy`. `phase_a`에서만 새 의미 계약을 사용한다.
기존 config/prompt는 보존하며 새 version을 선택한다. 기존 Phase A 재현은 기존
provider config와 `semantics_enabled=False`를 사용한다.

## 책임과 실행 의미

기존 좁은 full-match 규칙 → 부족할 때만 기존 Interpreter LLM 최대 1회.
같은 호출에서 목적/근거 필요성과 실행 인자를 추출한다. 코드는 두 결과를 별도로 검증한다.
근거 source는 `training_records`, `literature`뿐이다. Metric은 source가 아닌 계산 operation이다.
Tool 선택 순서, Metric 계산, 검색, Grader, Recovery, 종료 정책은 기존 흐름을 재사용한다.
공유 `nodes.py`, `workflow.py`는 변경하지 않는다. Phase A 전용 adapter에서만 결합한다.

`required_literature = literature_needed_for_answer OR explicit_literature_request == required`.
`optional_literature = explicit_literature_request == optional AND NOT required_literature`.
required와 optional은 중복되지 않는다. 개인 기록 need는 기록/Metric 의미 및 별도 근거 need로
결정하며 운동명 등장만으로 결정하지 않는다. 기존 `literature_requested`는 실제 선택할
required 또는 optional Literature의 합집합으로 정규화한다.
**optional은 관찰용 구분이다.** 실행되면 기존 문헌 Grader/Recovery/종료 의미가 그대로 적용되어
전체 응답을 막을 수 있다. optional 부족 시 비차단 진행은 이번 구현에 포함하지 않는다.

## 복합 질문 answer_type

하나의 주 answer_type 우선순위: `out_of_scope > advisory > causal > evaluative > descriptive`.
`general`은 개인 기록 없이 일반 지식 설명만 요구하는 경우다. 일반 과학 질문도 조언/원인/평가가
주 목적이면 해당 type을 사용한다. 지원 범위 밖 필수 요청이 섞인 질문은 partial answer 없이
`out_of_scope`다. 문장 순서보다 답변에 필요한 가장 강한 판단을 우선한다.

| 합성 예시 | 주 type | 독립적으로 보존할 need |
|---|---|---|
| 케이블 프레스 기록을 나열하고 그 수행량을 평가해줘 | evaluative | training_records + literature |
| 수행 횟수를 정리하고 변화 원인을 설명해줘 | causal | training_records + literature |
| 변화 원인과 다음 훈련 방안을 알려줘 | advisory | training_records + literature |
| 훈련 적응의 측정 방법을 설명해줘 | general | literature |

answer_type은 evidence channel의 단독 결정 변수가 아니다. `training_records_needed`,
`literature_needed_for_answer`, `explicit_literature_request`, 원문 `source_spans`를 따로 보존한다.
복합 의미를 하나의 type으로 충분히 설명할 수 없으면 위 우선순위로 주 type을 택하고
여러 source span과 모든 필요 source/기존 operation을 보존한다. 복수 Metric은 여전히 되묻는다.
핵심 목적/근거 need 자체를 식별할 수 없을 때만 type=null, purpose clarification이다.
type은 이미 알지만 핵심 근거 need만 미확정이면 type을 보존하고 evidence_need만 되묻는다.
지원 범위 안이라고 하면서 기록/문헌 need가 모두 없는 불일치도 채널을 추측하지 않고 근거 need를 확인한다.

## Interpreter clarification 계약

`intent unknown → purpose clarification`: answer_type 또는 핵심 evidence need를 정말 식별할 수
없을 때만 `question`/`evidence_need`를 미확정으로 둔다.
`intent known + argument missing → argument clarification`: 운동 후보 복수, 날짜/기간, N,
Metric 인자 부족은 목적 clarification 사유가 아니다. 이미 식별한 type/source need를 보존하고
해당 argument만 되묻는다. 범위 밖이면 운동/기간을 되묻지 않고 Tool 없이 지원 범위를 안내한다.
기록에서 직접 답할 수 있는 서술(언제/얼마/어느 주)에는 문헌을 추가하지 않는다.
적절성/효과/원인/조언에 일반 과학 근거가 필요한지는 의미로 판단한다. 단어 존재로 채널을
선택하는 새로운 regex Router는 만들지 않는다. LLM의 의미 출력은 typed schema와 원문 span 검증을 거친다.

## 시간 기본값

`recent_window_weeks=8`은 새 설정에 둔다. 마지막 **저장 기록일**을 포함하는 56일 범위:
end=anchor, start=anchor - (weeks*7 - 1)일. wall-clock 오늘 날짜를 사용하지 않는다.

| 조건 | 처리 |
|---|---|
| 명시적 날짜/기간 | 기존 grounding/normalization 우선 |
| 전체 기록/최고 기록/first-last N 등 자체 시간 의미 | 기본값 미적용 |
| 최근/요즘/recent의 실제 상대 기간 | 코드가 마지막 저장일 기준 최근 8주 |
| 기간 생략 + 현재 수준 평가 등 분석창 필요 | evaluative/causal/advisory이며 승인된 operation이고 Interpreter가 분석창 필요를 명시한 경우만 적용 |
| 그 밖의 기간 생략 | 기존 전체 기록 의미 |

승인된 분석창 operation: estimated_1rm, weekly_volume, weekly_frequency, plateau_candidates.
training_gap 및 first_last_n_session_median_e1rm에는 생략 시 최근 창을 적용하지 않는다.
새 상대 날짜 문법은 추가하지 않는다. 해석되지 않은 다른 상대 기간은 clarification을 유지한다.
상대 기간은 기록 요청의 time_condition.source_text에 추출된 원문 구절에서만 확인한다.
다른 절의 최근 문헌/발표 시점은 개인 기록 기간으로 바꾸지 않는다.
LLM이 원문에 없는 절대 날짜를 만들면 기존대로 거절/기간 clarification한다.
기본값 적용은 인자 clarification 중에도 trace/provenance와 사용자 응답에 명시한다.
기록이 없거나 기준일을 얻지 못하면 날짜를 만들어내지 않는다.

## 최종 Gold schema: phase_a_evidence_gold_v1

질문은 JSONL `{case_id, pair_id, question}`. Gold JSON은 `{version, cases:[...]}`.
질문과 Gold는 별도 파일이며 runtime은 Gold를 읽지 않는다. 실제 acceptance 질문/Gold는 사용자가
별도로 작성·freeze한다. 구현 중 열거나 사용하지 않는다. 아래는 필드 계약이며 실제 Gold가 아니다.

| 필드 | 허용값 / 의미 |
|---|---|
| case_id | 비어 있지 않은 고유 string |
| pair_id | 비어 있지 않은 string; 대비 쌍 식별 |
| expected_answer_type | descriptive/evaluative/causal/advisory/general/out_of_scope 또는 목적 미확정 때만 null |
| required_evidence_sources | 중복 없는 source 배열; []=필수 source 없음, null 불가 |
| optional_evidence_sources | 중복 없는 literature 배열; []=없음, null 불가; required와 교집합 금지 |
| acceptable_record_operations | 아래 기존 operation 또는 null의 **비어 있지 않은 대안 배열** |
| acceptable_metric_operation_sets | 아래 기존 Metric 집합의 **비어 있지 않은 대안 배열** |
| clarification_required | boolean |
| allowed_clarification_fields | 아래 필드의 중복 없는 배열; []=허용 없음, null 불가 |
| expected_final_behavior | answer / clarification / abstain |
| expected_default_recent_window | boolean; 코드 기본창 적용 여부, Tool 실행 여부와 별개 |

Record: exercise_records / exercise_first_last / get_session / list_sessions.
Metric: estimated_1rm / first_last_n_session_median_e1rm / weekly_volume /
weekly_frequency / training_gap / plateau_candidates. 대안이 있어도 복수 Metric 실행 기능을 추가하지 않는다.
clarification 필드: question, evidence_need, canonical_exercise_name, candidate_exercises,
exercise_resolution_status, time_condition, start_date, end_date, n_sessions, requested_analyses,
comparison, record_operation, session_id, literature_subquestion, user_assumptions.

### 대안 채점

실제 실행된 Record operation이 대안 중 하나와 같아야 한다. `[null]`은 Record 실행 없음이다.
outer []/null은 invalid. null과 실제 operation의 혼합 대안은 지원하지 않는다.
실제 실행된 Metric operation 집합이 대안 집합 하나와 **정확히** 같아야 한다.
순서는 무시하고 중복은 오답이다. outer []/null 및 inner null은 invalid.
`[[]]`은 Metric 없음이다. 한 대안뿐이어도 목록을 쓴다. 서로 같은 대안을 중복 기록하지 않는다.
`[[], [weekly_volume]]`처럼 Metric 없는 실행도 허용할 수 있다.
추가 Metric은 완성된 실제 집합이 사전에 기록한 대안 하나와 정확히 일치할 때만 허용된다.
대안들의 합집합이나 subset 채점은 하지 않는다. expected_record_operation과
expected_metric_operations는 제거한다. alias/중복 source of truth를 만들지 않는다.
복합 질문은 주 type을 한 번 채점하고 source/operation/동작을 독립적으로 채점한다.
optional source 미실행은 누락이 아니고 실행은 불필요 추가가 아니다. 실행된 경우 현재 graph의
차단 의미는 그대로이므로 final behavior는 별도 채점한다. execution_failure는 동작 정답이 아니다.
Tool operation 선택 정확성과 실행 성공은 별도로 기록한다. clarification 때 선택된 계획을
실행된 Tool로 세지 않는다. 근거 채널 need와 실제 실행 채널도 별도로 기록한다.
required-channel omission의 주 지표는 required source와 식별한 need의 차이다. 실제 실행 누락은
별도 지표다(정당한 clarification에서는 실행하지 않기 때문). answer 동작 정답은 answer_ready뿐 아니라
필수 source 실제 실행 및 허용된 Record/Metric operation 일치도 필요하다. clarification 동작 정답은
허용 필드 확인과 실행 operation 일치가 필요하다. 기존 Phase A에 없는 answer_type은 추정하지 않고
보조 type 지표의 채점 불가(null)로 기록한다. 두 설정의 전체 동작 분모는 같게 유지한다.

## debug trace: phase_a_semantics_v1

위치: `response_metadata.trace.interpreter.phase_a`.
바깥 `query_debug_trace_v1`은 유지한다. 새 Phase A의 debug 응답에는 성공/되묻기/보류/실패 모두
동일 구조를 제공한다. baseline/legacy는 이 중첩 필드를 생략한다. 필드명은 이 version에서 고정한다.

| 경로 | 타입 / null | 의미 |
|---|---|---|
| version | literal phase_a_semantics_v1 | 중첩 계약 version |
| parsing_source | rule / llm / null | 기존 내부 parsing_source; 해석 결과를 얻기 전에도 시도 경로, 알 수 없을 때만 null |
| evidence_need.status | resolved / clarification_required / unavailable | 목적·근거 need 식별 상태 |
| evidence_need.answer_type | 위 6종 / null | 주 목적 |
| evidence_need.literature_needed_for_answer | boolean / null | 의미상 일반 과학 근거 필요 |
| evidence_need.explicit_literature_request | none / optional / required / null | 명시적 요구 |
| evidence_need.required_sources | source 배열, null 불가 | 필수 근거 |
| evidence_need.optional_sources | source 배열, null 불가 | 관찰용 선택 근거 |
| evidence_need.source_spans | string 배열, null 불가 | 질문 원문 구절; 길이 제한·secret 제거 적용 |
| evidence_need.unresolved_fields | question / evidence_need 배열, null 불가 | 목적 미확정 필드; 인자 필드와 분리 |
| argument_resolution.status | ready / clarification_required / not_applicable / not_started / failure | 실행 인자 확정 상태 |
| argument_resolution.unresolved_fields | 위 clarification 필드 중 argument 배열 | purpose 필드 제외 |
| default_time_window.applied | boolean | 코드 기본창 적용 여부 |
| default_time_window.policy | recent_record_anchored / null | 기본 정책 |
| default_time_window.trigger | relative_recent / operation_default / null | 적용 조건 |
| default_time_window.recent_window_weeks | positive integer / null | 설정의 주 수 |
| default_time_window.anchor_date | ISO YYYY-MM-DD / null | 마지막 저장 기록일 |
| default_time_window.start_date | ISO YYYY-MM-DD / null | 포함 시작일 |
| default_time_window.end_date | ISO YYYY-MM-DD / null | 포함 종료일 |
| scope.out_of_scope | boolean / null | 지원 범위 판정; 미확인 null |
| scope.reason_code | 아래 안전한 enum / null | 범위 밖 사유 |

reason_code: unsupported_domain / unsupported_medical_request /
unsupported_personalized_prescription / unsupported_data_or_operation.
scope=false이면 reason=null; true이면 위 enum 필수. 적용 안 된 window의 나머지 필드는 모두 null.
목적 미확정이면 argument는 not_started, scope 미확인. 범위 밖이면 argument는 not_applicable.
원시 기록/예외/provider detail/프롬프트/비밀값은 trace에 넣지 않는다.

`POST /query/clarify`는 `debug: boolean=false`를 선택 필드로 추가한다. true일 때 `/query`와 같은
projection을 사용하며 **선택 반영 후** 인자 상태와 기존 parsing_source를 반환한다.
Interpreter 재호출은 하지 않는다. false/생략 때 기존 공개 응답을 유지한다.

## 검증·평가 순서

synthetic 단위 → 전용 통합(TestClient/mock) → 공개 CI → 로컬 전체 테스트.
실제 LLM/REST/DB나 acceptance 평가는 이번 구현에서 실행하지 않는다.
사용자가 frozen acceptance 제공 후 기준 Phase A/변경 Phase A를 동일 질문에 각 1회 실행한다.
단순 JSON/JSONL 오프라인 평가기는 hash, 기준/변경 SHA, 실행 순서, 설정, 호출 수,
시간, input/output token, 비용을 저장한 관찰 파일만 읽는다. Gold는 관찰 이후 채점에서만 사용한다.
필수 기준: required-channel omission 감소 AND 답변 가능한 질문의 부당한 clarification/abstain 감소
AND final behavior accuracy 감소 없음 AND execution_failure 증가 없음.
기존 interpretation_eval_v1은 주 판정 후 diagnostic only. 결과를 보고 재튜닝하지 않는다.

오프라인 명령: `python scripts/score_phase_a_semantics.py --manifest <manifest.json> --output <new-report.json>`.
manifest는 baseline_sha/changed_sha, files.questions/gold/before/after 각각의 path와 SHA-256을 포함한다.
questions는 JSONL, 나머지는 JSON. 경로는 manifest 기준 상대 경로다. 관찰 JSON은 commit_sha,
run_count_per_case=1, execution_order, model_config_hashes, stage_llm_calls, usage, latency_ms,
observations를 포함한다. usage는 단계별 input/output/cached/total token 및 추정 USD를 기록한다.
미관측 값은 null이며 0으로 바꾸지 않는다. observations는 `observe_state`로 실제 state를 투영한다.
baseline은 지정한 commit의 별도 소스 export에서 실행해야 한다. 현재 코드의 호환 모드는
회귀 테스트용이며 그 실행을 기준선 SHA에서 실행한 관찰이라고 기록하지 않는다.
평가기는 외부 호출을 하지 않고 hash/commit/순서/문항당 한 관찰을 확인하며 기존 보고서를 덮어쓰지 않는다.
JSON Schema: `docs/schemas/phase_a_evidence_gold_v1.schema.json`,
`docs/schemas/phase_a_semantics_v1.schema.json`. 교차 필드 의미는 본 계약 및 typed validator를 따른다.

구현 중 합성 10쌍 파일: `tests/fixtures/phase_a_semantics_synthetic_v1.json`.
실행 전 SHA-256: `6469ee16fd13fec67efb29efad31908985f6e0d3d75a31bfa917500ccd99795f`.
이 파일은 mock 계약 검증용이며 주 acceptance 평가셋/Gold가 아니다.
