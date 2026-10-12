# Phase A validation 범위 최소 수정

일반 문헌 질문에 개인 기록의 분석 기간을 요구하고, 모델의 개인 기록 flag가 다른 해석 필드와 다르다는 이유로 실행을 거절하던 범위를 수정했다. 실제 관찰 결과를 새로운 테스트 입력으로 사용하지 않고 별도의 synthetic 질문과 가짜 catalog로 계약만 검증했다. 실제 Phase A 재평가는 수행하지 않았다.

## 기간 검사

`time_condition.scope=unresolved`의 기간 확인과 `all_records`의 최근/비교 기간 확인은 코드가 파생한 개인 기록 필요 여부가 true인 경우에만 적용한다. 기록 조회와 Metric 계산에는 기존 기간 확인을 유지하고, 일반 literature-only 요청에는 기간 확인을 새로 추가하지 않는다.

기존 TimeCondition의 세 상태를 유지했다. `not_applicable`을 추가하거나 모델 입력 schema·prompt를 바꾸지 않았다. 문헌 질문의 `unresolved`도 기록 기간이 필요 없으면 그대로 통과할 수 있다. 실제로 명시된 날짜의 형식·원문 grounding·순서·범위 누락 검사와 숫자/N 검증은 문헌 질문에서도 유지한다. 모델이 제시한 다른 의미적 확인 요구는 지우지 않는다.

## 개인 기록 필요 여부 파생

`personal_record_requested`의 최종값은 모델 bool 대신 다음 기존 해석 의미에서 코드가 계산한다.

| 파생 신호 | 판단 기준 |
|---|---|
| record_operation | 기존 schema의 기록 조회 작업이 명시됨 |
| requested_analyses | 구현된 Metric 계산 요청이 존재함 |
| explicit_personal_request | 기존 PERSONAL_REQUEST 신호가 원 질문에 존재함 |
| exercise_session_context | 후보 운동 또는 canonical 운동과 개인 기록 session_id가 함께 존재함 |

신호가 하나 이상이면 true, 없으면 false이다. 모델의 원래 true/false는 판단 조건으로 사용하지 않는다. 운동 후보만 있거나 일반 문헌에서 운동명이 등장한 것만으로는 true가 되지 않는다. 새로운 운동명/질문별 regex나 Router 규칙을 추가하지 않았다. session_id 원문 검증과 기존 Phase A의 특정 session 조회 제한도 유지한다.

검증 전에 flag를 정규화하므로 운동 후보/catalog 검사·운동 선택·기간 검사에는 동일한 파생값이 적용된다. 문헌의 후보도 원문 mention과 실제 catalog membership, canonical 충돌 검사를 거치지만 기록 조회를 위한 단일 후보 선택을 요구하지 않는다. 모델이 기록 조회 작업을 생략한 Metric은 기존 `bind_interpretation`이 Metric 종류에서 기록 작업을 파생하므로 추가 lookup 작업 확인을 요구하지 않는다. Metric 없는 기록 조회의 작업 누락은 계속 clarification 대상이다.

## 불일치 진단과 호환성

검증된 `QuestionInterpretation.personal_record_normalization`에 다음을 기록한다.

```json
{
  "supplied_value": false,
  "derived_value": true,
  "mismatch": true,
  "signals": ["requested_analyses"]
}
```

이는 내부 해석 결과/trace의 optional typed 진단 필드다. 기존 결과를 읽을 때는 없어도 된다. LLM이 작성하는 `InterpretationDraft`와 catalog 제한 입력 schema에는 진단 필드를 추가하지 않았다. 모델 bool 자체의 불일치로 GroundingError나 추가 확인을 만들지 않는다. 정규화로 해결된 `personal_record_requested` 확인 항목만 제거하고, 실제 기간·운동 모호성·비교 조건 등 다른 unresolved_fields와 grounding 검사는 보존한다.

Router, prompt, retrieval config·embedding·BM25·RRF, corpus·Gold·평가 데이터, 서비스 기본 interpretation mode는 변경하지 않았다. 기본값은 계속 legacy다. Tool 선택은 기존 deterministic Router와 인자 연결 코드가 수행한다.

## 새로운 synthetic 테스트

신규 `tests/test_phase_a_validation_scope.py`는 다음 26개 매개변수 조합을 검증한다. 질문은 새로 작성했으며 가짜 `Synthetic Pull` catalog를 사용한다. dev/dev-ko·held-out·기존 E2E 10문항을 불러오거나 신규 입력으로 복사하지 않았다.

| 검증 묶음 | 조합 수 |
|---|---:|
| 문헌-only unresolved/all_records 기간 확인 미추가 | 2 |
| 개인 log/Metric unresolved 기간 확인 유지 | 2 |
| false flag를 log/Metric에서 true로 정규화하고 기존 인자 연결 | 2 |
| 기존 개인 기록 신호 / 운동+session context 파생 | 2 |
| 문헌 운동 후보: 원 bool true/false, 단일/복수 후보 모두 개인 기록 오인 방지 | 4 |
| 정규화된 flag 확인 항목 제거와 다른 unresolved 항목 보존 | 1 |
| 동일 bool의 mismatch=false | 1 |
| Interpreter trace 직렬화와 LLM schema 불변 | 1 |
| 잘못된 catalog 후보: 문헌/개인 기록 | 2 |
| 원문 운동 mention / canonical 충돌 거절 | 1 |
| 문헌의 canonical label 단독 catalog 우회 거절: true/false | 2 |
| 날짜 원문 grounding·형식·역전 거절 | 3 |
| 문헌의 명시적 날짜 조건 누락 보류 | 1 |
| 없는 N·Metric에 적용할 수 없는 N 거절 | 1 |
| 원문에 없는 분석 source 거절 | 1 |
| 합계 | 26 |

전체 회귀 테스트는 기존 묶음을 그대로 실행한다. 테스트 삭제·assertion/예상값 변경·skip/xfail·marker 변경은 없다. 로컬 전체 테스트가 수행하는 기존 산출물 무결성 검증과 새로운 질문 검증을 구분한다. 기존 평가 질문을 새 수정의 튜닝 입력으로 사용하지 않는다.

## 검증 결과와 보호 대상

| 검증 | 결과 |
|---|---|
| 신규 synthetic 계약 | 26 passed (관련 회귀 실행에 포함) |
| 관련 단위·회귀 | 126 passed |
| 공개 전체 | 858 passed, 81 deselected |
| 로컬 전체 | 939 passed |
| 보호 파일 SHA-256 | 1,345개 모두 불변 |

workspace 테스트 수에는 작업 전부터 미추적이던 감사 테스트 19개가 포함된다. 해당 파일은 commit하지 않는다. 단위/회귀는 합성 provider 또는 mock/파일 기반이며 실제 OpenAI·운영 REST·DB 호출은 0회, 실제 LLM token·비용도 0이다. 이 변경의 실제 사용자 질문 성능은 주장하지 않는다. 실제 Phase A 재평가는 별도 새 평가 질문 세트가 freeze된 뒤의 작업이다.

검증용 보호 hash에는 config/prompt, corpus·selection/build·평가/Gold·baseline·human decision·기존 연구 보고서, 두 번의 기존 REST 관찰 결과, alias, Router/retrieval/API/graph/Grader/Recovery/답변 코드와 수정 대상 외 interpretation 코드가 포함된다. 기존 frozen 파일을 덮어쓰거나 새 연구 version을 생성하지 않는다. 기록은 새 로컬 `reports/phase_a_validation_scope/`에 보존한다.
