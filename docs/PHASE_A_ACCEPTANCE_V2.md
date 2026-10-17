# Phase A acceptance v2: 실행 전 계약

이 문서는 실행 준비만 고정한다. 실제 acceptance 질문·Gold는 만들지 않았고, 사용자가 별도로 freeze하여 실행 단계에 제공한다. 기존 dev/dev-ko 36입력, held-out, acceptance v1은 재실행하지 않는다.

## Gold schema

새 version은 `phase_a_evidence_gold_v2`다. 기존 `phase_a_evidence_gold_v1`의 필드·허용값·null/빈 배열 의미·검증을 그대로 상속하고 필수 enum 필드 하나를 추가한다.

- `literature_answerability: "answerable" | "unanswerable" | "not_applicable"` (null/생략 불가)
- `answerable`: frozen corpus v1에 해당 문헌 질문의 주요 조건을 다루어 답할 근거가 있다. 긍정 결론만 뜻하지 않으며 부정적·제한적 결론도 답이다.
- `unanswerable`: 문헌이 필요하지만 frozen corpus v1에 해당 주제/핵심 조건을 다룰 근거가 없다. 한 번의 검색 실패를 뜻하지 않는다.
- `not_applicable`: 답변에 문헌이 필수가 아니다. `required_evidence_sources`에 literature가 없는 경우만 허용하며 optional Literature만 있는 경우도 여기에 해당한다. 따라서 개인 기록만으로 충분한 답변을 '근거 없는 답변'으로 세지 않는다.

`required_evidence_sources`에 literature가 있으면 answerable 또는 unanswerable만 허용한다. 검색 결과나 Grader를 보고 라벨을 만들지 않는다. corpus answerability와 전체 실행 가능성(운동·기간 모호성, 지원 범위)은 다른 축이며 `expected_final_behavior`는 기존 방식으로 별도 기록한다.

기존 필드 목록:
`case_id`, `pair_id`, `expected_answer_type`, `required_evidence_sources`, `optional_evidence_sources`, `acceptable_record_operations`, `acceptable_metric_operation_sets`, `clarification_required`, `allowed_clarification_fields`, `expected_final_behavior`, `expected_default_recent_window`.

기존과 같이 record 미사용은 `[null]`, Metric 미사용은 `[[]]`다. 바깥 대안 목록의 `[]`/null은 금지한다. Metric 대안은 순서 무시·중복 금지·집합 완전 일치이며, 허용 추가 Metric은 해당 대안 집합에 포함되어야 한다. expected operation alias는 추가하지 않는다.

정확한 JSON Schema는 [phase_a_evidence_gold_v2.schema.json](schemas/phase_a_evidence_gold_v2.schema.json), 실행 가능한 검증·채점 계약은 `src/evaluation/phase_a_acceptance_v2.py`다. 질문 파일은 JSONL로 `case_id`, `pair_id`, `question`만 가지며 Gold를 포함하지 않는다. 여기에는 실제 입력 예시를 작성하지 않는다.

## 채점

기존 acceptance v1 `score`를 그대로 호출해 다음을 계산한다.

- 필수 채널 누락(해석 채널 누락과 실행 채널 누락을 구분)
- 불필요한 채널 추가, Record/Metric 허용 대안 일치
- 부당한 되묻기·보류: expected answer인데 실제 clarification 또는 abstain
- 최종 동작 정확도: 기존 v1과 동일하다. answer는 필요한 채널 실행과 operation 일치도 요구하며 clarification은 허용 clarification 필드도 확인한다. 답변 내용의 사실 정확도와는 다르다.
- 실행 실패: graph execution_failure, HTTP 비200, 전송 실패, 비정상 응답 포함. HTTP 실패와 graph 실패가 겹쳐도 문항당 한 번 센다.

추가 지표는 다음의 **문항 수**다. 분모(각 라벨 수)도 함께 저장한다.

1. `answerable_abstain`: answerable인데 실제 abstain. clarification/HTTP 실패는 포함하지 않고 기존 지표로 별도 센다.
2. `unanswerable_answer`: unanswerable인데 실제 answer. 문헌이 필수가 아닌 not_applicable은 제외한다.

추가 지표는 corpus 라벨 기준의 동작 불일치다. 안전 보류나 다른 입력 제약이 있을 수 있으므로 개별 오류의 원인을 이 수치만으로 확정하지 않는다. 선택적 문헌의 실행 의미는 바꾸지 않는다.

## 채택 조건

A=기존 Grader, B=수정 새 Grader. 아래 네 조건을 모두 만족할 때만 `eligible_for_adoption=true`다.

