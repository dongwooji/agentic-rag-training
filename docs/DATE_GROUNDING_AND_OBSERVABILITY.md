# 날짜 정규화와 관찰 기능

Phase A는 원문에 명시된 연도·연월·날짜 범위를 코드로 정규화한다. LLM의 기간은 이 범위와 비교하며, 다르면 코드 값으로 정규화하고 output-only `date_range_normalization`에 원래 값·계산 값·불일치를 남긴다. Interpreter prompt와 LLM 입력 schema는 변경하지 않았다.

| 표현 | 시작일 | 종료일 |
|---|---|---|
| `2032년` | `2032-01-01` | `2032-12-31` |
| `2032년 2월` | `2032-02-01` | `2032-02-29` |
| `2030년부터 2032년까지` | `2030-01-01` | `2032-12-31` |
| `2034년 9월~12월` | `2034-09-01` | `2034-12-31` |
| `2034년 9월부터 12월까지` | `2034-09-01` | `2034-12-31` |

월 말은 달력으로 계산하며 윤년을 반영한다. 월 범위의 뒤쪽에 생략된 연도는 명시된 앞쪽 연도만 사용한다. 연도 없는 단독 월, 역전된 범위, 잘못된 날짜, 여러 모호한 기간은 임의로 현재 연도나 다른 기간으로 채우지 않는다. 두 개의 별도 날짜를 연속 범위로 임의 합치지 않는다.

원문에 기간이 없는데 모델이 만든 날짜는 실행 입력으로 인정하지 않는다. 기간 오류는 원문 JSON에서 거절된 날짜만 제거해 다른 운동·숫자·Metric grounding을 다시 검사한다. LLM을 재호출하지 않는다. 다른 grounding 오류는 기존 실행 실패 처리를 유지한다.

기간 확인이 필요하면 해석 상태는 `clarification_required`다. 공개 API는 기존 계약에 따라 `final_status=abstain_ready`, `response_metadata.clarification_required=true`, `response_mode=clarification_required`를 반환한다. `graph_execution_failure`로 변환하지 않는다. 사용자 문구는 코드가 만들며, 예를 들어 “어느 기간을 볼까요? 예: 2018년 9월~12월”로 안내한다. LLM의 내부 판단 문장을 공개 답변에 그대로 넣지 않는다.

개인 기록의 첫·마지막 날짜는 PostgreSQL의 실제 저장 세트가 있는 세션에서 읽기 전용으로 조회한다. 날짜를 고정하지 않는다. 요청 기간이 전체 기록 범위와 전혀 겹치지 않거나 저장 기록이 없으면 “해당 기간 기록이 없습니다. 다른 기간을 선택해주세요.”로 보류하며 Tool·Metric·LLM 답변 생성을 시작하지 않는다. 일부만 겹치는 기간은 원래 요청의 시작·종료일을 유지한다. 기록이 없는 날을 관찰한 날로 가정하지 않는다.

## 서버 진단 로그

`agentic_rag.diagnostics` logger는 JSON으로 `request_id`, `stage`, `check_id`, `field`, `exception_type`, 안전한 `reason`을 기록한다. 요청 ID는 서버가 생성하며 HTTP 처리 문맥에서 공유하고 종료 후 제거한다. 검사·단계 이름은 허용 목록으로 제한한다. 예외 문자열·traceback·인증 헤더·API key·DB 비밀번호·원시 운동 기록은 기록하지 않는다. schema 오류는 선언된 필드 위치만 기록한다. 로그 handler 오류로 실행이나 재시도 횟수가 바뀌지 않는다.

기간 grounding 오류도 서버 로그에 남지만 공개 API에서는 되묻기로 처리한다. 다른 실행 실패는 API에서 기존의 안전한 오류 응답을 유지한다. 정상적인 근거 부족 보류를 실행 예외로 표현하지 않는다.

## 선택적 `/query` trace

```json
{
  "question": "원하는 질문",
  "debug": true
}
```

