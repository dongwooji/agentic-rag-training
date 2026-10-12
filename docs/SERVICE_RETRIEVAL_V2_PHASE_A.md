# Service Retrieval V2: Phase A 실제 REST 정성 관찰

실행일: 2026-10-12 (Asia/Seoul). 기존 legacy 결과와 별도로 사용자의 후속 요청에 따라 **같은 AI 작성 사용자 스타일 질문 10개를 Phase A로 각각 1회** 실행했다. Gold가 없는 정성 smoke 관찰이며 benchmark·일반화 성능·accuracy 평가가 아니다. 실제 결과를 보고 설정·코드·prompt·validator·corpus·검색어를 튜닝하거나 재요청하지 않았다.

## 실제 적용 모드와 동일 조건

실제 구성 클래스는 **InterpretedWorkflow**, 실행 모드는 **phase_a**이다. 관찰 실행기 `--interpretation-mode phase_a`가 runtime 생성 시 기존 `AGENTIC_RAG_QUESTION_INTERPRETATION=phase_a` 선택을 적용하며 생성 클래스도 확인했다. 서비스의 영구 기본값은 기존 legacy 그대로이다.

Phase A는 규칙 우선 QuestionInterpreter → 규칙으로 해석하지 못할 때 한 번의 LLM Interpreter → typed/grounding/canonical 검증 → deterministic InterpretationRouter/인자 연결 → 기존 Tool·Grader·Recovery·답변 흐름이다. LLM에 임의의 실행 순서 결정권을 추가하지 않았다.

legacy와 이번 실행은 질문 원문·순서, retrieval config hash, corpus, embedding model/revision, max_seq_length=128, Dense/BM25 depth=10, positive_only, RRF 1:1/k=60, 최종 Top-10, prompt·validator·provider 설정이 같다. 이번 관찰에서 선택한 핵심 변수는 질문 해석 모드 하나다. child/reranker는 없고 pgvector 재현성 36문항은 재실행하지 않았다.

서비스 retrieval config SHA-256: `02a7d398a7a1597e68a56a4e4d97bea5bb6a59fcd98ea7e7a6ae4bbf56a27330`. 운영 corpus는 기존 **v1**이며 corpus v2로 이전하지 않았다.

실제 로컬 Uvicorn HTTP POST /query를 사용했다. 동일 실제 runtime의 상태 기록 래퍼만 FastAPI 의존성에 연결하고 실제 OpenAI·PostgreSQL·pgvector를 호출했다. 추가 확인이 필요한 문항은 후속 선택이나 인자 보충 없이 그 첫 응답을 보존했다. API 오류도 재요청하지 않는다. graph 내부 Recovery 최대 2회 정책은 기존 그대로다.

## 문항별 상태와 legacy 대조

| 번호 | 해석 출처 | 해석 상태 | Phase A Router | Grader | Recovery 시도 | Phase A terminal | legacy terminal |
|---:|---|---|---|---|---:|---|---|
| 1 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | answer_ready |
| 2 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |
| 3 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |
| 4 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |
| 5 | LLM 검증/실행 실패 | failure | invalid | 미실행 | 0 | execution_failure | answer_ready |
| 6 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |
| 7 | LLM 검증/실행 실패 | failure | invalid | 미실행 | 0 | execution_failure | abstain_ready |
| 8 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | answer_ready |
| 9 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |
| 10 | llm | clarification_required | ambiguous | 미실행 | 0 | abstain_ready | abstain_ready |

## HTTP·지연·사용량

| 번호 | HTTP | Tool 호출 | LLM 호출 | input/output token | 비용 USD | 지연 초 |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 200 | 미실행 | 1 | 2697/142 | 0.00266175 | 11.206 |
| 2 | 200 | 미실행 | 1 | 2700/220 | 0.00180540 | 1.997 |
| 3 | 200 | 미실행 | 1 | 2695/137 | 0.00263775 | 1.889 |
| 4 | 200 | 미실행 | 1 | 2683/125 | 0.00136515 | 1.466 |
| 5 | 200 | 미실행 | 1 | 2704/169 | 0.00157890 | 2.066 |
| 6 | 200 | 미실행 | 1 | 2693/220 | 0.00180015 | 2.758 |
| 7 | 200 | 미실행 | 1 | 2703/198 | 0.00170865 | 3.170 |
| 8 | 200 | 미실행 | 1 | 2691/240 | 0.00188865 | 2.323 |
| 9 | 200 | 미실행 | 1 | 2700/149 | 0.00148590 | 1.717 |
| 10 | 200 | 미실행 | 1 | 2698/201 | 0.00171840 | 2.123 |

