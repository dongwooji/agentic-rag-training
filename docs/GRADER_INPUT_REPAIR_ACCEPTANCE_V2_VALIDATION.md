# Grader 입력 수정 및 acceptance v2 준비 검증

## 기준과 고정 SHA

- 시작 기준 master: `035e64886f7e2484ba3cdc80da65c645cd55e079`
- PR #26 merge: `9f7266a7c6e82adca16fa0acc9aa070b10d89477`
- PR #27 merge: `035e64886f7e2484ba3cdc80da65c645cd55e079`
- 작업 branch: `fix/grader-inputs-acceptance-v2`
- 입력 경계 수정 commit: `b6dc0e45b18407f9877d695af8eae299a1f00ed9`
- **향후 평가에 사용할 코드 SHA: `992b495024e2f3a5b2db2abd3890e9a4f356a094`**
- 이 문서는 위 코드 SHA 이후 문서만 추가한다. 해당 SHA에서 src/config/scripts/tests의 파일 hash를 로컬 `reports/grader_inputs_acceptance_v2/implementation_files.json`에 고정했다.
- 평가 조건/채택 기준 protocol SHA-256: `bb2474116d63df5872a5702279e7641675dd079188f0c1befe651a1dd9d8b7a8`

기존 fixed requirements v1의 실제 평가 SHA는 `454177751180e4a88853e1a3e09e40899e6ef0c3`이며 기존 비채택 결정과 결과를 그대로 보존한다. 이번 변경은 그 결과 이후의 버그 수정이다. dev 36입력·held-out은 재실행하지 않았으므로 기존 수치를 수정 후 코드의 성능으로 해석하지 않는다. 과거 11입력이 모두 정상화되었다고 주장하지 않으며, 두 원인 경로의 수정은 새 synthetic으로만 검증했다.

## 수정과 범위

[입력 경계 수정 설명](GRADER_FIXED_REQUIREMENTS_V1_1.md)에 원인을 기록했다. 새 선택값은 `fixed_requirements_v1_1`이며 기존 Grader, 기존 fixed_requirements_v1 코드/prompt/config를 보존했다. 원 질문 보완 입력은 엄격한 기존 생성 계약 확인 후 `original_fallback`으로 연결하고, 출처 문자열은 코드 소유 구절 ID로 해석한다. 중립적 문헌 질문, 고정 요구사항 전체 재검사, 개인 기록과 문헌 요구 분리, membership/finalizer를 유지한다.

공용 변경은 API debug 선택 필드 등록, opt-in 의존성 추가, graph selector/input type 전달뿐이다. 기본 Grader와 interpretation은 legacy 그대로다. Retrieval·embedding·corpus·Gold·Tool·Recovery·Grader 충분성 의미는 변경하지 않았다.

## 검증

| 검증 | 결과 |
|---|---:|
| 새 synthetic/unit/API 경계 테스트 | 34 passed (입력 수정 15 + 실행기/채점기 19) |
| 관련 회귀 테스트 | 77 passed |
| 공개 테스트 | 1,214 passed / 81 deselected |
| 로컬 전체 테스트 | 1,295 passed |
| Python compileall | 통과 |
| Git 변경 공백 검사 | 통과 (`cr-at-eol`: 저장소 `* -text`의 CRLF 바이트 보존) |

최초 synthetic에서 이전 v1의 두 거절 동작을 재현했다. 수정 경로를 추가하기 전 새 경로 테스트는 실패했고, 수정 후 통과했다. 초기 테스트 파일의 Windows 기본 인코딩 읽기 오류 1건은 UTF-8 명시로 수정 후 관련·공개·전체 검증을 다시 수행했다. 일반 sandbox의 TestClient 로컬 통신 대기로 멈춘 작업 프로세스는 종료하고, 로컬 통신이 허용된 실행에서 검증했다. 테스트 범위나 assertion은 약화하지 않았다.

공개 테스트 수에는 기존 미추적 사용자 `tests/test_jats_parser_audit.py`의 19개가 포함된다. GitHub checkout에서는 이 파일이 없으므로 CI 예상 수와 차이가 있다. GitHub Actions의 실제 결과는 PR 최신 head check와 최종 사용자 보고에 기록한다.

실제 OpenAI/LLM, 서비스 REST, DB 조회·쓰기, 검색·embedding 평가 호출은 0회다. API 경계는 TestClient/mock, 실행기는 가짜 전송과 synthetic 자료만 사용했다. GitHub merge 상태 조회와 Git/CI 네트워크 작업은 수행했다. corpus 목록은 기존 papers metadata 파일만 읽었다.

## 보존 및 산출물

- 기존 보호 파일 2,075개 중 공용 수정 3개 외 2,072개 hash 불변
- 기존 frozen 결과·Gold·corpus·벡터·prompt/config 변경 없음
- 미추적 사용자 파일 7개 보존, commit 제외
- 실제 acceptance v2 질문·Gold 작성/열람/실행 없음
- [Gold schema·채점·실행 계약](PHASE_A_ACCEPTANCE_V2.md)
- [정확한 Gold JSON Schema](schemas/phase_a_evidence_gold_v2.schema.json)
- [실행 전 고정 protocol](decisions/phase_a_acceptance_v2_protocol.json)
- [Corpus v1 22편 제목·주제 표](CORPUS_V1_PAPER_SUMMARY_FOR_ACCEPTANCE_V2.md)

Gold는 v1 필드를 그대로 상속하고 `literature_answerability`만 추가했다. 기존 acceptance 지표를 그대로 계산하며 answerable→abstain, unanswerable→answer 수를 추가한다. 채택은 부당 보류 엄격 감소 AND 근거 없는 답변 비증가 AND HTTP 포함 실행 실패 비증가 AND 최종 동작 정확도 비감소다. 결과를 보고 조건을 바꾸지 않는다.

## 아직 수행하지 않은 단계와 한계

실제 acceptance는 사용자가 별도 freeze한 질문/Gold와 실행 지시를 제공한 후 수행한다. 실행기는 고정 코드 SHA checkout, 같은 config/모델/DB/corpus v1, phase_a, debug=true, 단일 턴, 자동 재시도 0을 요구한다. A/B raw 전체 봉인 후에만 Gold를 열고 오프라인 채점한다.

실제 LLM 출력에서 수정 효과나 의미적 정확도 향상을 확인한 것은 아니다. 유효한 출처 ID는 원문 연결만 보장하며 topic 해석의 의미적 정확성을 보증하지 않는다. 필수 문헌이 아닌 optional-only 문항은 not_applicable로 정의했다. 새로운 optional non-blocking 동작은 구현하지 않았다. 실제 서버 기동/DB fingerprint 경로는 이번에는 실행하지 않았으며, runtime 내부 관찰과 평가 실행 순서는 synthetic/mock으로 검증했다.

새 평가 준비는 최종 답변 내용을 자동 사실 채점하지 않는다. A/B 각각의 Interpreter와 검색어 생성이 별도로 실행되므로 Grader 단독 인과 효과가 아닌 전체 시스템 비교이며 확률적 변동과 실행 순서/cache 영향이 남는다. 새 Grader 자동 채택이나 PR merge는 수행하지 않는다.
