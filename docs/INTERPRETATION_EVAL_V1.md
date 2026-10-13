# 질문 해석 v1 REST 평가

AI 초안 평가 질문을 사용자가 제출한 바이트 그대로 동결했습니다. 문항 ID·라벨·답변 주의는 서비스 입력에 포함하지 않으며 POST 본문에는 질문만 전달했습니다.

질문 SHA-256: `100ad7d1cab3f2043767b7d8ad933579c522ad390fd8fc991b8b27fc549ed6cd`

기존 Phase A: `f423265dfb126ab07fefd5f7f6a097469d3f4238`. legacy·수정 Phase A: `62b247ece5f7df3ef9e51b43f4ebfcd9f0fb4417`.

세 설정의 src/config 차이는 PR #14의 contracts.py·validator.py 두 파일뿐입니다. 기본 legacy, corpus v1, Final V2의 운영 corpus v1용 검색 설정을 유지했습니다.

주 채점은 Router가 반환한 경로를 직접 비교합니다. ambiguous/invalid는 미분류로 오답입니다. 해석 필드의 채널 일치 수는 보조 진단으로 따로 기록하며 주 점수에 합산하지 않습니다.

공개 API의 abstain_ready 중 clarification metadata가 있거나 Router가 ambiguous이면 되묻기로 분류합니다. 나머지는 보류입니다. execution_failure는 항상 오답이며 HTTP/전송 실패는 별도로 셉니다. answer_ready는 실제 Tool 사용도 있어야 답변으로 채점합니다.

## 집계

| 분할 / 설정 | 경로 정확도 | 동작 정확도 | execution_failure | 부당한 보류·되묻기 | 채널 일치(보조) | HTTP/전송 실패 |
|---|---:|---:|---:|---:|---:|---:|
| dev/legacy | 4/10 (40%) | 8/10 (80%) | 0 | 1 | 3/10 | 0 |
| dev/phase_a_before | 0/10 (0%) | 2/10 (20%) | 3 | 4 | 5/10 | 0 |
| dev/phase_a_fixed | 2/10 (20%) | 4/10 (40%) | 2 | 3 | 6/10 | 0 |
| test/legacy | 3/20 (15%) | 13/20 (65%) | 0 | 4 | 4/20 | 0 |
| test/phase_a_before | 0/20 (0%) | 4/20 (20%) | 9 | 6 | 6/20 | 0 |
| test/phase_a_fixed | 2/20 (10%) | 9/20 (45%) | 1 | 8 | 11/20 | 0 |

## 실행량

| 분할 / 설정 | LLM 호출 | input (cached 포함) | cached | output | 추정 USD | REST 지연 합계(초) |
|---|---:|---:|---:|---:|---:|---:|
| dev/legacy | 14 | 75029 | 0 | 2538 | 0.067693 | 47.9 |
| dev/phase_a_before | 10 | 26956 | 16128 | 2086 | 0.018718 | 38.2 |
| dev/phase_a_fixed | 17 | 71834 | 46336 | 3148 | 0.036765 | 50.3 |
| test/legacy | 21 | 68152 | 0 | 2672 | 0.063138 | 56.0 |
| test/phase_a_before | 20 | 53960 | 34048 | 4341 | 0.037022 | 68.2 |
| test/phase_a_fixed | 25 | 76986 | 59392 | 4941 | 0.039884 | 79.9 |

## 제한과 무결성

기존 보호 파일 1489개 hash가 모두 동일합니다. 새 평가 원문·manifest·응답 전문·Tool 결과는 data/evaluation/interpretation_eval_v1 및 reports/interpretation_eval_v1에 로컬 보존합니다. 공개 저장소에는 질문 원문·기록·provider 결과를 올리지 않습니다.

dev 30회 완료 후 test 60회, 총 90회 POST를 각 문항·설정에서 한 번씩 실행했습니다. 사용자 선택 재개와 오류 재실행은 없습니다. 코드·prompt·validator·라벨·검색 조건을 결과에 따라 수정하지 않았습니다.

동일 질문에 대한 단일 확률적 실행이며 설정 순서가 고정되고 provider cache가 공유됩니다. LLM 변동과 validation 변경 효과를 완전히 분리하는 반복 실험은 아닙니다. 문서에 test 사전 비공유 문구가 있으나 사용자가 첨부 전체를 직접 제출하여 평가자가 문항을 읽었습니다. 독립적인 맹검 평가로 표현하지 않습니다. 답변 내용의 정확도와 인과 설명 품질은 이번 동작 채점 대상이 아닙니다.

