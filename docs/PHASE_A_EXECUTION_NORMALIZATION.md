# Phase A 실행 정규화: post-hoc correctness 수정

이 변경은 acceptance v2 결과를 분석한 뒤 수행한 post-hoc 수정이다. 기존 acceptance 질문·Gold·raw·채점 결과를 다시 실행하거나 변경하지 않으며, 기존 수치를 수정 후 코드의 성능으로 해석하지 않는다.

기준 master: `cbb92852b7ec8de3bce319b3323bcab2e51a1221`.
PR 1은 코드만 수정한다. PR 2의 Interpreter 추출 규칙 변경은 별도 branch/PR에서 진행한다.

## 코드 계약

- 모든 analysis의 source span과 N grounding을 먼저 검사한다. 그 뒤 operation과 실행 인자 `n_sessions`가 같은 요청만 처음 항목으로 정규화한다. 원본 provider 출력과 debug output_summary는 유지된다. `identical_metric_execution_deduplication_v1`이 내부 provenance에 남는다.
- 인자가 다르거나 실제 operation이 다른 요청은 합치지 않는다. 단일 Metric 제한은 유지한다. 출처가 잘못된 중복 항목도 검사에서 거절한다.
- 세션별 e1RM에 기반한 `plateau_candidates`는 추정 1RM 관련 분석을 충족할 수 있다. 자연어 정체 표현을 특정 Metric으로 강제하지 않는다.
- Phase A에서 Metric이 정해졌고 원문과 추출값에 날짜/상대 기간 제약이 없으면, 기간 생략은 기존 전체 기록 정책으로 정규화한다. 처음/마지막 N세션은 달력 날짜가 아닌 세션 선택 범위로 취급한다. 명시적 날짜 및 미지원 상대 기간은 그대로 검증/clarification한다.
- 최근 8주 정책은 변경하지 않는다. 최근 창이 적용됐거나, 적용되지 않은 현재 분석창 요구가 남은 경우 전체 기록으로 덮어쓰지 않는다.
- 기간 정규화로 해소한 time 필드만 제거한다. 불확실한 operation이나 실제 필수 N·운동·비교 조건은 임의로 해소하지 않는다. `record_scope_normalization_v1`이 내부 provenance에 남는다.
- 공개 되묻기는 복수 지표 선택, 지표 미확정, 비교 대상 미확정을 구분한다. 내부 exception이나 LLM의 자유형 reason은 노출하지 않는다.

## 경계와 제한

추가 Metric을 추출한 LLM 출력을 코드가 임의로 삭제하지 않는다. 그 문제는 PR 2의 추출 contract에서 별도로 다룬다. 일반적인 `requested_analyses` 미확정 사유도 실제 의도를 확정할 수 없다면 유지한다.

Router·공유 graph·Tool 계산·retrieval·corpus·Grader·Recovery·기존 prompt/config와 서비스 기본값은 변경하지 않는다. legacy workflow와 해석 trace schema는 유지한다.

## 검증과 실제 호출 계획

새 `test_phase_a_execution_normalization.py`와 기존 Phase A 단위/통합 테스트를 사용한다. Mock/TestClient와 실제 provider 호출을 구분한다.

실제 호출 질문은 `tests/fixtures/interpreter_extraction_smoke_v1.json`의 새 synthetic 8개로 고정한다. 단일 Metric+일반 문헌 설명 6개와 실제 복수 Metric 2개이며, acceptance 문장을 사용하지 않는다. 질문과 의도한 operation은 실행기/검증용일 뿐 runtime은 질문만 받는다.

PR 1 코드 SHA와 PR 2 코드 SHA에서 동일 질문을 각각 한 번, 기존 Grader/동일 모델·DB·corpus v1·phase_a/debug=true로 실행한다. 자동 재시도 및 질문 재실행은 하지 않는다. 추출된 operation과 최종 동작을 비교한다. 결과를 보고 prompt·코드·질문을 재튜닝하지 않는다. 이는 동작 확인이며 성능 평가가 아니다.

실행 전에 fixture SHA-256과 설정/환경을 별도 로컬 manifest에 기록한다. raw는 새 ignored reports 경로에 배타적으로 생성한다. 기존 frozen 자료와 별개로 보존한다.
