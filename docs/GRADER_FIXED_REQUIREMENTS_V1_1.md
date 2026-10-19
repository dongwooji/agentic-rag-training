# Fixed requirements v1.1 입력 경계 수정

기준 master: `035e64886f7e2484ba3cdc80da65c645cd55e079`. PR #26/#27은 각각 `9f7266a7c6e82adca16fa0acc9aa070b10d89477`, `035e64886f7e2484ba3cdc80da65c645cd55e079`로 merge됨을 먼저 확인했다.

이 변경은 기존 v1 dev 판정 이후의 버그 수정이다. 기존 `grader_fixed_requirements_v1` 결과/결정/코드/prompt/config는 보존한다. dev 36입력이나 held-out을 재실행하지 않았으며, 기존 수치를 수정 버전의 성능 근거로 사용하지 않는다.

## 원인과 수정

1. 기존 Grader는 Step 2의 `validation_status=passed` 또는 검증된 Phase A 문헌 질문만 받았다. 하지만 검색어 생성 계약에서 검증 실패는 **원 질문을 실제 검색어로 쓰는 정상 보완 경로**다. 이전 검색 재생 평가는 검증된 Phase A 문헌 질문이 없어서 HYB 7입력이 Grader 호출 전 input_failure로 끝났다.
   - v1.1은 검증된 Step 2 → 검증된 Phase A → 검증 실패 후 코드가 선택한 원 질문 순으로 받는다.
   - 보완 경로는 `fallback=true`, failed, query_source=original, input_query=actual_query=original_question을 모두 확인한다. 거절된 generated_query를 통과시키거나 validation_status를 passed로 바꾸지 않는다.
   - `question_source=original_fallback`으로 출처를 보존한다. 원 질문이 개인 맥락을 포함함을 provider에 알리고 동일한 문헌/기록 분리 계약으로 첫 Grader 호출에서 문헌 요구만 만든다. 추가 LLM 호출은 없다.
2. 기존 최초 응답은 LLM이 `source_span` 문자열을 원문 그대로 복사해야 했다. 의역이 출력된 HYB 4입력은 schema_failure였다.
   - 코드가 질문의 손실 없는 구절 목록 `L1..Ln`, 원 질문 구절 목록 `Q1..Qn`을 제공한다. LLM은 자유형 출처 문자열 대신 `source_id`, `direction_source_id`를 출력한다.
   - 코드는 목록에 있는 ID만 원문 구절로 변환한다. 알 수 없는 ID·open인데 방향 ID 존재·방향 요청인데 원문 ID 없음은 여전히 실패한다. source text를 유사 매칭하거나 grounding 검증을 생략하지 않는다.
   - 요구사항 topic은 기존과 같이 모델의 중립적 해석이다. ID의 존재 검증이 topic의 의미적 정확성까지 보증하지는 않는다. 문헌/개인 요구 분리도 의미 판단을 포함한다는 한계를 유지한다.
3. 공개 DebugTrace는 `extra=forbid`인데 기존 trace builder의 `grader_requirements_v1` 필드가 schema에 없었다. 새 Grader를 debug=true로 호출하면 HTTP serialization 실패 가능성이 있었다. 선택 필드 하나만 등록하고 기존 allowlist projection을 그대로 사용했다. debug=false 응답은 기존 형태다.

## 보존한 의미

최초 요구사항 hash 고정, 현재 결합 Top-10으로 모든 고정 항목 재평가, 중립적 질문 형식, 개인 기록/문헌 요구 분리, chunk membership, deterministic finalizer, bounded Recovery/근거 결합은 동일하다. v1의 exact-span 검증도 이전 코드에 그대로 남는다. 새 source-reference 입출력 계약을 위해 v1.1 prompt/config만 별도 파일로 추가했으며 의미 정책이나 사례 예시는 추가하지 않았다.

선택값 `AGENTIC_RAG_GRADER_VERSION=fixed_requirements_v1_1`은 명시적 실행용이다. 기본값은 계속 legacy Grader이며 기존 fixed_requirements_v1도 그대로 선택할 수 있다. 서비스 interpretation 기본값도 legacy다.

새 버전의 고정 요구사항 내부 version은 `runtime_evidence_grader_fixed_requirements_v1_1`. 기존 debug block 이름 `grader_requirements_v1`은 유지하고 내부 version으로 구분한다. source_span/direction_span은 LLM의 복사 문자열이 아닌 코드가 해석한 원문 구절이다.

공유 graph의 변경은 Grader가 제공한 selector/input type을 선택하는 두 줄뿐이다. 기존 Grader는 기존 selector와 input type을 계속 사용한다. 노드·edge·종료 정책은 변경하지 않았다.

## 검증 범위

새 synthetic 테스트에서 이전 실패의 원인 두 가지를 재현하고, 실제 검색어 생성 함수의 보완 metadata → graph Grader 연결, 출처 ID 해석, 알 수 없는 출처 거절, 고정 요구사항 재검사·누락/중복/다른 ID 거절, chunk membership, provider wire schema, debug REST serialization을 확인한다. API 검증은 TestClient/mock이며 실제 OpenAI/DB/서비스 REST 호출이 아니다.

acceptance v2 질문/Gold는 작성하거나 읽지 않았다. 실행기·채점기 역시 synthetic 자료만 사용한다. 성능 판정은 향후 사용자가 freeze한 acceptance v2로 수행한다.
