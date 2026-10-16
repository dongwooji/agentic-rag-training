# 특정 운동 training_gap의 세션 입력 수정

기준선 master: `de542771cfd3798010fde5df15ee2a0d6d5b8d25`.

`PsycopgTrainingRepository.list_sessions`는 세션마다 `exercise_names` 배열을 반환한다.
기존 Metric 필터는 세트 행의 `exercise_name`만 확인하여, 특정 운동이 바인딩된
`training_gap`에서 실제 세션을 모두 제외했다. 공백 계산 이전의 입력 형태 문제다.

`src/tools/metric.py`에서 `training_gap`에 한해 단수 `exercise_name` 필드가 없는
세션 행의 `exercise_names` 목록 membership을 확인한다. 단수 필드가 있으면 기존
정확한 운동명 필터를 유지한다. 전체 기록 요청은 기존처럼 모든 세션을 사용한다.
날짜 경계, 세션 중복 제거, 정렬, 시간/일 단위 공백 공식과 다른 Metric 처리는 변경하지 않았다.

새 synthetic 테스트 14개는 특정 운동 세션과 다른 운동 제외, 날짜 양끝 포함,
전체 기록, 잘못된 membership, 단수 세트 형태, 다른 Metric 입력 의미,
실제 Repository SQL 어댑터 → Training Log Tool → executor → Metric 경계를 확인한다.
SQL 실행만 mock 처리하며 실제 DB/LLM/서비스 REST는 호출하지 않는다.
수정 전 3 failed / 11 passed로 재현했고, 수정 후 관련 테스트는 50 passed다.
공개/전체/CI 및 최종 head는 PR과 로컬 실행 기록에 기록한다.

이 변경은 Phase A evidence acceptance v1
(`6c68ea996f967bcdda47bfed2671aae1ae583969`) 결과 이후에 수행한 correctness 수정이다.
기존 acceptance는 재실행하지 않았으므로, 그 성능 수치를 수정 후 코드의 성능으로 해석하지 않는다.
평가 파일·Gold·raw result·decision·prompt·evidence 정책·retrieval·Grader·Recovery는 변경하지 않았다.
후속 operation 필수 인자 contract 변경은 별도 branch/PR에서 수행한다.
