# Phase A evidence acceptance v1

## SHA와 blind 실행

`input_freeze.json`의 질문/Gold SHA가 지정값과 일치한다. 두 clean worktree에서 기준선 20회 → 원시 결과 동결 → 변경 20회 → 두 manifest 동결을 완료한 뒤 처음으로 Gold/REVIEW/build를 읽었다. `blind_execution_freeze.json`, `answer_files_unseal.json`에 경계와 시각을 기록했다. 원본 5개는 변경하지 않았다.

Baseline `685e2b9194419fbf757df2df2156299b77e4afe3`, changed `6c68ea996f967bcdda47bfed2671aae1ae583969`. 두 환경 모두 phase_a이며 서비스 기본값 legacy는 유지했다. DB fingerprint/corpus v1/벡터·retrieval/catalog/model 인자 등 16개 비교 항목이 일치한다. 상세 config/prompt SHA는 두 preflight 및 environment_comparison.json에 있다.

## 먼저 동결한 필수 판정

| 기준 | baseline | changed | 판정 |
|---|---:|---:|---|
| required-channel omission | 6 | 0 | PASS |
| expected-answer의 부당한 clarification/abstain | 12 | 9 | PASS |
| final behavior accuracy | 5/20 (25%) | 10/20 (50%) | PASS |
| graph-state execution_failure | 0 | 0 | PASS |

**동결 scorer 기준 acceptance PASS. REST 안정성 통과를 뜻하지 않는다.** 변경 버전에서 HTTP 500이 3건(EN-03A/B, EN-10B) 발생했다. 사용자에게 정상 clarification/trace가 반환되지 않았다. 기존 observer는 graph 내부 state만 투영하므로 응답 변환 실패를 execution_failure에 반영하지 않는다. 이 한계를 숨기거나 결과를 보고 scorer/판정을 바꾸지 않았고, API 경계 실행 실패 0→3을 별도 기록했다.

필수 판정과 hash는 decision.json / decision_FREEZE.json에 먼저 저장했고 이후 보조 분석을 작성했다. Gold는 최상위 키가 schema_version, 기존 scorer는 version을 요구했다. 원본은 보존하고 키 하나만 바꾼 입력 사본을 만들었으며 cases의 완전 동일성을 확인했다(gold_format_adapter.json). 라벨·규칙 변경은 없다.

## 문항별 비교

| 문항 | baseline 동작/HTTP | changed 동작/HTTP | changed type | baseline 정답 | changed 정답 |
|---|---|---|---|---:|---:|
| EN-01A | answer | answer | descriptive | O | O |
| EN-01B | answer | answer | evaluative | - | O |
| EN-02A | answer | answer | descriptive | O | O |
| EN-02B | clarification | answer | general | - | O |
| EN-03A | clarification | HTTP 500 오류 (graph: clarification) | descriptive | - | - |
| EN-03B | clarification | HTTP 500 오류 (graph: clarification) | advisory | - | - |
| EN-04A | clarification | answer | advisory | - | O |
| EN-04B | clarification | clarification | evaluative | - | - |
| EN-05A | clarification | clarification | descriptive | - | - |
| EN-05B | clarification | clarification | causal | - | - |
| EN-06A | answer | answer | descriptive | O | O |
| EN-06B | answer | abstain | descriptive | O | - |
| EN-07A | clarification | abstain | out_of_scope | - | O |
| EN-07B | clarification | answer | evaluative | - | O |
| EN-08A | clarification | clarification | causal | - | - |
| EN-08B | clarification | clarification | causal | - | - |
| EN-09A | clarification | clarification | causal | - | - |
| EN-09B | clarification | answer | causal | - | O |
| EN-10A | answer | answer | advisory | O | O |
| EN-10B | clarification | HTTP 500 오류 (graph: clarification) | advisory | - | - |

정답은 동결 scorer의 source/실행 operation/최종 동작 계약을 모두 적용한 결과다. 단순 answer_ready 비율이 아니다. baseline answer_type은 N/A. HTTP 500의 내부 graph 동작을 별도로 표기했다. 전체 Gold 대안·need·Tool·되묻기 사유·Grader/Recovery·응답 전문은 DETAILS.md 및 case_comparisons.json 참조.

