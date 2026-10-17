# 고정 문헌 요구사항 Grader v1: 구현 전 계약·평가 동결

승인일: 2026-10-17 (Asia/Seoul). 기준 master: `e7a61b58526b0e2cc048091d7eed2b13f5a38aee`.
계약 버전: `runtime_evidence_grader_fixed_requirements_v1`.
이 문서와 `grader_fixed_requirements_v1_protocol.json`을 구현·신규 Grader 평가 전에 SHA-256으로 동결한다.

## 실행 계약

요구사항 생성 전용 LLM 호출을 추가하지 않는다. 검증된 기존 Step 2 `actual_query`를 문헌 질문으로 우선 사용한다.
Step 2 검증 실패 시 검증된 Phase A 문헌 질문을 사용할 수 있다. 둘 다 없으면 임의로 질문을 만들지 않고 input failure로 기록한다.
평가에서는 이미 동결된 Step 2 질문과 그 검증 정보를 사용하고, 기존/신규 Grader에 같은 원문·문헌 질문·최초 Top-10을 제공한다.
검색어·Step 2/3·Interpreter prompt는 수정하거나 다시 생성하지 않는다. Gold·case 분류는 실행 입력에 넣지 않는다.

초기 Grader 호출 1회에서 문헌 질문을 최소 1개~최대 8개의 독립적으로 필요한 질문으로 분해하고 첫 근거 판정도 수행한다.
각 항목은 `component_id`(연속 C1..Cn), `topic`(문헌 질문의 주제/비교 조건), `direction`(open/supporting/opposing),
`source_span`(문헌 질문의 실제 부분 문자열), `direction_span`(방향 명시 요청의 원문 부분 문자열 또는 null)을 가진다.
코드는 topic과 direction으로 requirement를 질문형으로 렌더링한다:

- open: "{topic}에 대해 문헌은 무엇을 말하는가?"
- supporting: "사용자가 명시적으로 요청한 {topic}의 지지 근거는 무엇인가?"
- opposing: "사용자가 명시적으로 요청한 {topic}의 반대 근거는 무엇인가?"

open 질문을 특정 결론을 증명하는 문제로 바꾸지 않는다. 부정·조건부·적용 범위가 제한된 답도 직접 근거가 있으면 supported다.
근거에 언급이 없다는 것 자체는 부정적 결론의 근거가 아니다. 개인의 관측·Metric 값을 문헌으로 증명하지 않는다.
개인 기록 조회 기간과 연구의 기간·대상·운동·비교 조건을 구분하며 후자는 보존한다. 자연어 키워드별 임의 삭제 규칙은 만들지 않는다.
source_span membership, 방향 표시와 direction_span 존재·원문 membership은 코드가 확인한다. 의미적 완전성 자체를 코드가 보장한다고 주장하지 않는다.

초기 요구사항은 검증 후 canonical JSON SHA-256과 함께 고정한다. 재검사는 고정 항목을 전부 입력받으며,
출력은 component_id/status/supporting_chunk_ids만 허용한다. ID 누락·추가·중복·순서 변경은 schema failure다.
요구사항 문구를 재검사 출력에서 다시 받지 않는다. 이전 supported 상태를 재사용하지 않고 현재 결합 Top-10으로 매번 전부 재평가한다.
최종 충분성은 기존 membership 검증과 all-required deterministic finalizer를 재사용한다.

초기 분해와 판정이 같은 호출이므로 최초 분해가 검색 근거로부터 완전히 독립적이지 않다는 한계는 유지한다.
LLM schema/provider 오류와 semantic missing을 구분한다. 오류를 supported로 바꾸거나 자동 재호출하지 않는다.

기존 Grader 코드·prompt·config는 변경하지 않는다. 새 버전은 명시적 선택에만 연결하고 서비스 기본은 기존 Grader/legacy다.
graph node/edge/재시도 상한/Recovery prompt/근거 결합/Tool 계산/optional Literature blocking은 변경하지 않는다.
공유 graph 변경은 신규 Grader opt-in 입력·고정 상태의 전달과 debug trace 추가로 제한한다.
trace는 신규 버전일 때만 `grader_requirements_v1` 블록(source/query/hash/components)을 추가한다.
debug=false 및 기존 Grader 응답 형태는 유지하고 원시 예외·secret·provider detail은 공개하지 않는다.

## 구현과 평가 분리

구현 중에는 신규 synthetic/unit/mock 입력만 사용한다. 신규 Grader로 dev 입력을 실행하거나 결과를 미리 보지 않는다.
구현 완료 후 코드 SHA·source hash·테스트 결과를 별도 implementation seal에 고정한다.
공개/로컬 전체 회귀는 완료된 구현을 검증하며 기존 테스트/기존 버전만의 frozen 입력 사용을 신규 Grader dev 판정 실행과 혼동하지 않는다.
신규 Grader의 dev 최초 LLM 실행은 단 한 번의 판정 실행이다. 시작 뒤 코드/prompt/config/Gold/기준을 수정하지 않는다.
실패한 요청도 재실행하지 않으며 시작 journal과 결과를 보존한다. 최종 답변 생성과 Router/Interpreter/Step 2/3 실행은 평가하지 않는다.

## dev 판정 프로토콜