1. B answerable_abstain < A answerable_abstain
2. B unanswerable_answer ≤ A unanswerable_answer
3. B execution_failure ≤ A execution_failure
4. B final_behavior_accuracy ≥ A final_behavior_accuracy

분모·threshold·Gold·정책을 결과에 맞춰 바꾸지 않는다. answerable이 0건이면 1번의 엄격 감소를 만족할 수 없다. 이 준비 작업은 서비스 기본값을 바꾸지 않으며, 평가에서 조건을 충족해도 채점기는 설정을 자동 변경하지 않는다.

## 실행·봉인 순서

사용자 제공 manifest의 version은 `phase_a_acceptance_v2_manifest`다. `code_sha`와 `files.questions`, `files.gold`의 상대 `path` 및 `sha256`을 포함한다. Gold는 별도 JSON 파일이다. 두 파일은 manifest 폴더 안에 두며 manifest 자체 SHA-256도 별도로 전달한다.

1. 제공된 manifest SHA와 현재 clean code SHA를 확인한다. questions SHA를 확인하고 **questions만** 파싱한다. 이 단계에서는 Gold 파일을 열거나 hash하지도 않는다.
2. 같은 checkout/Python에서 독립 서버 프로세스 A/B를 순서대로 시작한다. 해석은 둘 다 phase_a, corpus는 v1, retrieval 설정·모델·DB는 동일하다. 유일한 실행 설정 차이는 `AGENTIC_RAG_GRADER_VERSION=legacy / fixed_requirements_v1_1`이다.
3. 각 질문에 `/query` `debug:true` POST 한 번. 순서는 A 전체 → B 전체다. 자동 HTTP/LLM 재시도, clarification 재개, 대화 이력은 없다. 기존 bounded Recovery는 양쪽 모두 그대로 실행한다. 이번 acceptance는 실제 REST 최종 응답을 관찰하므로 기존 최종 답변 생성도 실행한다.
4. 평가 전용 observer가 기존 workflow state를 allowlist로 투영하여 실제 Tool operation·해석·상태를 기록한다. 질문/label/case ID를 runtime 실행 인자로 추가하지 않는다. HTTP 응답과 관찰 자료는 별도로 저장하고 HTTP 실패가 있으면 공개 응답 실패를 우선한다.
5. training/literature/retrieval DB 테이블의 read-only 내용 hash를 실행 전·A 후·B 후 비교한다. 원시 DB 행·비밀번호는 저장하지 않는다. config·corpus 파일 hash와 코드 SHA도 다시 확인한다. 변경 시 평가를 중단하고 채점하지 않는다. DB fingerprint와 config hash는 두 설정 간 동일성 확인이며 성능 결과가 아니다.
6. A/B raw와 observer 자료가 모두 존재한 뒤 `RAW_FREEZE.json`에 SHA-256을 기록한다. 완료 표시가 없으면 채점하지 않는다. 모든 파일은 exclusive create이며 중단 후 자동 재실행·재개하지 않는다.
7. 별도 score 명령에서 raw 무결성·순서·관찰 자료를 먼저 확인하고, **그 후에만** Gold SHA 확인/파싱·오프라인 채점을 한다. 실제 서버/DB/LLM 호출은 없다. `SCORES.json`도 덮어쓰지 않는다.

명령은 고정된 평가용 코드 SHA checkout에서 실행한다. 외부 호출이 가능한 실행 단계는 사용자 freeze 자료와 별도 실행 지시를 받은 뒤에만 사용한다.

```text
python scripts/run_phase_a_acceptance_v2.py run --manifest <manifest.json> --manifest-sha256 <SHA-256> --output <새 폴더>
python scripts/run_phase_a_acceptance_v2.py score --manifest <manifest.json> --manifest-sha256 <SHA-256> --output <동일 폴더>
```

인증값은 서버 환경변수로만 제공한다. 실행기는 shell 명령에 인증값을 넣지 않는다. 서버 로그·raw는 로컬 reports에 보관하고 공개 문서로 자동 복사하지 않는다.

## 제한

단일 실행이고 A/B의 Interpreter·검색어 생성·판정 응답은 확률적으로 달라질 수 있다. 따라서 전체 시스템 비교이며 동일한 검색 결과를 재생하는 Grader 단독 비교는 아니다. 고정 순서·cache 영향도 기록한다. 실행 전용 SHA와 동일 corpus/DB/config를 요구하지만 별도 OS 전체 재현 환경을 구축하지는 않는다. Runtime Grader는 Gold를 참조하지 않는다. optional Literature blocking, Graph/Grader/Recovery 의미, 운영 기본값은 바꾸지 않는다.
