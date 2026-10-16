# Phase A operation별 필수 인자 contract

이 변경은 Phase A evidence acceptance v1 (`6c68ea996f967bcdda47bfed2671aae1ae583969`) 결과를 분석한 뒤 수행한 post-hoc 수정이다. 기존 acceptance는 재실행하지 않았으므로, 그 acceptance 성능 수치를 이 수정 후 코드의 성능으로 해석하지 않는다.

- acceptance-evaluated SHA: `6c68ea996f967bcdda47bfed2671aae1ae583969`.
- 이번 branch base SHA: `91cc57f8c936138e9f71beb064f9358c18545444`
  (특정 운동 training_gap의 세션 입력 수정 PR #23 head).
- 수정 후 최종 head SHA 및 공개/전체/CI 결과: 이 문서를 포함한 commit의 PR 본문과
  `reports/posthoc_sequence_v1/`의 최종 실행 기록에 구분해서 기록한다.

## 실행 contract

`config/phase_a_operation_arguments_v1.json`은 실행에 필요한 인자와 상위 Record
operation만 정의한다. 자연어 routing 규칙이나 특정 평가 질문은 포함하지 않는다.
validator와 기존 binding이 같은 Metric → Record 연결 정보를 읽는다.

| Operation | 실행 필수 인자 | 상위 Record operation |
|---|---|---|
| exercise_records | canonical_exercise_name | — |
| exercise_first_last | canonical_exercise_name | — |
| get_session | session_id | — |
| list_sessions | 없음 | — |
| estimated_1rm | records_source | exercise_records |
| first_last_n_session_median_e1rm | records_source, n_sessions | exercise_records |
| weekly_volume | records_source | exercise_records |
| weekly_frequency | records_source | list_sessions |
| training_gap | records_source | list_sessions |
| plateau_candidates | records_source | exercise_records |

Metric은 상위 Record의 필수 인자도 상속한다. `records_source`는 실행 코드가 공급하므로
사용자에게 묻지 않는다. `n_sessions`의 Tool 기본값은 유지하지만, 처음/마지막 N 비교에서
사용자 N을 요구하는 기존 Phase A 계약은 유지한다. config에 기존 Tool operation을
기술하는 것이 Phase A의 특정 session 조회 등 기존 지원 제한을 해제하는 것은 아니다.

## 차단 조건과 원문 제약

인자 되묻기의 기준은 operation 필수 인자와 미해결 사용자 제약의 합집합이다.
LLM의 `unresolved_fields`를 그대로 차단 목록으로 사용하지 않는다.

- 운동: 운동별 세트 조회에 필수다. 세션 operation에서는 원문의 운동 표현,
  catalog·alias·기존 restriction 신호가 있으면 그 범위를 반드시 resolve한다.
  모호한 운동을 전체 기록으로 넓히지 않는다.
- 기간: 기존 날짜 grounding/normalization, 미확정 기간, 명시적 범위 검사를 유지한다.
  최근 8주 정책과 날짜 계산은 변경하지 않는다.
- N: 해당 operation의 필수 N 또는 원문에 있는 세션 수 제약이면 보존한다.
  operation이 N을 쓰지 않더라도 원문의 마지막 N세션 조건을 버리고 전체 기록으로
  실행하지 않는다. 이미 추출된 N이 있으면 기존 숫자 grounding 검사를 반드시 통과해야 한다.
- 비교·가정·세션 ID: 원문의 비교 요구 및 기존 구조화 제약에 해당하면 보존한다.
  실제 지원 제한과 원문 span 검사는 기존 validator가 계속 수행한다.
- operation/purpose/channel 선택 오류, 복수 Metric, 문헌 하위 질문 검사는 기존 정책을 유지한다.

선택된 operation에 없는 추가 인자, Tool에서 조회·계산할 값, 답변에서 산출할 값은
입력 부족으로 취급하지 않는다. 예를 들어 weekly_frequency의 불필요한 n_sessions나
세트 증가 권고량을 사용자에게 요구하지 않는다. 이는 권고량을 코드가 계산한다는 뜻이 아니며,
답변 생성과 문헌 충분성 판단은 기존 흐름을 사용한다.

global training_gap의 별도 validator 예외는 제거했다. list_sessions의 필수 운동 인자가
없다는 같은 contract로 처리하고, 특정 운동을 명시하면 별도 원문 제약으로 보존한다.
동일한 최소 실행 contract를 가진 global weekly_frequency도 운동 없는 세션 조회를 사용할 수 있다.
`global_training_gap_v1` 문자열은 기존 provenance 소비자를 위한 config의 호환 표시일 뿐이며,
운동 validation을 우회하는 별도 실행 분기는 없다.

## 진단 및 유지 정책

내부 `QuestionInterpretation.argument_contract_normalization`에 version,
`required_user_arguments`, `explicit_constraint_fields`, `ignored_llm_fields`를 기록한다.
공개 response schema나 기존 versioned debug trace 필드명은 변경하지 않는다.
prompt 및 LLM의 InterpretationDraft schema도 변경하지 않는다.

`정체`라는 표현을 plateau_candidates에 강제 연결하는 검사만 제거했다.
estimated_1rm으로 반대 방향 강제 연결하지 않는다. 원문에 명시된 영어 operation 이름의
기존 누락 검사는 유지한다. plateau_candidates 자체와 복수 Metric 제한도 유지한다.

answer_type/evidence required·optional, out_of_scope, 운동 후보 선택, 최근 8주,
retrieval/embedding/corpus/벡터/Gold, Tool 계산, Grader/Recovery, optional Literature blocking,
legacy workflow와 서비스 기본값을 변경하지 않는다. 공유 nodes.py/workflow.py는 수정하지 않는다.

## 검증과 한계

신규 synthetic/config/unit 및 mock Tool/API 통합 테스트만 사용한다. 기존 인라인 synthetic
입력 4곳은 실제 필수 N·명시적 비교·운동 필수 operation으로 바꾸고 assertion과 expected 값은
유지했다. frozen synthetic pair 파일 및 acceptance 입력/Gold/raw/decision은 변경하지 않았다.
실제 LLM/OpenAI·서비스 REST·DB 호출, acceptance/기존 interpretation 평가 재실행은 하지 않는다.

이 작업은 잘못된 복수 Metric 추출, 문헌 하위 질문 누락, 비교 기간 자체의 지원 확장,
optional Literature의 비차단 동작을 해결하지 않는다. 임의의 자연어 제약을 새로 추론하는
범용 계층도 추가하지 않는다. 제공된 typed 인자와 기존 원문 검증으로 표현할 수 없는 새로운
제약은 별도 contract 검토 대상이다. 테스트/CI 성공은 동결 평가 성능 향상의 증거가 아니다.

별도 발견: 특정 운동 weekly_frequency도 list_sessions의 exercise_names 입력을 받지만,
기존 Metric 필터는 exercise_name만 확인하므로 빈 결과가 될 수 있다. 작업 2의
`test_other_metrics_do_not_gain_new_session_input_semantics[weekly_frequency]`에서
합성 세션의 빈 결과를 확인했다. training_gap 외 Metric을 변경하지 않는 요청 범위에 따라
이 문제는 수정하지 않았다. 필수 인자 검증을 통과했다는 것이 이 계산 문제의 해결을 뜻하지 않는다.