## dev/legacy: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| D01 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| D02 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| D03 | 200 | 기록+문헌 / hybrid | 기록+문헌 | answer_ready / 답변 | True / True | query_training_log, search_literature |
| D04 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| D05 | 200 | 기록 / log_metric | 기록 | answer_ready / 답변 | True / True | query_training_log, compute_metrics |
| D06 | 200 | 기록 / log_lookup | 기록 | answer_ready / 답변 | True / True | query_training_log |
| D07 | 200 | 기록+문헌 / literature_only | 문헌 | abstain_ready / 보류 | False / False | search_literature |
| D08 | 200 | 문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| D09 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| D10 | 200 | 범위 밖 / unsupported | 기록 | abstain_ready / 보류 | True / True | 없음 |

## dev/phase_a_before: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| D01 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| D02 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| D03 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| D04 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| D05 | 200 | 기록 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| D06 | 200 | 기록 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| D07 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| D08 | 200 | 문헌 / ambiguous | 문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| D09 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| D10 | 200 | 범위 밖 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |

## dev/phase_a_fixed: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| D01 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| D02 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| D03 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| D04 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| D05 | 200 | 기록 / log_metric | 기록 | answer_ready / 답변 | True / True | query_training_log, compute_metrics |
| D06 | 200 | 기록 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| D07 | 200 | 기록+문헌 / ambiguous | 문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| D08 | 200 | 문헌 / literature_only | 문헌 | execution_failure / execution_failure | True / False | search_literature |
| D09 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| D10 | 200 | 범위 밖 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |

## test/legacy: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| T01 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| T02 | 200 | 기록+문헌 / literature_only | 문헌 | abstain_ready / 보류 | False / False | search_literature |
| T03 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T04 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T05 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| T06 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| T07 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| T08 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T09 | 200 | 기록+문헌 / log_lookup | 기록 | answer_ready / 답변 | False / True | query_training_log |
| T10 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| T11 | 200 | 기록+문헌 / literature_only | 문헌 | abstain_ready / 보류 | False / False | search_literature |
| T12 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| T13 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T14 | 200 | 기록 / log_lookup | 기록 | answer_ready / 답변 | True / True | query_training_log |
| T15 | 200 | 기록 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T16 | 200 | 기록 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T17 | 200 | 문헌 / literature_only | 문헌 | answer_ready / 답변 | True / True | search_literature |
| T18 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| T19 | 200 | 범위 밖 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T20 | 200 | 범위 밖 / unsupported | 기록 | abstain_ready / 보류 | True / True | 없음 |

## test/phase_a_before: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| T01 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T02 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| T03 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T04 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T05 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| T06 | 200 | 기록+문헌 / ambiguous | 문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T07 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T08 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T09 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T10 | 200 | 기록+문헌 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / True | 없음 |
| T11 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T12 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T13 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T14 | 200 | 기록 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T15 | 200 | 기록 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T16 | 200 | 기록 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T17 | 200 | 문헌 / ambiguous | 문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T18 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T19 | 200 | 범위 밖 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T20 | 200 | 범위 밖 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |

## test/phase_a_fixed: 문항별 상태

| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |
|---|---|---|---|---|---|---|
| T01 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T02 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| T03 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T04 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T05 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| T06 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T07 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T08 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T09 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / False | 없음 |
| T10 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T11 | 200 | 기록+문헌 / ambiguous | 기록+문헌 | abstain_ready / 되묻기 | False / True | 없음 |
| T12 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / True | 없음 |
| T13 | 200 | 기록+문헌 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T14 | 200 | 기록 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T15 | 200 | 기록 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |
| T16 | 200 | 기록 / log_metric | 기록 | answer_ready / 답변 | True / True | query_training_log, compute_metrics |
| T17 | 200 | 문헌 / literature_only | 문헌 | answer_ready / 답변 | True / True | search_literature |
| T18 | 200 | 기록+문헌 / invalid | 미분류 | execution_failure / execution_failure | False / False | 없음 |
| T19 | 200 | 범위 밖 / ambiguous | 범위 밖 | abstain_ready / 되묻기 | False / False | 없음 |
| T20 | 200 | 범위 밖 / ambiguous | 기록 | abstain_ready / 되묻기 | False / False | 없음 |


## 관찰 결과와 검증

수정 Phase A의 test 실행 실패는 기존 Phase A의 9건에서 1건으로 감소했고 동작 정답은 4/20에서 9/20으로 늘었습니다. 답변만 허용된 문항의 부당한 보류·되묻기는 6건에서 8건으로 늘었습니다. 원시 경로 정답은 0/20 → 2/20이며, 채널 일치 보조 진단은 6/20 → 11/20입니다. 이번 단일 실행을 일반화 성능이나 수정의 독립적 인과 효과로 해석하지 않습니다.