전체 Phase A 사용량:

```json
{
  "calls": 10,
  "by_stage": {
    "interpreter": 10
  },
  "input_tokens": 26964,
  "cached_input_tokens": 14336,
  "output_tokens": 1801,
  "total_tokens": 28765,
  "estimated_cost_usd": 0.0186507,
  "usage_unavailable_calls": 0
}
```

추정 비용은 기존 config pricing snapshot을 사용한 추정치이며 청구서 금액이 아니다. 실패 호출에서 usage를 받지 못했다면 token·비용을 확정할 수 없다. 호출 수는 실제 provider invoke 기록이다. HTTP 지연은 서비스 초기화와 모델 로딩을 제외한다. legacy와 Phase A는 서로 다른 시간의 각 1회 실행이며 LLM 변동이 있을 수 있어 차이를 독립 일반화 성능이나 인과적 효과 크기로 해석하지 않는다.

## 관찰 결과와 원인

실제 Phase A에서는 answer_ready 0개, abstain_ready 8개, execution_failure 2개였다. 8개 보류 응답은 모두 `response_mode=clarification_required`이며 추가 확인 입력을 기다리는 상태다. HTTP 200은 이 graph 종료 상태를 반환하는 기존 API 계약이다. 이번 관찰에서는 추가 확인에 답하거나 운동 후보를 선택하지 않았다.

- 10문항 모두 규칙 해석의 full-match 조건에 해당하지 않아 LLM Interpreter를 각각 1회 호출했다. provider 오류는 없고 사용량을 모두 확보했다. 검증까지 완료된 8문항의 `parsing_source`는 `llm`이며 2문항은 검증 단계에서 실패했다.
- Q1·3·4의 모델 출력은 `personal_record_requested=false`, `literature_requested=true`, `clarification_required=false`, `unresolved_fields=[]`였다. 개인 기록이 필요 없는 일반 문헌 질문이지만 `time_condition.scope=unresolved`였고, 기존 `src/interpretation/validator.py:151`의 검사가 개인 기록 요청 여부와 무관하게 기간 확인을 추가한다. 이후 InterpretationRouter가 `I-ambiguous`로 보내 문헌 검색 전에 종료했다. Q2에는 모델이 기간/비교 대상·근력 지표 확인 요구도 추가했고 validator의 기간 요구가 더해졌다. 이 결과는 Retrieval miss나 Grader 판정 실패가 아니다.
- Q5·Q7은 후보 운동과 Metric을 포함하면서 `personal_record_requested=false`를 출력했다. `src/interpretation/validator.py:66`이 이 상충된 필드 조합을 `GroundingError`로 거절하며 공개 상태는 `ungrounded_interpretation` / execution_failure가 된다. 저장된 모델 출력의 기존 validator offline 재실행으로 최초 거절 사유 `candidates need an original personal exercise mention`을 확인했다. 이는 원인 확인용이며 합성 repository를 사용했고 그 조회 전에 거절되므로 LLM·DB·REST 호출은 0이었다. 운영 코드나 검증 기준은 바꾸지 않았다.
- Q6은 개인 기록/training_gap 요구를 인식했으나 기간·비교 구간 불명확성과 `exercise_first_last` 세션 선택이 현재 Phase A의 전체/기간 조회 결합 범위에 맞지 않아 추가 확인에서 종료했다. 문헌 요구는 false로 해석됐다.
- Q7은 근력 도움이 되는지 묻는 절을 추가 estimated_1rm Metric으로도 해석하고, 두 Metric 및 literature_subquestion을 출력하면서 literature_requested=false로 표시했다. 최초 거절은 위의 personal_record_requested 불일치지만, 원문 요구와 해석된 채널/Metric의 불일치도 기록한다.
- Q8은 training_gap과 문헌 하위 질문을 출력했지만 개인 기록·문헌 요청 flag는 모두 false였다. validator가 채널 누락·불일치와 기간 등 확인 요구를 추가해 실행을 멈췄다. legacy의 잘못된 공백 답변은 이번에 재생성되지 않았지만, 계산을 올바르게 완료한 것도 아니다.
- Q9·Q10의 모델은 각각 의료 진단/재활 및 식단/보충제 계획이 현재 지원 범위 밖임을 기록했다. 그러나 응답은 명확한 범위 부족 보류와 입력 부족을 구분하지 않고, 기간·지표 등 추가 정보 요청과 함께 clarification_required로 반환됐다. 해당 서비스를 안전하게 제공할 수 있다는 검증으로 해석하지 않는다.
- 모든 문항에서 Log/Metric/Literature Tool, Step 2/3, Evidence Grader, Recovery 및 답변 생성 LLM은 0회였다. 실제 호출은 DB 초기화·운동 catalog 읽기와 OpenAI Interpreter이며, 질문별 pgvector similarity 검색은 0회다. Phase A의 downstream 흐름이 실제 질문에서 정상 완주했다고 주장하지 않는다.