## 보조 지표

| 지표 | baseline | changed |
|---|---:|---:|
| required source 실제 실행 누락 수 | 22 | 15 |
| 불필요 채널 추가 수 | 0 | 1 |
| optional→required 과도 승격 문항 수 | 1 | 1 |
| Record operation 정답 | 9/20 | 12/20 |
| Metric operation 정답 | 10/20 | 13/20 |
| answer_type 정답 | N/A | 14/20 |
| 기본 최근 기간 정답 | 16/20 | 20/20 |
| 실제 HTTP 200 / 500 | 20 / 0 | 17 / 3 |
| LLM 호출 | 32 | 52 |
| input token | 136769 | 246150 |
| cached input token | 34048 | 94208 |
| output token | 6464 | 10615 |
| 추정 비용 USD | 0.10868235 | 0.1687896 |
| 평균 / p95 지연 초 | 5.347 / 13.006 | 7.734 / 18.739 |

p95는 20문항 nearest-rank(19번째)이다. 실제 청구 비용은 provider 미제공으로 null; 추정 비용은 동결된 stage별 config 가격표와 관측 usage만 사용했다. 84회 LLM 호출 모두 usage를 받았다. 합계 input 382919 / output 17079 / total 399998 token, 추정 USD 0.27747195. 단계별 호출·Tool 조합·contrast pair는 auxiliary_metrics.json / DETAILS.md에 기록했다.

## 관찰된 실패와 한계

- HTTP 500 세 건: 최근 8주가 적용된 clarification metadata에 default_time_window가 추가되지만, 공개 ClarificationMetadata의 extra=forbid와 충돌한다. 저장된 metadata만으로 같은 extra_forbidden(default_time_window)을 오프라인 재현했다. 재요청·런타임 수정 없이 원인을 확인했다(api_boundary_failures.json).
- EN-03A/B는 실제 catalog의 Weighted dips로 정상 확정됐다. 이 실행의 500을 운동명 후보 연결 실패로 설명하지 않는다. 이후 n_sessions 등의 불필요 clarification과 위 metadata 충돌이 관찰됐다. 다만 다른 표현/실행에서는 운동명 LLM 해석 품질의 영향을 받을 수 있다.
- EN-06B: LLM이 explicit_literature_request=optional과 literature_needed_for_answer=true를 동시에 출력했다. 코드가 문헌을 required로 결정했다. 문헌 부족과 Recovery 후 전체 abstain으로 끝났다. optional Literature도 실제 선택되면 기존 blocking semantics이며, 이번 결과에서 optional 식별/실행 한계가 모두 드러났다.
- EN-05A/B는 전반적 training gap 질문에 운동을 되물었다. EN-08A는 필요한 운동 clarification 외 requested_analyses도 되물어 동결 Gold의 허용 필드 기준에서 오답이다. EN-08B도 plateau_candidates 관련 clarification이 남았다.
- EN-09A는 수치 비교의 “왜”를 causal/문헌 요구로 판단해 유일한 불필요 채널 추가가 됐다. 8월/9월 복수 기간 grounding과 복수 분석 clarification도 남았다.
- 기본 8주 applied는 EN-03A/B, EN-07B, EN-10B에서 관찰되나 세 HTTP 오류에서는 최종 사용자에게 정상 전달되지 않았다. trace/기간 내부 정확도와 사용자 응답 성공을 구분해야 한다.
- 단일 probabilistic run, 고정 variant 순서와 shared cache; 분산·통계적 유의성은 측정하지 않았다. 제한된 frozen 20문항의 결과이며 일반화 증거가 아니다.
- 최종 동작 채점은 근거 source와 Tool operation까지 확인하나, 답변 문장 전체의 과학적 정확성/개인 조언 적절성에 대한 독립 사람 평가는 아니다.
- corpus v1, 기존 벡터/Gold/운영 서비스/Router/Grader/Recovery는 수정하지 않았다. 자동 재시도와 clarification 후속 요청은 0회. PR #20 및 평가 PR은 merge하지 않는다.

기존 interpretation test 20 회귀 진단은 이 문서·상세 결과를 동결한 뒤 별도 REGRESSION.md에 기록한다. 해당 결과로 위 acceptance 판정을 바꾸지 않는다.
