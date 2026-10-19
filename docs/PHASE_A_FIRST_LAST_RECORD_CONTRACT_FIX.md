# Phase A first/last N 기록 조회 검증 충돌 수정

이 수정은 **interpreter extraction smoke v1의 S05 관찰 후 진행하는 사후 수정**이다. smoke v1과 acceptance v2를 재실행하거나 수정하지 않았다. 기존 결과를 수정 후 코드의 성능 근거로 해석하지 않으며 성능 향상을 주장하지 않는다.

기준 master: `7f383cd0ad1a0f6176c358e6eef1a173dc9ba2b8` (PR #30·#31 merge 후).

## 원인과 최소 수정

Interpreter가 `first_last_n_session_median_e1rm`과 `record_operation=exercise_first_last`를 출력하면, 기존 validator는 record operation 허용 목록에서 차단했다. 하지만 binding은 이미 Metric contract의 `exercise_records`를 파생해 전체 기록을 조회하고 N세션 비교를 계산한다.

validator에서 **단일 `first_last_n_session_median_e1rm` + `exercise_first_last` 조합에 한해서만** binding과 같은 `record_operation_for_metric()`으로 파생한 operation을 기존 허용 목록과 비교한다. 원래 추출값은 유지하며 `metric_derived_first_last_record_validation_v1`을 내부 provenance에 기록한다.

순수 기록 조회의 `exercise_first_last`, 다른 Metric에 붙은 `exercise_first_last`, `get_session`, 알 수 없는 operation은 계속 차단한다. 실제 복수 Metric, 운동 모호성, 숫자·날짜 grounding, N 누락 등의 기존 검증도 유지한다. Interpreter의 미확정 항목을 일괄 삭제하지 않는다.

prompt/config·Router·binding·공유 graph·Tool 계산·Grader·Recovery·corpus·retrieval·서비스 기본값은 변경하지 않았다.

## 새 synthetic 검증

`tests/test_first_last_record_contract.py`에 새 질문과 fake 기록만 사용한 12개 테스트를 추가했다. 기존 S05 또는 acceptance 문장을 사용하지 않았다.

- 정상 first/last N 해석 통과 및 기존 Metric contract의 `exercise_records` binding 확인.
- N 누락은 `n_sessions` clarification을 유지하고 Tool 실행 전에 종료.
- 순수 조회 및 다른 Metric의 `exercise_first_last`, `get_session`, 미지원 operation 차단.
- FastAPI TestClient + mock Interpreter + 실제 Training Log/Metric Tool + fake repository 통합: 전체 4세션을 조회해 첫 2세션 중앙값 49.5, 마지막 2세션 중앙값 115.5를 계산. endpoint-only 조회가 호출되면 테스트 실패.

| 검증 | 결과 |
|---|---|
| 수정 전 새 테스트 | 3 failed / 9 passed: 정상 실행 차단 및 잘못된 record operation clarification 재현 |
| 수정 후 새 테스트 + 관련 회귀 | 113 passed |
| 공개 테스트 | 1,257 passed / 81 deselected |
| 로컬 전체 테스트 | 1,338 passed |
| 기존 보호 파일 SHA-256 | 2,147개 변경 없음 |
| 실제 LLM·외부 REST·DB 호출 | 없음. TestClient/mock/fake 기록만 사용 |

로컬 수에는 미커밋 사용자 테스트 19개가 포함된다. GitHub CI 수는 그만큼 적다. 원래 테스트 assertion·expected·marker는 변경하지 않았다.

pytest XML과 보호 파일 hash 목록은 ignored `.build/first-last-contract-*`에 보존한다. 최종 commit SHA 및 GitHub Actions 결과는 PR 설명에 기록한다. merge하지 않는다.
