# Phase A evidence — HTTP clarification boundary / global training_gap 최소 수정

## 평가 기준선과 보존

Phase A evidence acceptance v1의 주 평가 결과는
`6c68ea996f967bcdda47bfed2671aae1ae583969` 기준이다.
그 이후의 HTTP clarification boundary 및 global training_gap 수정은
해당 acceptance 평가를 다시 실행하지 않았으므로,
기존 acceptance 결과를 수정 후 head의 성능 근거로 해석하지 않는다.

- acceptance-evaluated SHA: `6c68ea996f967bcdda47bfed2671aae1ae583969`.
- 평가 결과 PR #21 head: `f4cfc2a9f989cf3f5792e8d893d950647f8e02d5` (변경 없음).
- PR #21 base: `eval-base/phase-a-evidence-acceptance-v1`, 위 evaluated SHA에 고정.
- base 생성·PR retarget 확인을 먼저 수행한 뒤 PR #20 branch를 수정했다.
- bugfix 후 최종 PR #20 head의 full SHA와 CI 결과는 PR #20 본문 및 최종 실행 기록
  `reports/phase_a_evidence_bugfix_v1/completion.json`에 별도로 기록한다.
  평가 SHA와 수정 후 head를 동일한 평가 실행으로 취급하지 않는다.

평가 질문·Gold·raw result·decision·보고서 230개는 내용 디코딩 대신 SHA-256으로 보존을 확인했다.
재현용으로 허용된 HTTP 500 metadata 세 건만 분리했다. 세 metadata가 동일함을 확인하고
중복 없는 새 fixture를 만들었다. 질문·정답·답변은 fixture에 포함하지 않았다.

## 1. 공개 clarification metadata 변환

원인: Phase A의 기간 안내 단계가 내부 provenance에 `default_time_window`를 남기지만,
API는 `trace`만 제외한 나머지 내부 metadata를 `ClarificationMetadata`에 전달했다.
기존 공개 모델의 `extra=forbid`와 충돌해 `/query`와 `/query/clarify`에서 HTTP 500이 가능했다.

변경 파일은 `src/api/app.py`다. 아래 기존 공개 필드만 명시적으로 투영한다.

- `response_mode`, `clarification_required`
- `clarification_id`, `clarification_type`, `options`, `expires_at`

내부 전용 필드는 버리고 공개 필드 값은 기존 모델로 계속 검증한다. 내부 `trace`도 전달하지 않고,
`debug=true`일 때만 기존 안전한 trace 생성기를 호출한다. 구조화된 기본 기간 정보는
`phase_a_semantics_v1.default_time_window`에서 관찰한다. 공개 response schema와 version은
확장하지 않았다. 내부 provenance, 기존 기간 결정 및 안내문, exercise 선택 응답 필드는 유지한다.

저장 metadata를 strict 공개 모델에 직접 넣으면 종전의 `extra_forbidden(default_time_window)`을
여전히 재현한다. 수정은 schema 완화가 아니라 API 투영 경계에만 있다.

## 2. 전체 기록 training_gap의 운동 인자

원인: validator는 개인 기록 요청이면 운동을 항상 요구했다. 그러나 기존 binding은
`training_gap`을 `list_sessions → compute_metrics(training_gap)`으로 연결하고,
두 기존 인자는 `canonical_exercise_name=None`인 전체 세션 계산을 이미 지원한다.

변경 파일은 `src/interpretation/validator.py`다. 아래 조건을 모두 만족할 때만 운동 요구를 생략한다.

1. 요청 Metric이 하나이고 `training_gap`이다.
2. 추출된 운동 표현·canonical 이름·후보가 모두 없다.
3. 기존 정규화 실패나 운동 제한 규칙이 없다.
4. 기존 alias/canonicalizer와 제공된 catalog에서도 원문의 특정 운동 단서가 없다.

이 경우 운동 관련 미해결 필드만 제거하고 `global_training_gap_v1` 적용 이력을 남긴다.
개인 기록 need는 보존한다. 기간·숫자·Metric 수·원문 span 등 다른 검증은 그대로 수행한다.
기존 binding을 재사용해 전체 세션을 조회한다. 새 자연어 패턴이나 prompt 예시는 추가하지 않았다.

특정 운동, 복수 후보, 미등록 운동, LLM이 운동 필드를 누락했지만 기존 catalog/alias/restriction으로
확인되는 운동 질문은 종전 resolution을 유지한다. 다른 Metric이나 복수 Metric에는 이 예외를
적용하지 않는다. 이는 이미 추출된 `training_gap`의 인자 요구 수정이며, 임의의 자연어 의도를
코드가 새로 추론하는 기능은 아니다.

## 검증

실제 LLM/OpenAI·서비스 REST·DB 호출 및 acceptance/기존 interpretation 평가 재실행은 0회다.
TestClient, mock Interpreter, 인메모리 세션과 기존 실제 Tool 구현만 사용했다.

- API 신규 11개: 저장 metadata 충돌 재현, 양 endpoint에서 debug 생략/false/true 직렬화,
  내부 필드·가짜 trace 차단, 선택 응답 필드 보존, 잘못된 공개 값 거절,
  새 synthetic 최근 기간+인자 clarification.
- training_gap 신규 22개: 전체 기록과 기존 record_operation 표현, 불필요 운동 질문 제거,
  기존 최근 8주, 실제 Record/Metric 인메모리 연결(공백 14일·5일), 공개 응답,
  특정·모호·미등록 운동과 LLM 운동 필드 누락, 기간·다른 인자 유지,
  catalog/숫자/span grounding 및 다른/복수 Metric 회귀.
- 수정 전 재현: API 9 failed / 2 passed, training_gap 10 failed / 12 passed.
- 수정 후 관련: API 관련 121 passed, training_gap 관련 170 passed.
- 공개 테스트: 1085 passed / 81 deselected.
- 최초 전체: 1165 passed / 1 failed. 기존 synthetic freeze 도구 테스트에서 임시
  `reports.staging` rename이 Windows `PermissionError(WinError 5)`로 거절됐다.
  해당 테스트는 코드 변경 없이 새 임시 경로에서 1 passed, 이후 동일 범위의 전체 재확인은
  1166 passed다. assertion/marker/검증 범위는 변경하지 않았다.
- CI 최종 결과, 실행 환경 및 수정 후 최종 head는 별도 completion 기록과 PR #20 본문에 남긴다.

## 변경하지 않은 범위

Interpreter prompt, answer_type 및 evidence required/optional 정책, 최근 8주 정책, retrieval,
embedding, corpus, 기존 벡터·Gold, Tool 계산, Grader/Recovery, optional blocking,
legacy workflow 및 서비스 기본값은 변경하지 않았다. 공유 graph 실행 코드와 binding,
공개 API contract 파일도 변경하지 않았다. 새 성능 판정이나 일반화 주장은 하지 않는다.

## 별도 발견 — 수정하지 않음

기존 특정 운동 `training_gap` 경로에는 세션 형태의 잠재적 불일치가 있다.
`PsycopgTrainingRepository.list_sessions`는 `exercise_names` 목록을 반환하지만,
`MetricTool._prepare_records`는 canonical 운동 필터가 있을 때 단수 `exercise_name`을 읽는다.
이 형태의 synthetic 세션 두 개를 기존 Metric Tool에 넣으면 `empty` / record_count=0이 된다.
운동 resolution과 canonical binding은 유지되지만 계산 결과가 비는 문제는 별개다.
이번 요청의 Tool 계산 의미 변경 금지에 따라 해당 경로는 수정하지 않았다.
