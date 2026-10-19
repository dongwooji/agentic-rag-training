# 문항별 실행 관찰 — acceptance v2

질문 원문·개별 운동 기록·최종 응답 전문은 로컬 raw에 보존한다. 아래는 공개 가능한 실행 metadata와 원래 채점 결과의 요약이다.

## V2-01 · G-LA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 충분 |
| Grader 실행 상태 | completed | completed |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-02 · G-LA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 충분 |
| Grader 실행 상태 | completed | completed |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-03 · G-LA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 충분 |
| Grader 실행 상태 | completed | completed |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-04 · G-LA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 충분 |
| Grader 실행 상태 | completed | completed |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-05 · G-LA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 충분 |
| Grader 실행 상태 | completed | completed |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-06 · G-LU

기대 동작: 보류. literature_answerability: `unanswerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | abstain | abstain |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 부족, 부족 | 부족, 부족, 부족 |
| Grader 실행 상태 | completed, completed | completed, completed, completed |
| 재검색 횟수 | 1 | 2 |
| Recovery 생성 시도 | 2 | 2 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-07 · G-LU

기대 동작: 보류. literature_answerability: `unanswerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | abstain | answer |
| Terminal | abstain_ready | answer_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 부족, 부족, 부족 | 부족, 충분 |
| Grader 실행 상태 | completed, completed, completed | completed, completed |
| 재검색 횟수 | 2 | 1 |
| Recovery 생성 시도 | 2 | 1 |
| 고정 채점 정답 여부 | True | False |

Gold는 unanswerable. A 보류/B 답변. B 답변에는 직접 근거 부재에 대한 제한 설명이 있으나 종료 상태 기준으로는 불일치.

## V2-08 · G-LU

기대 동작: 보류. literature_answerability: `unanswerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | literature_only | literature_only |
| 관찰 동작 | abstain | abstain |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | search_literature | search_literature |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 부족, 부족, 부족 | 부족, 부족, 부족 |
| Grader 실행 상태 | completed, completed, completed | completed, completed, completed |
| 재검색 횟수 | 2 | 2 |
| Recovery 생성 시도 | 2 | 2 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-09 · G-HA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | hybrid | hybrid |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | query_training_log, compute_metrics, search_literature | query_training_log, compute_metrics, search_literature |
| Record operation | list_sessions | list_sessions |
| Metric operation | weekly_frequency | weekly_frequency |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 부족, 충분 | 충분 |
| Grader 실행 상태 | completed, completed | completed |
| 재검색 횟수 | 1 | 0 |
| Recovery 생성 시도 | 1 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-10 · G-HA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | hybrid | hybrid |
| 관찰 동작 | answer | abstain |
| Terminal | answer_ready | abstain_ready |
| 실행 Tool | query_training_log, compute_metrics, search_literature | query_training_log, compute_metrics, search_literature |
| Record operation | exercise_records | exercise_records |
| Metric operation | weekly_volume | weekly_volume |
| Clarification 필드 | — | — |
| Grader 판정 순서 | 충분 | 부족, 부족 |
| Grader 실행 상태 | completed | completed, completed |
| 재검색 횟수 | 0 | 1 |
| Recovery 생성 시도 | 0 | 2 |
| 고정 채점 정답 여부 | True | False |

A 답변/B 보류. B는 특정 운동의 주간 볼륨 적정 범위를 고정 요구사항으로 두고 두 번 모두 부족 판정.

## V2-11 · G-HA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | requested_analyses | requested_analyses |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 requested_analyses 확인. 정규화된 분석 목록에 plateau_candidates가 중복되어 나타남. Grader 미도달.

## V2-12 · G-HA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | canonical_exercise_name, requested_analyses | canonical_exercise_name, requested_analyses |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 canonical_exercise_name/requested_analyses 확인. training_gap과 plateau_candidates가 함께 나타남. Grader 미도달.

## V2-13 · G-HA

기대 동작: 답변. literature_answerability: `answerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | requested_analyses | requested_analyses |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 requested_analyses 확인. weekly_frequency와 plateau_candidates가 함께 나타남. Grader 미도달.

## V2-14 · G-HU

기대 동작: 보류. literature_answerability: `unanswerable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | requested_analyses | requested_analyses |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

Gold는 보류이나 양쪽 모두 requested_analyses 되묻기. 문헌 검색/Grader 미도달.

## V2-15 · G-R

기대 동작: 답변. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | log_metric | log_metric |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | query_training_log, compute_metrics | query_training_log, compute_metrics |
| Record operation | list_sessions | list_sessions |
| Metric operation | weekly_frequency | weekly_frequency |
| Clarification 필드 | — | — |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-16 · G-R

기대 동작: 답변. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | time_condition | time_condition |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 time_condition 되묻기. training_gap 해석은 있으나 실행 전 중단.

## V2-17 · G-R

기대 동작: 답변. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | requested_analyses | requested_analyses |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 requested_analyses 되묻기. 요약에 단일 plateau_candidates가 있으므로 공개 trace만으로 미해결 사유의 더 세부 원인은 확정하지 않음.

## V2-18 · G-R

기대 동작: 답변. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | time_condition | time_condition |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | False | False |

양쪽 모두 time_condition 되묻기. first_last_n_session_median_e1rm 해석은 있으나 실행 전 중단.

## V2-19 · G-R

기대 동작: 답변. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | log_metric | log_metric |
| 관찰 동작 | answer | answer |
| Terminal | answer_ready | answer_ready |
| 실행 Tool | query_training_log, compute_metrics | query_training_log, compute_metrics |
| Record operation | exercise_records | exercise_records |
| Metric operation | weekly_volume | weekly_volume |
| Clarification 필드 | — | — |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

A/B 최종 동작 일치.

## V2-20 · G-C

기대 동작: 되묻기. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | ambiguous | ambiguous |
| 관찰 동작 | clarification | clarification |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | canonical_exercise_name | canonical_exercise_name |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

Gold가 요구한 운동 clarification. 후속 선택 요청은 단일 턴 조건에 따라 하지 않음.

## V2-21 · G-O

기대 동작: 보류. literature_answerability: `not_applicable`.

| 항목 | A | B |
|---|---|---|
| HTTP | 200 | 200 |
| Route | unsupported | unsupported |
| 관찰 동작 | abstain | abstain |
| Terminal | abstain_ready | abstain_ready |
| 실행 Tool | — | — |
| Record operation | — | — |
| Metric operation | — | — |
| Clarification 필드 | — | — |
| Grader 판정 순서 | — | — |
| Grader 실행 상태 | — | — |
| 재검색 횟수 | 0 | 0 |
| Recovery 생성 시도 | 0 | 0 |
| 고정 채점 정답 여부 | True | True |

양쪽 모두 unsupported 경로의 보류.
