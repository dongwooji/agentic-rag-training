# Retrieval v2 Step 2/3: 생성과 번역, 검색 전 확인

## 실행 흐름과 변경 범위

서비스의 첫 Literature Tool 호출 직전 전용 생성기를 실행한다. 입력은 원 질문 하나이며
Router 분류, 평가 분류, case ID, Gold를 넘기지 않는다. 개인 기록 여부를 LLM이 반환하지만
실제 검색어 선택과 검증·보완 처리는 코드가 한다. Router/입력 검증으로 문헌 Tool이
실행되지 않는 요청에서는 생성기도 호출하지 않는다. 각 요청의 초기 호출에만 적용하며 Recovery는 기존 경로다.

| 설정 | Dense 첫 검색어 | BM25 첫 검색어 | 전용 LLM 호출 |
|---|---|---|---|
| H0/H1 | 원 질문 | 원 질문 | 없음 |
| retrieval_step2_generation_v1 | Step 2 실제 검색어 | 같은 검색어 | 생성 1회 |
| retrieval_step3a_translation_v1 | 같은 Step 2 실제 검색어 | 영어 번역 또는 Step 2 보완 검색어 | 생성 1회 + 번역 1회 |
| retrieval_step3b_translation_v1 | 같은 영어 번역 또는 Step 2 보완 검색어 | 같은 검색어 | 같은 2단계; 평가에서 추가 호출 없음 |

H1과 새 설정의 차이는 config_version과 query_mode뿐이다. BM25 positive_only,
Dense model/revision/embedding, corpus, parent chunk, tokenizer, 후보 깊이, RRF,
Recovery, evidence fusion은 유지한다. 서비스 기본값은 legacy/H0이며 V2 확정 후 전환한다.
Phase A 검증과 prompt는 수정하지 않고 initial query 연결만 제거했다.
metadata, 약어·숫자 정규화, child, 후보 깊이 변경은 이번 범위에서 제외한다.

## Schema와 검증

생성 schema는 has_personal_context(bool), literature_query(str)이다. bool 강제 형변환과
추가 필드를 허용하지 않는다. false이면 생성문을 무시하고 원 질문을 사용한다.
Step 2에서는 원 질문의 영어/한국어 용어 표기를 그대로 유지하며 용어를 번역하지 않는다. 언어 변경은 Step 3에서만 한다.
true일 때 빈 문자열, 날짜·범위, N-session/N세션/N회 세션, catalog 장비 괄호 표기,
plateau candidate/정체 후보/training gap/개인 e1RM 집계 표현을 검사한다.
개인 중량 값은 원문의 개인 기록 표현이 있는 구절에서 weight/e1RM/중량/체중 값과
kg/lb 값을 추출해 생성문에 남았는지 검사한다. 숫자만으로 개인 값이라고 판정하지 않는다.
연구 조건의 10분 준비운동, 주 2회 훈련, 8~12회 반복은 허용한다.
완결된 질문의 설명 예시는 '고령자의 근감소 예방에 효과적인 저항운동 방법은 무엇인가?'다.
이 예시는 사용자가 제안한 것으로, 평가 데이터 유래 예시를 제거하기 위해 교체했다.

이것은 일반 문자열 패턴 검사이며 의미 판정기를 추가하지 않았다. 이름·날짜·단위의
모든 자연어 표현을 판별할 수 없고, 한 문장 안 연구 조건과 개인 기록 조건이 혼재하면
보수적으로 원문을 사용할 수 있다. 남은 개인 문맥과 주제 소실은 검색 전 대응표 사람 검토로 확인한다.

번역 schema는 literature_query(str)만이다. has_personal_context는 허용하지 않는다.
입력은 동결된 Step 2 실제 검색어이고 내용 재생성·재분리를 하지 않는다.
숫자 표기별 개수, 숫자와 같은 단위의 연결(초와 분은 다른 단위), 약어의 대소문자와 개수를 검사한다.
번역 실패는 원 질문이 아닌 Step 2 실제 검색어로 복귀한다. 단위의 언어별 동의어만 검증용으로
대응시키며 검색 문자열을 정규화하지 않는다. 단위 환산은 허용하지 않는다.
시간·중량·길이·백분율·세션/횟수/세트와 m/s, km/h 등 복합 단위의 공통 표기를 검사한다.
목록 밖 영문 단위 식별자는 원 표기와 같아야 하며, 의미를 확정할 수 없는 단위 번역은 보수적으로 원 검색어를 사용한다.