수정 Phase A test의 답변은 T16(Log/Metric), T17(Literature) 두 문항입니다. 17문항은 되묻기, T18은 실행 실패입니다. T19·T20의 범위 밖 질문도 안전한 보류 상태 대신 clarification으로 종료되어 허용 동작 오답입니다. 최종 상태는 공개 API에서 abstain_ready로 표현되므로 response_metadata와 Router 경로를 함께 확인했습니다.

남은 되묻기 사유에는 기간·비교 구간, 한 요청당 하나의 Metric 제한, 세션 선택 표현 제한, 운동 후보 선택, LLM이 남긴 미해결 필드가 있습니다. T18은 저장된 LLM 출력의 운동 후보가 16개로 계약의 최대 12개를 초과한 schema 검증 실패입니다. 저장 출력에 대한 로컬 Pydantic 검사로 확인했으며 REST나 LLM을 다시 실행하지 않았습니다. dev D08은 Literature 경로와 Grader를 통과한 뒤 최종 답변 grounding 검증에서 실패했습니다. 이 실패는 질문 해석 실패와 구분했습니다.

personal_record_requested 불일치 진단은 수정 Phase A dev에서 D05·D06·D10, test에서 T01·T03·T04·T06·T08·T10·T11·T15·T16·T20에 기록되었습니다. 정규화 후에도 다른 validation 조건 때문에 되묻기가 남을 수 있습니다. LLM 호출 기록에서 두 Phase A 설정 모두 문항당 Interpreter 1회를 확인했으며 legacy에는 Interpreter 호출이 없습니다. 이번 구어체 문항에서는 Phase A가 deterministic 해석만으로 종료된 사례가 없습니다.

총 LLM 호출 **107회**, input **372917**(cached 155904 포함), output **19726**, total **392643**, 추정 비용 **$0.26321955**입니다. 모든 호출에 usage가 기록되었습니다. 단계별 호출 수: {'answer': 15, 'step2': 7, 'step3': 7, 'grader': 11, 'recovery': 7, 'interpreter': 60}. 비용은 기존 versioned config의 token 단가로 추정했으며 청구 금액 확인이 아닙니다. REST 요청 지연 합계는 340.6초이며 서버·모델 초기화 시간은 제외합니다.

단위 synthetic 26개, 관련 Phase A/API mock 회귀 109개, 공개 테스트 **884 passed / 81 deselected**, 로컬 전체 **965 passed**입니다. 실제 실행은 Uvicorn REST /query + 기존 OpenAI provider + PostgreSQL/pgvector로 별도 수행했습니다. 보호 파일 1,489개 hash, 원문 hash, 각 설정의 모든 tracked src/config hash, 첫 POST 전에 동결한 평가 도구·채점 테스트 hash를 재확인했습니다. 서비스 기본값은 legacy이며 prompt·Router·retrieval·corpus·Gold는 변경하지 않았습니다.

## 재현 자료

로컬 원문은 `data/evaluation/interpretation_eval_v1/questions.md`, 동결 metadata는 같은 디렉터리의 `manifest.json`입니다. `reports/interpretation_eval_v1/RESULTS.md`에 모든 문항의 사유·답변 요약·답변 전문·진단을 기록했고, `scores.json`과 `execution_audit.json`에 채점 및 실행량을 보존했습니다. 원시 REST JSON 여섯 개는 각각 완료 시 SHA-256을 동결했습니다. data/reports는 저장소 공개 정책에 따라 ignored 상태로 보존합니다.

실행 도구는 `scripts/evaluate_interpretation_v1.py`입니다. prepare는 새 디렉터리만 허용하고 이전 소스를 git archive로 export합니다. 기존 data/reports를 export 디렉터리에 연결해야 하며 이번 실행은 Windows junction으로 같은 원본을 읽었습니다. 각 run은 새 프로세스와 새 출력 디렉터리를 요구합니다. dev의 legacy → phase_a_before → phase_a_fixed를 완료한 뒤 test에서 같은 순서로 실행했습니다. 결과 디렉터리가 존재하면 재실행을 거부합니다. report는 동결된 응답 hash를 검증한 후 오프라인으로만 채점합니다. 산출물 생성 후 문서의 설명 문장은 편집했으며 채점·실행 도구는 첫 POST 이후 변경하지 않았습니다.