dev 18 + dev-ko 18 = 36입력. dev-ko는 같은 원본 18개 Gold를 연결하며 독립 36문항이라고 부르지 않는다.
corpus v1과 pinned MiniLM128/Final V2의 저장된 최초 Hybrid Top-10을 두 Grader에 동일하게 제공한다.
Dense/BM25 depth=10, BM25 positive_only, equal RRF k=60, parent, 최종 Top-10, reranker/child 없음.
초기 검색은 저장 ranking 재생이며 신규 검색/쿼리 생성 없이 시작한다. Recovery 검색만 같은 retriever/config/기존 벡터로 실행한다.
이 평가의 지연은 초기 검색 재생을 제외한 Grader/Recovery 처리 시간이며 실제 REST E2E 지연이라고 부르지 않는다.

1. 기존 Grader 각 입력 1회와 신규 Grader 초기 판정 각 입력 1회: 같은 원문·동결 Step 2 문헌 질문·초기 근거.
2. 신규 초기 판정과 요구사항을 공유하여 off는 종료, on은 기존 Recovery(max_retry=2)·검색·fusion·재판정 수행.
3. Gold는 실행 종료 후 scorer만 읽는다. CE/EGR은 required group의 any/all과 Top-10을 기존 지표 그대로 사용한다.
4. dev/dev-ko 및 합산을 보고하되 판정은 합산 36입력만 사용한다. 각 요청을 같은 가중치로 센다.

Grader-Gold 불일치 A = CE=1인데 정상 완료 판정이 insufficient인 입력 수.
불일치 B = CE=0인데 정상 완료 판정이 sufficient인 입력 수.
실행 실패는 별도 계수하며 분모에서 삭제하거나 insufficient로 바꾸지 않는다. 이것은 Gold 일치도이지 의미적 정답 판정의 완전한 대체가 아니다.
채택: 신규 A+B < 기존 A+B AND 신규 A<=기존 A AND 신규 B<=기존 B AND 신규 실행 실패<=기존 실행 실패.
조건 미충족 시 비채택. 불일치가 양쪽 모두 0으로 같아도 strict decrease 미충족이다. 이후 기준 변경 없음.

Recovery 채택은 신규 Grader 채택과 별도로 기록한다:
최종 CE@10(on)>=최종 CE@10(off) AND 실행 실패 요청 수(on)<=실행 실패 요청 수(off).
이 중 하나라도 위반하면 Recovery 채택 보류. 서비스 설정은 판정과 무관하게 자동 변경하지 않는다.
최종 EGR/CE, 문항별 변화, 호출·token·비용·Grader/검색/전체 처리 지연·중단 사유를 저장한다.

최종 동작은 마지막 Grader가 정상 충분이면 answer_ready, 정상 부족이면 abstain_ready, 판정 자체 실패면 execution_failure다.
Recovery/검색의 실제 실행 오류는 별도 execution_failure 플래그와 단계/사유로 기록하여 채택 기준에서 센다.
duplicate_query/budget_exhausted는 기존 bounded 종료로 처리하며 추가 검색/Grader 호출을 하지 않는다.
마지막 충분/부족 판정과 이후 실행 오류를 별도 필드로 보존한다. 최종 답변 생성 LLM은 호출하지 않는다.

## 판정 후 held-out 진단

dev decision을 코드/결과 hash와 함께 먼저 write-once 저장한 뒤에만 held-out을 로드·실행한다.
기존 frozen corpus v2, MiniLM128, 기존 Final V2 query/ranking/Gold의 evidence-bearing 20개를 사용한다.
선택된 Grader(비채택이면 기존 Grader)와 판정된 Recovery 조건으로 각 입력 1회만 실행한다.
기존 Grader가 선택되면 기존 Recovery on 조건을 유지하며 신규 Recovery 채택 주장을 하지 않는다.
corpus v2는 이 진단의 기존 조건이며 운영 corpus 교체가 아니다. dev와 합산하지 않는다.
diagnostic only, 독립 검증 아님. 결과를 보고 설정·결정·Gold·기준을 변경하거나 재실행하지 않는다.

## 호출 예산·보존·보고

dev 초기 72회(기존36+신규36), Recovery/재판정 최대144회. held-out 초기20회+Recovery/재판정 최대80회.
총 Grader/Recovery 예산은 92~316회이며 질문 생성·번역·최종 답변 생성은 0회다. 동일 retry 경로에서 요구 생성 전용 추가 호출 0회.
모델·temperature·reasoning은 기존 값 유지. 신규 구조의 출력 공간은 max_output_tokens=1800으로 사전 고정한다.
저장 가격 snapshot 기준 예상 USD 1~3, 여유 예산 USD 3~5. 실제 usage/추정 비용/측정 지연을 보고하며 비용 절감이나 성능 향상을 미리 주장하지 않는다.
새 산출물은 `reports/grader_fixed_requirements_v1/`, 공개 계약·요약·결과는 새 docs 경로에 저장한다.
기존 frozen/보호 파일 hash, 실제 호출 여부, 기준 SHA/평가 SHA/최종 head, 공개/전체 테스트·CI를 구분하여 보고한다.
별도 branch와 PR로 제출하며 merge하지 않는다.