`debug`를 생략하거나 `false`로 보내면 응답에 trace가 없다. `debug`는 JSON boolean만 허용한다. `true`이면 `response_metadata.trace`에 다음을 제공한다.

- `request_id`: 서버 로그와 연결하는 ID.
- `interpreter`: 모델 출력의 의미 필드 요약과 검증·정규화 결과. legacy는 Interpreter를 사용하지 않으므로 `not_used`로 표시한다.
- `initial_queries`: 실제 Step 2 한국어 검색어와 Step 3 번역 검색어, 실제 Dense/BM25 검색어, 보완 여부. 번역 보완 경로가 사용되면 영어 칸에도 실제 Step 2 검색어가 올 수 있다. 호출되지 않은 단계는 비어 있다.
- `retrievals`: 초기 검색과 Recovery 회차별 실제 Top 근거의 제목·section·chunk ID·순위.
- `graders`: 각 회차의 판정, 구성 요소별 지원·부족 상태와 사용된 근거. provider 실패 회차는 실패로 표시하며 예외 상세는 넣지 않는다.
- `recovery`: 회차별 재검색어, 검색된 근거와 이전 검색들에 없었던 새 근거.
- `fusions`와 `final_evidence`: 회차별 결합 결과와 최종 근거 순서·출처·결합 점수.

trace는 이미 실행한 결과를 선택한 필드로 투영한다. 추가 LLM·검색 호출이나 재시도를 하지 않는다. 논문 본문·개인 기록·prompt·provider 응답 ID·예외 메시지를 포함하지 않으며, 텍스트 필드에 섞인 설정 비밀값과 일반적인 credential 표현도 가린다. malformed 진단 필드가 있으면 trace의 검증 상태를 `unavailable`로 표시하고 기존 최종 상태를 유지한다. 운동 선택으로 멈춘 요청에는 그 이후 실행되지 않은 단계가 표시되지 않는다. `/query/clarify`의 입력·응답 계약은 유지한다.

Grader는 최대 3회, Recovery는 최대 2회, 검색 근거는 회차별 최대 10개로 관찰 결과를 제한한다. 상태와 재시도 종료 조건, retrieval 설정·corpus·Router·prompt는 변경하지 않았다. 서비스 기본 interpretation mode는 `legacy`를 유지한다. 날짜 정규화와 기록 범위 안내는 Phase A 경로에 적용되며 로그와 선택적 trace는 두 경로 모두에서 사용할 수 있다.

## 검증 범위

새 synthetic 테스트는 달력 범위·윤년·기간 불일치 정규화·원문 없는 기간 거절·숫자 grounding 유지·기록 범위 밖/부분 중복·사용자 문구·로그 비노출·request_id 분리·debug 기본 응답 호환·회차별 trace·진단 실패의 상태 보존을 검사한다. `interpretation_eval_v1`의 test 20문항을 입력으로 사용하지 않았다. 실제 OpenAI/DB 실행을 통한 재평가는 이번 작업에 포함하지 않는다.

최종 새 synthetic 테스트 52개와 기존 운동 선택 회귀 23개가 함께 통과했다(75 passed). 공개 테스트는 910 passed / 81 deselected, 로컬 전체 테스트는 991 passed다. 기존 config·corpus·Gold·평가 자료·보고서 1,397개 SHA-256이 모두 동일하다. 테스트는 mock/TestClient·LangGraph와 로컬 산출물 검증이며 실제 OpenAI·DB 호출 검증과 구분한다.

기록 범위 검사는 전체 저장 기록의 날짜 범위가 대상이다. 그 범위 안에서 특정 운동의 기록이 비어 있는 경우까지 Metric/Tool 의미를 새로 설계하지는 않았다. 상대 기간과 복잡한 복수 기간은 기존 확인 정책을 유지한다. 이미 실행 중인 Uvicorn 프로세스는 재시작해야 새 요청 계약과 코드를 읽는다.
