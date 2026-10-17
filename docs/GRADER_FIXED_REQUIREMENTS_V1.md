# 고정 요구사항 Grader v1 실행 안내

구현·평가 계약은 [동결 문서](decisions/GRADER_FIXED_REQUIREMENTS_V1.md)와
[protocol](decisions/grader_fixed_requirements_v1_protocol.json)이 기준이다.
기존 Grader 파일과 서비스 기본값은 보존한다. 이 버전은 새 Agent가 아니라 기존 Grader port의 명시적 선택 구현이다.

## 선택과 처리

`AGENTIC_RAG_GRADER_VERSION`을 지정하지 않거나 `legacy`이면 기존 Grader를 사용한다.
`fixed_requirements_v1`을 명시한 경우에만 신규 provider와 계약을 사용한다.
해석 모드의 기본값 `legacy`, Retrieval 설정, corpus, Recovery max_retry=2에는 영향을 주지 않는다.

검증된 Step 2 질문을 우선 사용하고, 검증된 Phase A 문헌 질문은 보완 경로다.
첫 호출에서 질문형 요구사항과 첫 근거 판정을 함께 받는다. 요구사항 source span과 방향 span의 출처를 검증한다.
그 뒤 요구사항을 hash와 함께 저장하고, 재검사에서는 ID/status/chunk ID만 받는다.
전체 요구사항을 현재 결합 Top-10으로 매번 재평가하고 기존 membership/finalizer를 사용한다.
LLM이 질문 전체를 올바르게 분해했는지까지 코드가 증명하지는 않는다.

## debug trace 추가 계약

기존 `query_debug_trace_v1`과 `phase_a_semantics_v1` 필드명을 변경하지 않는다.
신규 Grader의 요구사항이 정상 확정되고 요청 debug=true인 경우에만 `grader_requirements_v1`이 추가된다.

| 필드 | 타입·null | 의미 |
|---|---|---|
| version | string, non-null | `runtime_evidence_grader_fixed_requirements_v1` |
| question_source | `step2` 또는 `phase_a`, non-null | 검증된 문헌 질문의 출처 |
| literature_question | string, non-null, 최대1500자 | 고정 요구사항을 만든 질문 |
| sha256 | string, non-null, 64자리 hex | 전체 고정 요구사항 계약의 canonical JSON hash |
| components | array, 1~8개 | C1..Cn 순서의 요구사항 |
| components[].component_id | string, non-null | `C1`~`C8` |
| components[].topic | string, non-null, 최대450자 | 중립적인 문헌 주제·비교 조건 |
| components[].direction | `open`/`supporting`/`opposing`, non-null | 명시 요청의 방향 |
| components[].source_span | string, non-null, 최대1500자 | 문헌 질문의 실제 부분 문자열 |
| components[].direction_span | string 또는 null, 최대2000자 | 명시적 방향 요청의 원문; open은 null |

실제 렌더링된 질문형 requirement와 현재 판정·지원 ID는 기존 `graders[].components`에 있다.
debug가 없으면 이 정보는 공개 응답에 추가하지 않는다. 내부 예외·provider 정보·secret은 trace에 투영하지 않는다.

## 평가 실행

신규 Grader로 dev를 미리 실행하지 않는다. synthetic 검증과 회귀 테스트를 완료한 구현 commit의 source hash,
동결 protocol hash, 테스트 결과를 `reports/grader_fixed_requirements_v1/implementation_seal.json`에 기록한 뒤 판정 실행한다.
명령의 secrets JSON에는 명시적으로 승인된 OpenAI credential을 사용하며 값은 출력·commit하지 않는다.

```powershell
C:/mini/python.exe scripts/run_fixed_requirements_v1.py --phase dev --secrets-json .env.local.json
# dev_decision.json과 그 SHA-256 봉인을 확인한 뒤에만:
C:/mini/python.exe scripts/run_fixed_requirements_v1.py --phase heldout --secrets-json .env.local.json
C:/mini/python.exe scripts/run_fixed_requirements_v1.py --phase report
```

각 실행은 write-once다. `*_started.json`이 있으면 해당 평가를 다시 실행하지 않는다.
중단·오류도 기록이며 파일을 지워 재실행하지 않는다. 성공·실패를 보고한 뒤 임의로 재튜닝하지 않는다.
평가는 DB/REST/최종 답변 생성 없이 저장 최초 ranking과 기존 offline retriever를 사용하는 문헌 처리 흐름 평가다.
Gold는 실행 결과 저장 후 scorer만 읽는다. 조건별 초기 판정 공유 때문에 호출 수·비용은 event ID 중복을 제외한 실제 호출에서 집계한다.

개발 검증은 신규 synthetic 테스트와 기존 공개·전체 회귀다. 신규 코드 효과는 별도 판정 실행 결과에서만 판단한다.
기존 acceptance·Gold·검색어·벡터·기존 평가 결과는 수정하지 않는다. PR을 자동 merge하지 않는다.
