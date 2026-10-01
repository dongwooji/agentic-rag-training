# Phase A.2 — 운동 후보 선택

기존 Phase A의 규칙 우선 질문 해석과 single-metric 실행은 유지한다.
여러 저장 운동 후보가 남으면 자동 병합하지 않고 사용자 선택을 기다린다.
이는 post-baseline 실행 계층 변경이며 frozen 평가 결과를 개선한 실험이 아니다.

## 구현 전 조사

1. Phase A는 `QuestionInterpretation.unresolved_fields`와 `clarification_required`로 정보 부족을 표현했다. 실행 상태는 `clarification_required`, 공개 종료 상태는 `abstain_ready`였다.
2. `InterpretedWorkflow`에서 `FinalResponse.provenance.response_metadata.response_mode`를 만들었으나 API의 공개 응답 변환은 metadata를 제외했다.
3. 기존 schema는 단일 canonical 이름과 resolved/ambiguous/not_found 상태만 표현했다. 후보 목록과 후속 요청 저장소가 없었다.
4. A.1은 모델의 ambiguous 상태를 미해결 운동 필드로 반환했다. 이후 로컬 관계 정책이 해머컬 두 이름을 자동 공동 조회로 바꿔 사용자가 범위를 선택할 수 없었다.
5. 저장된 해석에서 운동 선택만 확정하고 InterpretationRouter와 Argument Binder 직전으로 돌아가는 것이 최소 변경이다. 원 질문을 다시 Interpreter에 넣을 필요가 없다.
6. `/query`의 기존 질문 규격을 유지하고 별도 `/query/clarify`를 추가한다. 질문과 후속 선택의 입력 검증을 분리하며 기존 클라이언트를 유지한다.

## 흐름

```text
/query → 기존 rule parser 또는 Interpreter 1회 → 후보·원문·catalog 검증
  ├─ 단일 후보 → Router → Binder → 기존 Tool/workflow
  └─ 복수 후보 → abstain_ready + clarification metadata (후속 Tool 0회)
                  ↓ 사용자 option id
/query/clarify → TTL·단일 사용·허용 option·현재 catalog 검증
              → 선택만 해석에 주입 → Router → Binder → 기존 Tool/workflow
```

`candidate_exercises`는 현재 catalog의 이름으로 제한한다. 기존 좁은 rule path도 동일 후보 검증을 거친다.
원문 운동 표현과 확실한 기존 별칭/명시 장비에 충돌하는 후보는 거부한다.
모델은 후보를 제안할 수 있으나 검증된 복수 후보가 남으면 단일 이름을 출력했더라도 실행하지 않는다.
명시한 장비를 잘라 generic 표현으로 추출한 경우에도 승인된 명시 표현 규칙이 우선한다.
문자열 유사도만으로 후보를 만들거나 DB 목록 밖의 이름을 제안하지 않는다.

## 후보 정책

운동별 조건문이 아니라 별도 선언적 후보 정책과 공통 store/resume 흐름을 사용한다.
실제 processed canonical 목록을 확인한 사례:

- 해머컬 → Hammer Curl / Hammer Curl (Dumbbell)
- 시티드 숄더 프레스 → Seated Shoulder Press (Barbell) / Seated Shoulder Press (Dumbbell)
- 랫풀다운 → Lat Pulldown / Lat Pulldown (Cable) / Lat Pulldown Closegrip

덤벨 해머컬은 단일 이름만 조회한다. generic 해머컬은 두 후보를 선택지로 제시한다.
‘두 기록 함께’는 후보 전체가 기존 승인된 관계 그룹과 정확히 일치할 때만 추가한다.
다른 장비/그립 변형에는 자동 공동 조회 선택지를 제공하지 않는다.
공동 조회 시 set ID와 저장 이름을 보존하고 중복 가능성을 안내한다. 겹친 값이 발견되면 기존 Metric 계산 차단 정책을 유지한다.
이 단계에서는 원자료 재분류나 중복 제거를 하지 않는다.

## API

기존 `POST /query {"question": "..."}`는 그대로 사용한다.
선택이 필요한 경우 기존 공개 필드 외에 선택적 `response_metadata`를 추가한다.

```json
{
  "response_mode": "clarification",
  "clarification_required": true,
  "clarification_id": "서버가 반환한 ID",
  "clarification_type": "exercise_selection",
  "options": [
    {"id": "1", "label": "Hammer Curl", "canonical_exercises": ["Hammer Curl"]},
    {"id": "2", "label": "Hammer Curl (Dumbbell)", "canonical_exercises": ["Hammer Curl (Dumbbell)"]},
    {"id": "3", "label": "두 기록 함께", "canonical_exercises": ["Hammer Curl", "Hammer Curl (Dumbbell)"]}
  ],
  "expires_at": "서버가 반환한 만료 시각"
}
```

후속 요청:

```json
{"clarification_id": "첫 응답의 ID", "selected_option": "2"}
```