생성 상태는 query_source(original/generator), isolation_status(isolated/original_already_suitable/validation_failed),
validation_status, fallback, fallback_reasons로 저장한다. 번역은 query_source(step2/translation)와
자체 검증·보완 상태를 저장한다. LLM 제공자 실패와 검증 실패를 검색 실패나 근거 부족으로 표시하지 않는다.

## 검색 없는 준비와 승인

첫 중간 확인은 prompt 전문과 검증 규칙이다. 지금은 코드/mock 테스트만 작성하며 실제 호출은 하지 않는다.
승인 후 승인 근거와 정확한 source_hashes를 갖는 새 승인 기록을 만든다.
actor=user, decision=approved, evidence_ref=실제 사용자 승인 메시지,
sources=src.evaluation.literature_query_freeze.source_hashes(ROOT) 형태다.
사용자 승인 전에 이 기록을 만들거나 승인 상태를 미리 채우지 않는다.

신규 준비에는 OPENAI_API_KEY만 필요하며 PostgreSQL을 조회하지 않는다.
서비스 전체 실행에는 기존과 같이 DB 비밀번호가 필요하지만 query 준비에는 PGPASSWORD가 필요 없다.
.env.local.json은 아래의 --secrets-json 옵션으로만 읽으며 값·파일 내용은 출력하거나 저장하지 않는다.
모델은 기존 Phase A의 고정 모델 설정을 새 literature_query_model_v1.json으로 복사했고
가격은 기존 설정 스냅샷에 따른 추정 비용이다. 실패 호출의 사용량이 없으면 실제 비용을 0으로 단정하지 않고
usage_unavailable_calls를 별도로 보고한다.

사용자 prompt 승인 후 실행할 두 명령(아직 실행하지 않음):

```powershell
python scripts/prepare_literature_queries.py --stage generation --input data/evaluation/retrieval_step2_queries_v1 --output data/evaluation/retrieval_step2_generation_queries_v1 --prompt-approval reports/experiments/retrieval_v2_step23_prompt_approval_v1.json --secrets-json .env.local.json
python scripts/prepare_literature_queries.py --stage translation --input data/evaluation/retrieval_step2_generation_queries_v1 --output data/evaluation/retrieval_step3_translation_queries_v1 --prompt-approval reports/experiments/retrieval_v2_step23_prompt_approval_v1.json --secrets-json .env.local.json
```

첫 명령은 기존 v1 동결본의 원 질문과 표시 정보만 읽는다. Phase A 생성문·판정을 재사용하지 않는다.
18 dev + 18 dev-ko를 한 번씩 생성해 동결한 뒤 두 번째 명령이 그 실제 사용 검색어를 번역한다.
각 단계는 기존 출력 경로를 덮어쓰지 않으며, 중단된 경로를 재실행하지 않는다. 완료 행은 즉시 보존한다.
소스·prompt·config 해시는 호출 전마다 확인해 평가가 끝날 때까지 규칙 변경을 차단한다.

각 동결본은 원문/생성문/실제 검색어/검증·보완 사유/모델/response ID/prompt·config 해시/decoding 설정을 저장한다.
단계별 호출·실패·토큰·추정 비용·지연 합계와 중앙값/p95를 dev/dev-ko 따로 기록한다.
영어 단계 대응표에는 한국어 생성문과 실제 사용문도 포함한다.
두 manifest SHA-256, 전체 대응표, 단계별 사용량을 보고하고 검색 전에 멈춘다.

## 대응표 승인 뒤 평가

Dense/BM25/Hybrid/RRF, 지표 계산, H1 비교는 사용자 대응표 승인 뒤 별도 단계에서 실행한다.
기존 run_retrieval_v2_preparation.py는 모든 비원문 query 모드를 검색 전 차단한다.
load_reviewed_conditions는 양쪽 manifest 해시와 사용자 승인 근거, 모든 산출물 해시,
한국어 동결본 → 번역 입력의 정확한 연결을 확인한다. 여기에는 LLM 호출 코드가 없다.

