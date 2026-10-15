# Phase A 근거 필요성 분리 구현·검증 기록

기준선 master: `685e2b9194419fbf757df2df2156299b77e4afe3`.
구현 코드 commit: `dee6bd2fe9a74cc7b895e682470b908f22d68b01`.
계약 commit: `15e0207`.
작업 branch: `feature/phase-a-evidence-needs`.
이 문서는 위 구현 이후의 문서 전용 commit으로 추가한다. 실행 코드 SHA는 위 값이다.
계약과 JSON Schema는 [계약 문서](PHASE_A_EVIDENCE_NEEDS_V1.md)를 따른다.

## 구현 결과

규칙으로 완전히 해석되는 요청은 기존 Rule parser를 재사용하고 LLM을 호출하지 않는다.
그 외에는 기존 Interpreter의 **동일한 1회 호출**에서 answer_type, 근거 필요성,
원문 source span과 기존 인자를 추출한다. 추가 Agent/LLM 왕복/Tool 계획기는 없다.

새 typed 의미 출력에서 코드는 required/optional source를 결정하고 기존
literature_requested와 personal_record_requested를 정규화한다. 개인 기록 필요성은
기존 코드의 기록/Metric/개인 기록 신호와 독립 의미 need를 함께 사용한다.
운동명만 등장했다고 개인 기록을 요청한 것으로 만들지 않는다.
answer_type 하나로 근거 채널을 결정하지 않는다.

목적을 파악했으나 운동·기간·N·Metric 인자가 부족한 경우 evidence need와 type을 보존하고
해당 인자만 clarification한다. type은 알지만 핵심 근거 need가 미확정이면 type을 보존한
목적 clarification을 사용한다. 범위 밖 요청은 Tool/최종 답변 LLM 호출 없이 지원 범위를 안내한다.

최근 기간은 새 config의 recent_window_weeks=8과 마지막 저장 기록일에서 코드가 계산한다.
명시적 기간, 전체/최고 기록, N세션 자체 의미가 우선한다. 날짜·기간의 기존 검증과
원문에 없는 날짜 거절은 유지한다. 잘못된 원문 날짜도 기존 기간 clarification으로 이어진다.
기본 기간은 최종 답변 문구, 내부 provenance, debug trace에서 확인할 수 있다.

`response_metadata.trace.interpreter.phase_a`의 version은 phase_a_semantics_v1이다.
기존 내부 parsing_source(rule/llm), evidence_need, argument_resolution,
default_time_window, scope를 고정 구조로 제공한다. 실패 시에도 얻은 의미와 검증 상태를 보존한다.
비밀값은 기존 allowlist/마스킹 projection으로 제거한다.

`/query/clarify`는 선택 필드 debug=false를 추가했다. true이면 `/query`와 같은 projection을
사용하며 선택을 반영한 인자 상태를 반환한다. Interpreter를 재호출하지 않는다.
debug=false/생략일 때 기존 응답 형식을 유지한다.

서비스 기본값은 **legacy**다. 서비스 factory가 phase_a를 선택한 경우에만 새 config와
semantics_enabled=True를 사용한다. 기존 provider config/prompt와 기본 constructor의 이전
계약은 회귀 검증을 위해 보존한다. 실제 기준선 평가는 반드시 지정 기준선 소스에서 실행한다.

## 검증

| 검증 | 결과 |
|---|---|
| 신규 synthetic 단위·통합·평가기 | 113 passed |
| 관련 단위·통합·회귀 묶음 | 264 passed; 이후 추가 사례는 최종 전체에 포함 |
| 로컬 공개 marker 실행 | 1048 passed, 81 deselected |
| 최종 로컬 전체 | 1130 passed |
| JSON Schema 내보내기 일치 재확인 | 1 passed |
| compileall / diff whitespace 검사 | 통과 |
| 기존 보호 파일 hash | 1461개, 변경/누락 0 |

공개 marker 실행 뒤 오프라인 관찰 회귀 사례 1개를 추가했고, 최종 신규/전체 테스트는 이를 포함한다.
작업 전부터 있던 미추적 테스트 19개는 로컬 공개/전체 실행에 포함되며 PR에는 포함하지 않는다.
GitHub CI는 commit된 공개 소스만 검사하므로 로컬 개수와 다를 수 있다.
GitHub Actions 완료 결과는 최종 PR head의 checks와 작업 완료 보고에서 확인한다.

테스트는 Python 3.12.7의 로컬 C:/mini/python.exe에서 수행했다. GitHub CI는 기존 설정의
Python 3.13을 사용한다. Windows sandbox의 asyncio socket-pair 생성/임시 디렉터리 제약 때문에
같은 테스트를 sandbox 밖에서 실행하고 작업 폴더 아래의 별도 basetemp를 사용했다.
테스트/marker/assertion을 줄이거나 skip/xfail을 추가하지 않았다.