`POST /query/clarify`는 같은 공개 응답 구조를 반환한다. canonical 이름을 임의 입력하는 API가 아니라 서버 옵션 ID만 선택하는 규격이다.
잘못된 옵션은 HTTP 400이며 요청을 소모하지 않는다. 만료는 410, 존재하지 않거나 이미 사용한 ID는 404다.
후속 schema 오류는 422, Phase A를 사용하지 않는 서버는 409다. 내부 예외는 상세 내용 없이 500이다.
원래 기간/N 등 다른 미해결 조건은 보존한다. 운동을 선택했다고 다른 조건을 추측하거나 Tool을 실행하지 않는다.

## 저장 및 호출 수

workflow 인스턴스별 in-memory store, TTL 20분, 최대 1,024개 대기 요청이다.
질문, 검증된 해석, 원래 해석 trace/출처, 후보와 선택지, 생성·만료 시각을 저장한다.
UUID ID와 lock을 사용해 동시 후속 요청도 한 번만 소모한다. 입력·저장 객체는 복사해서 공유 변경을 막는다.
잘못된 선택은 재선택 가능하지만 유효한 선택은 실행 전에 소모한다. 이후 실행 실패 시 자동 재실행하지 않는다.
새 요청 등록 시 만료 항목을 정리한다. 만료된 요청의 데이터는 외부로 반환하지 않는다.

질문 해석 호출 수는 기존처럼 rule 0회 또는 LLM 최대 1회다.
선택 후에는 Interpreter/ToolInputResolver LLM을 호출하지 않는다. 실제 답변 생성·문헌 판단에 필요한 기존 LLM 호출은 별개로 유지한다.
확인 단계에서는 Training Log/Metric/Literature Tool, Grader, Recovery, Final Answer provider 모두 호출하지 않는다.
공개 metadata는 allowlist typed schema로 전달하고 내부 provider trace/비밀값을 공개하지 않는다.

## 파일 책임

- `interpretation/contracts.py`, `exercise_catalog.py`: 후보 목록 및 catalog enum 제한.
- `exercise_selection.py`, `config/exercise_selection_v1.json`: 선언적 후보·명시 표현 정책.
- `validator.py`: 후보·원문·catalog·장비 충돌 검증 및 모호성 보존.
- `clarification.py`: TTL store, 단일 사용 선택 검증, 운동 필드만 확정.
- `graph/interpreted_workflow.py`: 확인 응답과 재개; 기존 Router/Binder 호출.
- `graph/runtime_group_training.py`: 선택한 한 이름은 그대로 조회, 명시적 그룹 선택만 공동 조회.
- `api/contracts.py`, `api/app.py`: 추가 metadata와 별도 후속 endpoint.
- `question_interpreter_prompt.md`: 기존 1회 typed 해석에 후보 목록을 포함하는 최소 규격 변경. 모델/config/판단 prompt 튜닝 없음.
- `tests/test_exercise_clarification.py`: store, 선택, API, 호출 차단, 장비·catalog 검증.
- 기존 Phase A/A.1 및 관계 테스트: 새 schema fixture와 선택 후 공동 조회 정책 반영.

## Swagger live smoke 순서 (구현 단계에서는 실행하지 않음)

Phase A로 서버를 재시작한 뒤 `/docs`에서:

1. `/query`: `{"question":"내 해머컬 기록 보여줘."}` → 확인 metadata, Tool 0회.
2. `/query/clarify`: 첫 ID와 `"selected_option":"2"` → 덤벨 해머컬만 조회.
3. 같은 후속 요청을 반복 → 404, 실행 없음.
4. 새 해머컬 질문 → 새 ID로 `"3"` 선택 → 명시적 공동 조회, 중복 제한 안내.
5. `/query`: `{"question":"내 덤벨 해머컬 기록 보여줘."}` → 단일 조회.
6. `/query`: `{"question":"내 시티드 숄더 프레스 기록 보여줘."}` → 바벨/덤벨 후보 선택 후 단일 조회.
7. `/query`: `{"question":"내 랫풀다운 기록 보여줘."}` → 후보 선택.
8. `/query`: `{"question":"내 데드리프트 기록 보여줘."}` → 단일 조회.

조회 후 최종 답변 생성에는 기존 OpenAI API 비용이 발생할 수 있다. 확인 중 Interpreter fallback 1회가 발생할 수도 있다.

## 한계 및 보존

프로세스 재시작 시 선택 state는 사라지고 다중 worker/server 간 공유되지 않는다.
인증/사용자별 소유권은 아직 없으므로 로컬 단일 프로세스 Swagger 검증용이다. 공개 서비스에는 사용자 결합, rate limit과 외부 저장소가 필요하다.
후보 정책은 검증된 표현만 포함하며 모든 한국어 모호성을 포괄하지 않는다. LLM 후보의 의미적 적절성을 완벽히 보증하지 않는다.
이 단계의 후속 API는 운동 선택만 처리하며 기간/N의 일반 대화형 수집은 제공하지 않는다.
Multi-Metric, analysis_goal/hypothesis, Redis/대화 메모리, 검색/Grader/Recovery/Fusion/Tool 계산식은 추가·변경하지 않는다.
frozen baseline, eval/Gold 및 exercise_aliases_v1.csv는 수정하지 않는다.
Structured output 방식은 기존 Responses parse를 재사용한다 ([OpenAI 문서](https://developers.openai.com/api/docs/guides/structured-outputs)).
