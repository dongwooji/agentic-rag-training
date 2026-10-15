# Phase A evidence-need acceptance set v1 — 검토용 초안

- 작성: Claude 초안 → Will 검토·수정 → freeze (2026-10-15, Will 승인)
- 형식: `phase_a_evidence_gold_v1` (Codex 계약 기준), 10 contrast pair / 20문항
- **구현 담당 Codex 채팅에 이 파일을 보여주지 마세요.** freeze 후 실행 단계에서만 전달합니다.
- 최근 8주 기준: 마지막 기록일 2018-09-29 → 2018-08-05 ~ 2018-09-29 (양 끝 포함 56일)

## 문항과 Gold

| ID | 질문 | answer_type | 필수 source | 보조 source | 허용 Metric 집합 | Record op | 최종 동작 | 8주 기본값 |
|---|---|---|---|---|---|---|---|---|
| EN-01A | 2017년 바벨 인클라인 벤치프레스 주별 볼륨 추이를 정리해줘 | descriptive | training_records | - | {weekly_volume} | exercise_records | answer | - |
| EN-01B | 2017년 바벨 인클라인 벤치프레스 주별 볼륨, 근비대가 목표였다면 모자라진 않았어? | evaluative | training_records, literature | - | {weekly_volume} | exercise_records | answer | - |
| EN-02A | 2018년 바벨 시티드 숄더프레스 추정 1RM이 어떻게 바뀌었어? | descriptive | training_records | - | {estimated_1rm} | exercise_records | answer | - |
| EN-02B | 2018년 바벨 시티드 숄더프레스 추정 1RM이 오르다가 꺾였던데, 일반적으로 이런 꺾임은 어떤 요인들로 설명될 수 있어? | causal | training_records, literature | - | {estimated_1rm} / {plateau_candidates} | exercise_records | answer | - |
| EN-03A | 최근에 중량 딥스를 일주일에 몇 번씩 했어? | descriptive | training_records | - | {weekly_frequency} | list_sessions | answer | O |
| EN-03B | 최근 중량 딥스 빈도를 보고, 앞으로 주에 몇 번 하는 게 좋을지 조언해줘 | advisory | training_records, literature | - | {weekly_frequency} | list_sessions | answer | O |
| EN-04A | 근력 향상을 목표로 할 때 한 세트에 몇 회 정도 반복하는 게 일반적으로 권장돼? | general | literature | - | 없음 | 없음 | answer | - |
| EN-04B | 2018년에 중량 딥스를 한 세트에 보통 몇 회씩 했는지 보고, 근력 향상 목적에 맞는 반복 범위였는지 알려줘 | evaluative | training_records, literature | - | 없음 | exercise_records | answer | - |
| EN-05A | 2017년에 2주 넘게 운동을 안 한 적이 있었어? | descriptive | training_records | - | {training_gap} | list_sessions | answer | - |
| EN-05B | 2017년에 2주 넘게 운동을 쉰 적이 있었어? 있었다면 그 정도 공백이면 근력이 많이 빠지는 수준이야? | evaluative | training_records, literature | - | {training_gap} | list_sessions | answer | - |
| EN-06A | 바벨 인클라인 벤치프레스 처음 3세션과 마지막 3세션의 추정 1RM 중앙값을 비교해줘 | descriptive | training_records | - | {first_last_n_session_median_e1rm} | exercise_records | answer | - |
| EN-06B | 바벨 인클라인 벤치프레스 처음 3세션과 마지막 3세션의 추정 1RM 중앙값을 비교해줘. 비슷한 주제의 논문이 있다면 참고로 덧붙여줘 | descriptive | training_records | literature | {first_last_n_session_median_e1rm} | exercise_records | answer | - |
| EN-07A | 최근 바벨 인클라인 벤치프레스 하고 나면 팔꿈치가 시큰거리는데, 어떤 약을 먹으면 될지 알려줘 | out_of_scope | - | - | 없음 | 없음 | abstain | - |
| EN-07B | 최근 바벨 인클라인 벤치프레스 추정 1RM이 떨어졌는지 확인해줘 | descriptive | training_records | - | {estimated_1rm} / {plateau_candidates} | exercise_records | answer | O |
| EN-08A | 2018년 시티드 로우 추정 1RM이 몇 달째 제자리인데, 이런 정체는 보통 뭐 때문에 생겨? | causal | training_records, literature | - | 없음 | 없음 | clarification | - |
| EN-08B | 2018년 바벨 인클라인 벤치프레스 추정 1RM이 몇 달째 제자리인데, 이런 정체는 보통 뭐 때문에 생겨? | causal | training_records, literature | - | {estimated_1rm} / {plateau_candidates} | exercise_records | answer | - |
| EN-09A | 2018년 9월 중량 딥스 주간 볼륨이 8월보다 왜 낮게 나왔는지 주별 수치로 보여줘 | descriptive | training_records | - | {weekly_volume} | exercise_records | answer | - |
| EN-09B | 2018년 9월에 중량 딥스 볼륨을 줄였는데, 그게 근육 유지에 문제가 될 수 있을까? | evaluative | training_records, literature | - | {weekly_volume} | exercise_records | answer | - |
| EN-10A | 근력 운동 초보가 주당 세트 수를 늘릴 때 어떤 원칙을 따르면 좋아? | advisory | literature | - | 없음 | 없음 | answer | - |
| EN-10B | 최근 바벨 시티드 숄더프레스 주간 볼륨을 보고, 다음 달에 세트 수를 얼마나 늘리면 좋을지 조언해줘 | advisory | training_records, literature | - | {weekly_volume} | exercise_records | answer | O |

