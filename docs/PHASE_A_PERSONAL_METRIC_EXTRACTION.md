# Phase A 개인 Metric 추출 규칙 v1

이 변경은 acceptance v2 분석 후의 post-hoc correctness 수정이다. 기존 acceptance는 재실행하지 않았으며 기존 성능 수치를 수정 후 코드의 성능으로 해석하지 않는다.

PR 1 base/head: `e0718ce67ca3faed39312b5caac4e1c162cfdfbe`. PR 2는 그 branch 위에 별도 PR로 제출한다.

## 선택과 보존

기존 `question_interpreter_semantics_v1.{json,md}`를 덮어쓰지 않는다. 새 prompt/config는 `question_interpreter_semantics_v1_1.{json,md}`이며 typed schema와 모델·sampling·retry 설정은 동일하다.

명시적으로 선택할 때만 새 규칙을 사용한다:

```powershell
$env:AGENTIC_RAG_QUESTION_INTERPRETATION = 'phase_a'
$env:AGENTIC_RAG_PHASE_A_EXTRACTION_VERSION = 'personal_metric_v1'
```

선택값 `original` 또는 환경변수 생략은 기존 추출 버전이다. 서비스 interpretation 기본값은 `legacy` 그대로다. 추출 버전은 Phase A 구성에서만 사용하며, Router나 공유 graph의 의미는 바꾸지 않는다. 기존 설정을 실행하는 historical runner도 원래 동작을 유지한다.

## 추출 계약

`requested_analyses`에는 개인 기록에서 실제 계산해 달라고 한 operation만 넣는다. 일반 문헌 설명의 효과·원인·적응·조언에 등장하는 결과변수나 개념을 별도의 개인 Metric으로 바꾸지 않는다. 문헌 요구는 `literature_subquestion` 및 evidence need에 보존한다.

실제로 독립적인 개인 Metric을 여러 개 요청하면 모두 추출하고 기존 단일 Metric 제한을 적용한다. 내부 계산 의존성은 추가 요청으로 세지 않는다. 동일한 실행 요청은 한 번만 출력하고 다른 인자는 보존한다. analysis source는 원문의 개인 계산 요청을 직접 뒷받침해야 한다. 의미가 불확실하면 추측해 Metric을 추가하지 않고 목적/근거 요구를 보존한 인자 clarification을 한다.

특정 평가 표현에 대한 keyword→Metric 매핑, 새 Metric, 복수 Metric 실행, 새 LLM 호출 단계는 추가하지 않는다. 날짜·운동 grounding, 최근 8주 정책, required/optional, optional Literature blocking, retrieval, Grader, Recovery와 legacy는 유지한다.

## freeze와 확인

- 새 synthetic 8문항 SHA-256: `6d07cb74db25fc88b7e7577845fede8fe5f1f8ba6735b80c2d779aedc1c76c99`
- 새 prompt SHA-256: `c87eee554942463eeeab5b70ce1fe551a48565573cb731651c79ac2370ddb73a`
- 질문은 실제 호출 전에, 추출 규칙은 실제 확인 결과를 열기 전에 고정했다.
- PR 1만 적용한 상태와 새 추출 버전을 선택한 PR 2 상태에서 질문마다 한 번씩 실제 `/query`를 실행한다. 자동 재시도·재실행·사후 튜닝은 없다.
- 기존 Grader, 동일 모델·DB·corpus v1, Phase A, debug=true를 사용한다. 최종 동작에는 후속 retrieval/Grader/답변 생성의 영향도 포함되므로 추출 규칙만의 성능 차이로 해석하지 않는다.
- 실제 결과는 별도 동작 확인 보고서에 기록한다. acceptance 또는 일반화 성능 평가가 아니다.