실제 OpenAI 호출 **0**, 서비스 REST 호출 **0**, DB 호출 **0**.
API 검증은 TestClient와 합성 provider/Tool/기록 repository를 사용했다.
실제 LLM token/비용은 발생하지 않았다. mock 사용량을 실서비스 사용량으로 합산하지 않는다.

## Synthetic 검증 범위

실행 전에 고정한 서술형/판단형 10쌍은 weekly volume/frequency, e1RM, training gap에
대해 같은 기록 요청에 평가·원인·조언을 추가했을 때 source 선택이 보존되는지 확인한다.
SHA-256: `6469ee16fd13fec67efb29efad31908985f6e0d3d75a31bfa917500ccd99795f`.
수정하지 않았으며 acceptance 질문/Gold가 아니다. 실제 LLM 의미 정확도를 측정한 결과도 아니다.

추가 합성 검증:

- 명시적 required/optional과 의미상 문헌 need의 코드 결합, boolean 정규화.
- 목적 미확정과 운동/기간/N/Metric 인자 미확정의 구분, 의미 need 보존.
- 이전 개인 기록 신호 재사용, false 개인 기록 플래그의 정규화와 mismatch 기록.
- 마지막 저장일 기준 최근 8주, 현재 평가의 승인된 operation 창, 명시적 기간/전체/최고/N세션 우선.
- 기록 기준일 없음, 승인하지 않은 상대 기간, 잘못된 원문 날짜, 임의 생성 날짜의 보수적 처리.
- catalog/원문 span/N grounding과 복수 Metric 제한의 회귀.
- 범위 밖 요청의 무실행 보류, optional Literature의 기존 Grader 및 2회 Recovery 후 보류.
- query/clarify에서 debug on/off, strict boolean, 운동 선택 후 상태 갱신과 Interpreter 재호출 없음.
- rule/llm parsing_source, provider 실패 trace, 비밀값 제거, 서비스 factory 연결.
- Gold 대안 정확 일치, 중복/잘못된 null 거절, optional 채점, hash/commit/실행 횟수 검증.
- 실행 계획과 실제 Tool 실행의 구분, 인자 검증 실패 후에도 이미 파악한 need의 오프라인 관찰 보존.

## 변경 파일

| 영역 | 파일 |
|---|---|
| 새 의미 계약/정책 | src/interpretation/semantic_contracts.py, semantic_policy.py |
| 기존 Phase A 연결 | src/interpretation/contracts.py, interpreter.py, validator.py, provider.py, exercise_catalog.py, clarification.py, messages.py |
| Phase A 전용 실행 adapter | src/graph/interpreted_workflow.py |
| API | src/api/contracts.py, app.py, dependencies.py, debug_trace.py |
| 새 설정/prompt | config/phase_a_semantics_v1.json, question_interpreter_semantics_v1.json, question_interpreter_semantics_v1.md |
| 오프라인 채점 | src/evaluation/phase_a_semantics.py, scripts/score_phase_a_semantics.py |
| 합성 검증 | tests/test_phase_a_semantics.py, test_phase_a_semantics_evaluation.py, fixtures/phase_a_semantics_synthetic_v1.json |
| 계약/보고 | 본 문서, PHASE_A_EVIDENCE_NEEDS_V1.md, schemas/phase_a_evidence_gold_v1.schema.json, schemas/phase_a_semantics_v1.schema.json |

공유 src/graph/nodes.py와 workflow.py, Router, Retrieval/embedding/BM25/RRF,
corpus, 기존 Gold/벡터, Tool 계산, Grader 의미, Recovery 정책, legacy workflow는 변경하지 않았다.
작업 전 미추적 파일 7개도 보존했다. 원시 기록/모델 응답/개별 실제 trace를 공개 commit에 넣지 않았다.

hash 검사의 목록과 결과, 로컬 실행 출력은 ignored reports/phase_a_evidence_needs_v1에 저장했다.
hash 범위는 작업 전 config, 기존 docs, 공유 graph 2개 파일, data/raw·processed·literature 및
기존 reports다. 기존 Gold 저장 위치에는 쓰지 않았고 새 주 acceptance 파일은 열거나 생성하지 않았다.

## 제한과 다음 단계

optional Literature도 실제 선택되면 기존 graph의 Grader/Recovery/종료 정책에 따라 전체 응답에
영향을 준다. optional 비차단 실행, partial answer, UI, claim decomposition, 복수 Metric,
새 자연어 상대 날짜, 새 Agent/framework는 구현하지 않았다.

LLM은 의미를 잘못 해석할 수 있다. typed schema/원문 grounding과 synthetic 통과가 의미 정확도
향상의 증거는 아니다. 사용자 제공 frozen acceptance 전후 각 1회 비교와 주 성공 기준 판정은
아직 실행하지 않았다. 기존 interpretation_eval_v1도 실제 진단 재실행하지 않았다.
주 판정 후에만 기존 평가를 regression diagnostic으로 사용하며, 결과를 보고 재튜닝하지 않는다.