## pair별 검증 의도

| pair | 바뀌는 것 | 확인하려는 것 |
|---|---|---|
| EN-01 | 서술 → 평가 | "연구"라는 단어 없이 평가 요구만으로 문헌이 필수가 되는가 |
| EN-02 | 서술 → 원인 | 원인 질문에서 기록 + 문헌을 모두 선택하는가 |
| EN-03 | 서술 → 조언 | "최근" 상대 기간에 8주 기본값 적용 + 조언에 문헌 추가 |
| EN-04 | 일반 지식 → 개인 기록 평가 | 개인 기록이 없으면 문헌만, 있으면 기록 + 문헌 |
| EN-05 | 공백 서술 → 공백 영향 평가 | training_gap 서술과 평가의 구분 |
| EN-06 | 문헌 요청 없음 → 선택적 문헌 요청 | optional 문헌을 required로 승격하지 않는가 (실행상 차단 가능성은 알려진 한계) |
| EN-07 | 범위 밖(약 추천) → 범위 안 | out_of_scope 즉시 보류, 되묻기 없음 |
| EN-08 | 운동 모호("시티드 로우") → 운동 명확 | 목적은 알고 인자만 모호할 때 answer_type 보존 + 운동만 되묻기 |
| EN-09 | "왜"가 있는 계산 설명 → 효과 평가 | "왜"라는 단어만으로 causal/문헌이 되지 않는가 |
| EN-10 | 일반 조언 → 개인 기록 기반 조언 | 조언에서 개인 기록 필요 여부 구분 + 8주 기본값 |

answer_type 분포: descriptive 8, evaluative 4, causal 3, advisory 3, general 1, out_of_scope 1

## 작성 원칙

- 사용 운동: 바벨 인클라인 벤치프레스(Incline Bench Press (Barbell), 등록된 한국어 표현이라 규칙으로 확정), 바벨 시티드 숄더프레스(Seated Shoulder Press (Barbell), 명시적 선택 규칙으로 확정), 중량 딥스(Weighted dips).
- 의도적으로 모호한 운동은 EN-08A의 "시티드 로우" 하나 (Seated Cable Row (close Grip) / Hammer seated row (CLOSE GRIP), 둘 다 2018년 기록 있음).
- Metric 실행 시 Record Tool이 먼저 호출되는 구조에 맞춰 Record op를 기록: e1RM·처음/마지막 N·볼륨·정체 → exercise_records, 빈도·공백 → list_sessions.
- 8주 기본값 문항(EN-03, EN-07B, EN-10B)의 운동은 모두 2018-08-05 이후 기록이 있음.
- 복수 Metric 제한에 걸리지 않도록 각 문항은 Metric 하나로 답할 수 있게 작성.
- 피한 표현: Codex 프롬프트와 계약에 나온 예시 문장("주간 볼륨이 어떻게 변했어", "충분한 수준이야", "관련 연구가 있으면", "세트 사이 휴식", "훈련 빈도를 줄인 게 원인"), interpretation_eval_v1 test 문장(스쿼트 중량 하락 원인, 무릎 부상·재활, 식단, 최장 공백).

## 알려진 위험 (freeze 전 기록)

- "중량 딥스"는 한국어 별칭 규칙이 없어 LLM의 catalog 선택에 의존합니다. catalog의 딥스 운동은 Weighted dips 하나뿐이라 후보 하나를 기대하지만, LLM이 되묻기를 선택하면 해당 문항(EN-03A/B, EN-04B, EN-09A/B)은 부당한 되묻기로 채점됩니다. 결과 해석 시 이 점을 함께 보고합니다.
- EN-06B의 optional 문헌은 이번 범위에서 비차단 실행이 구현되지 않아, 문헌 근거가 부족하면 보류될 수 있습니다(계약에 명시된 한계). Gold는 answer로 유지합니다.

## freeze 전 확인 결과 (Codex 코드 확인, 2026-10-15)

- 바벨 인클라인 벤치프레스: 등록 표현 → 1개 / 바벨 시티드 숄더프레스: 명시 규칙 → 1개 / 중량 딥스: 별칭 없음, LLM 선택 / 데드리프트: Deadlift (Barbell) 별칭 1개 → 모호성 시험용으로 부적합해 "시티드 로우"로 교체
- Metric 실행 전 Record Tool 호출: 있음 (위 operation 매핑 반영)
- 시티드 로우: `seated_row_unspecified` 제한 규칙으로 후보 수와 무관하게 그립·장비 clarification (runtime_exercise_relations_v1.json, validator.py) → EN-08A Gold 유지

## freeze 절차

1. 위 확인 결과로 수정 → `build.py` 다시 실행
2. `questions.jsonl`, `gold.json`의 SHA-256을 `manifest.json`에 기록 (문항 수 20, 순서 = case_id 순)
3. 저장소의 별도 경로에 커밋 (구현 branch와 분리). 구현 완료·변경 후 코드 SHA 고정 뒤에만 실행 담당에게 전달