LLM은 총 10회, input 26,964 token(이 중 cached 14,336), output 1,801 token, total 28,765 token, 추정 비용 $0.0186507이다. HTTP 요청 지연 합계는 30.714초다. 기존 legacy와의 대조는 문항별 1회 정성 관찰이며 일반화 accuracy 또는 Phase A 효과 크기를 주장하지 않는다.

## 검증과 보존

| 검증 | 결과 |
|---|---|
| 관련 관찰 래퍼·Phase A·검색어·REST 합성 테스트 | 123 passed |
| 공개 전체 테스트 | 832 passed, 81 deselected |
| 로컬 전체 테스트 | 913 passed |
| 실제 Phase A REST | 같은 질문 10개 각 1회, HTTP 200 |
| 보호 파일·기존 legacy 결과 | 1,387개 작업 전/후 SHA-256 불변 |

단위/회귀 테스트에서는 실제 OpenAI·PostgreSQL을 호출하지 않았다. 실제 REST 관찰의 Interpreter/provider/DB 경로와 분리해 보고한다. workspace 테스트 수에는 작업 전 미추적 감사 테스트 19개가 포함되며 해당 파일은 commit하지 않는다. 이번 tracked 변경은 관찰 실행기의 opt-in 모드·Interpreter keyword 인자 전달·추가 해석 기록, 관련 합성 테스트 및 새 공개 결과 요약뿐이다. 서비스의 영구 기본 해석 모드는 바꾸지 않았고 `src/` 실행 코드·config·prompt·validator·corpus·Gold·기존 보고서는 그대로다.

동결 corpus·selection/build·평가/Gold·baseline·human decision·기존 연구 보고서·alias와 모든 `src/` 코드, 이전 legacy 결과를 포함한 1,387개 파일의 SHA-256을 검증했다. 새 raw 결과 SHA-256: `dfb28b60c11cb07fdcf9cf489a500365141276f4dcc8277f1d3de770648c5a1f`. 기존 legacy raw 결과 hash `6c91298fb10c54fd257d9cbf03ff64fd5f4cbf414971ba09b4732c68a719cebf`도 그대로다. 보호 목록과 진단 기록은 로컬 `reports/service_retrieval_v2_phase_a/`에 보존한다.

## 상세 문서 보존

문항별 해석 결과·원본 HTTP 응답·Step 2/3 검색어·보완 사유·검색 문헌 제목/section/parent/chunk ID·Log/Metric 주요 결과·Grader·Recovery query·token/비용·최종 답변 전문은 새 로컬 `reports/service_retrieval_v2_phase_a/E2E.md`와 `rest/rest.json`에 저장했다. 기존 legacy 원본과 보고서는 변경하지 않는다. docs/PUBLISHING.md에 따라 개인 기록 상세와 raw provider 응답은 GitHub에 재배포하지 않고 공개 요약만 commit한다.