비교 순서는 H1 → Step 2 → Step 3a → Step 3b다. 각 비교에 명세의 채택 기준을 따로 적용하고,
앞 단계 탈락 시 다음 조건은 직전 채택 설정과 직접 비교한다. 주 조건을 사후에 고르지 않는다.
dev/dev-ko 지표와 문항별 좋아짐/나빠짐/동일, 표시용 Hybrid 8/문헌 전용 10 집계를 각각 보고한다.
문헌 전용 여부에 따른 임의 query 선택은 없으며 has_personal_context=false이면 원문을 유지한다.
평가 중 재생성·재번역하지 않는다. 별도 검증용 평가 질문·Gold는 열람하거나 실행하지 않는다.

## Git 및 PR 기록

branch는 feature/retrieval-question-isolation을 이어 쓴다. 현재 upstream이 origin/master이므로
첫 push는 반드시 git push -u origin feature/retrieval-question-isolation으로 한다.
관련 파일만 지정해 추가하며 사용자 변경과 AGENTS.md, corpus v2 설계 초안,
JATS 감사 스크립트·테스트, discover_literature_corpus_v2.py, audit_table_evidence_utility.py는 제외한다.
PR 설명에는 --secrets-json과 dependencies.py의 AGENTIC_RAG_RETRIEVAL_CONFIG 연결을 명시한다.
현재 승인 단계에서는 commit/push/PR, 실제 생성·번역과 검색을 실행하지 않는다.

## 대응표 승인 후 실행 단계 (2026-10-10)

위 중간 중단 단계는 완료됐으며 사용자가 대응표·번역 검증 수정·검색 평가·PR을 승인했다.
기존 생성 동결본과 번역 v1은 보존하고, 번역 v2는 기존 모델 출력을 오프라인 재검증한 것이다.
LLM 호출을 반복하지 않으며 생성 실패 7건은 원 질문으로 평가에 포함한다.
날짜 뒤 일반 단어를 단위로 보던 오탐을 일반 규칙으로 수정했으며,
숫자와 단위·약어 변경 검사는 계속 적용한다. 자세한 결정과 한계는 `RETRIEVAL_V2_STEP23_DECISION.md`에 기록했다.

```powershell
python scripts/revalidate_literature_translation.py --input data/evaluation/retrieval_step3_translation_queries_v1 --output data/evaluation/retrieval_step3_translation_queries_v2
python scripts/run_retrieval_query_evaluation.py --generation data/evaluation/retrieval_step2_generation_queries_v1 --translation data/evaluation/retrieval_step3_translation_queries_v2 --approval reports/experiments/retrieval_v2_step23_query_revalidation_v2/approval.json --output reports/experiments/retrieval_v2_step23_evaluation_v1 --h1-reference reports/experiments/retrieval_v2_step1_v1
```

출력 경로는 존재하지 않는 새 version이어야 한다. 이 명령은 완료된 경로에 재실행하지 않는다.
승인 기록은 사용자 채팅의 승인 근거와 생성·번역 manifest 해시를 연결하며 임의 승인 값을 만들지 않는다.
새 평가 실행기는 승인·해시·번역 입력 연결을 먼저 확인하고, 모든 검색 순위를 저장한 뒤 Gold로 지표를 계산한다.
H1 순위·지표 재현과 Step 2 문헌 전용 문항 불변을 확인한다. 기존 준비 실행기의 검색 차단은 유지한다.
결과는 개발용 진단이며 독립 일반화 성능이 아니다. 실제 PostgreSQL 경로는 이번 평가에서 실행하지 않는다.
서비스 기본값은 legacy/H0이고 V2 설정 확정 시 전환한다. held-out 질문·Gold는 열람하거나 실행하지 않는다.

완료 결과는 `RETRIEVAL_V2_STEP23_RESULTS.md`와 `reports/experiments/retrieval_v2_step23_evaluation_v2/`에 있다.
첫 실행의 보고 집계 오류는 저장된 순위를 `--rankings-run reports/experiments/retrieval_v2_step23_evaluation_v1`
옵션으로 새 출력 version에 불러와 해결했다. 검색어와 채택 기준은 바꾸지 않았으며 추가 검색·LLM 호출은 없다.
