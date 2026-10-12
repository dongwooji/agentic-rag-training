# Retrieval v2 실험 계획

## 목적

문헌 검색(Literature Tool)의 확인된 문제를 하나씩 고치고, 각 변경의 효과를 dev 평가로 측정한다.
설정을 확정한 뒤에만 별도 검증용 평가(held-out v2)를 한 번 실행해 최종 성능을 보고한다.

해결 대상(근거: Retrieval v1 진단 보고서):

- BM25 zero-match: 일치하는 단어가 없어도 점수 0인 chunk가 Top-K에 들어가 RRF에 반영됨
- 질문 오염: Hybrid 질문에서 개인 기록 문맥까지 문헌 검색어에 들어감
- 언어 불일치: 한국어 검색어가 영어 논문 코퍼스에 그대로 들어가 BM25가 거의 동작하지 않음
- Dense 잘림: parent chunk 대부분이 MiniLM 입력 한도(128토큰)를 넘어 앞부분만 임베딩됨
- 후보 깊이: Dense 10 + BM25 10 밖에 필요한 근거가 있는 문항이 많음

## 범위

포함:

- 문헌 검색 단계의 검색어 생성, 검색 단위, 후보 수, 결과 결합 방식
- 같은 MiniLM의 parent 입력 길이 256/512 실험(별도 embedding version, 기존 128 보존)

제외:

- 임베딩 모델 교체
- 코퍼스 내용 변경(논문 선택, parser, parent chunking 정책)
- 표·그림 추출
- Router, Runtime Grader, Recovery, 최종 답변 생성 변경
- E2E 평가 (Retrieval v2 이후 별도 단계)

## 평가 데이터와 코퍼스

| 용도 | 평가셋 | 코퍼스 | 사용 규칙 |
|---|---|---|---|
| 실험·설정 선택 | 기존 dev/diagnostic 평가셋 (문헌 포함 문항) | corpus v1 (frozen) | 반복 사용 가능. 일반화 성능이라고 표현하지 않음 |
| 한국어 검색 측정 | dev-ko (아래 참고) | corpus v1 (frozen) | dev와 동일하게 취급 |
| 최종 평가 | held-out v2 (24문항) | corpus v2 build freeze v1 | **설정 확정 전에는 실행·열람 금지. 설정별 1회만 실행** |

dev 실험은 corpus v1에서 하고, held-out만 corpus v2에서 실행한다.
이번에 바꾸는 항목은 특정 코퍼스에 묶이지 않는 검색 방식이므로 dev에서 정한 설정을 v2에 그대로 적용한다.
보고서에는 "설정은 corpus v1 dev에서 선택, 최종 평가는 corpus v2 held-out"이라고 명시한다.

### dev-ko 만들기

dev 문항 대부분에 영어 전문용어가 섞여 있어 순수 한국어 검색 성능을 측정할 수 없다.
그래서 dev 문항을 순수 한국어로 바꿔 쓴 별도 dev 셋을 만든다.

- 의미는 그대로 두고 영어 용어만 한국어로 바꾼다. 예: "progressive overload" → "점진적 과부하"
- RIR, RPE, 1RM, e1RM 같은 약어는 영어 그대로 유지한다. 그 외 용어는 한국 운동 커뮤니티에서
  흔히 쓰는 표현을 우선하고, 한글 음차(예: 디로딩)도 허용한다.
- 의미가 같으므로 원 문항의 Gold를 그대로 연결한다. 새 Gold를 만들지 않는다.
- held-out 문항은 참고하지 않는다.
- 바꿔 쓴 규칙과 원문↔한국어 대응표를 함께 저장하고 version을 붙여 freeze한다.
- 기존 dev 평가셋 파일은 수정하지 않는다.
- 변환된 18문항은 동결 전에 사용자에게 보여주고, 확인을 받은 뒤 동결한다.

## 보존할 것 (수정 금지)

- corpus v1, corpus v2 build freeze v1
- 기존 dev 평가셋과 Gold, held-out v2 평가셋과 Gold
- 기존 Dense / BM25 / Hybrid baseline 결과
- Retrieval v1 진단 보고서

새 결과는 새 경로와 새 version으로 저장한다.

최종 held-out Gold: `heldout_v2_v1_1_final`, `gold_evidence.json` SHA-256
`7c491a55e96b70b26b10ce7625c535e1dc521b4d83ab9d4e984d5d2c84df4b42`.
AI 대조 18/18 및 사람 검토 완료 후 현 freeze를 유지한다. 경미한 표현 지적 5건은 차기 version 후보로만
기록한다. 상세 결정: [최종 결정](../decisions/RETRIEVAL_V2_FINAL.md).

## 비교 설정

held-out에서 비교할 설정은 다음 네 가지다. Step 2/3 결과 확인 후 기준선 변경은
`docs/decisions/RETRIEVAL_V2_STEP45.md`의 사후 결정으로 명시한다. held-out 실행 전 설정을 확정한다.

| 설정 | 내용 | 의미 |
|---|---|---|
| H0 | 기존 v1 검색 방식 그대로 | 출발점 |
| H1 | H0 + BM25 zero-match 수정 | 버그 수정만의 효과 |
| Step 2 기반 | 과거 사전 기준에 따라 채택된 한국어 검색어 경로 | 역사적 사전 기준 경로 |
| V2 | Step 3b 기반 + 이후 채택 변경 | 사후 결정에서 출발한 최종 경로 |

## 지표

- EvidenceGroupRecall@10
- CompleteEvidence@10
- MRR
- boundary / abstention 문항은 검색 지표와 분리해서 따로 집계
- 문항별 변화(좋아짐 / 나빠짐 / 동일)를 평균과 함께 기록
- dev와 dev-ko를 따로 집계

## 채택 기준

각 실험 결과를 보기 전에 정한다. 아래 초기 dev 중심 기준은 Step 2/3까지의 역사적 기준이며,
Step 4 이후에는 아래 합산 기준과 개별 사전 결정 기록을 적용했다. 문서 정정이며 기존 결과·판정은 변경하지 않는다.

- dev의 CompleteEvidence@10이 직전 단계보다 나빠지지 않는다.
- dev 문항별로 좋아진 수가 나빠진 수보다 많다.
- dev-ko에서 크게 나빠지지 않는다.

충족하지 못하면 그 변경은 빼고 다음 단계로 넘어간다. 결과는 채택 여부와 관계없이 기록한다.

Step 2/3에서는 위 기준을 검색 전에 다음과 같이 구체화한다. 각 조건에 별도로 적용하며,
앞 조건이 탈락하면 직전 채택 설정을 비교 기준으로 쓴다.

- dev CompleteEvidence@10은 비감소.
- dev 문항별 EGR@10의 좋아짐 수가 나빠짐 수보다 많음.
- dev-ko EGR@10, CompleteEvidence@10, MRR은 모두 비감소.
- 부동소수점 동일 판정은 기존 평가 구현의 허용 오차를 사용함.

Step 4·5 및 후속 보정·길이·reranker 비교는 결과를 보기 전에 다음 합산 기준으로 고정했다.

- 주 기준: dev+dev-ko 합산 36문항 CompleteEvidence@10 비감소.
- 합산 문항별 EGR@10 개선 수 > 악화 수.
- dev/dev-ko 각각은 보고만 하며 별도 채택 조건을 두지 않는다.
- 이후 누적 기준선 Step 3b는 결과 확인 후 사후 결정이다. 과거 사전 기준 결과는 소급 변경하지 않는다.
- 자세한 순서·초과 문장 처리·후보 부족 처리: `docs/decisions/RETRIEVAL_V2_STEP45.md`.
- 보정·길이 실험은 세 조건 각각 Step 3b 독립 비교와 사전 tie-break를 적용했다.
  reranker는 동일 성능 기준에 CPU 평균 ≤3초를 추가했다. 각 개별 결정 기록이 상세 기준이다.

## 단계

실험은 누적 방식이다. 각 단계는 바로 앞 단계의 채택된 설정 위에서 변수 하나만 바꾼다.
Step 4·5는 사용자 승인에 따라 하나의 branch·PR에서 순서대로 누적 진행한다.
다음 단계는 사람이 확인한 뒤 시작한다.

### 0. 준비

- H0를 corpus v1 dev에서 다시 실행해 기존 baseline 결과와 일치하는지 확인한다
  (Top-10 chunk ID와 순서, 지표. 점수는 부동소수점 오차 허용).
- 검색 설정을 version이 붙은 설정 파일로 관리할 수 있게 정리한다(versioned retrieval config).
  리팩터링 후에도 H0 결과가 그대로여야 한다.
- dev-ko를 만들고 freeze한다. H0로 dev-ko 기준값을 측정한다.

### 1. BM25 zero-match 수정 (H1)

- 변경: BM25 점수가 0 이하인 후보는 결과에서 제외한다. 일치 후보가 없으면 BM25 결과는 빈 목록이다.
- RRF는 BM25 결과가 비어 있어도 Dense 결과만으로 동작해야 한다.
- 버그 수정이므로 채택 기준과 관계없이 적용한다. 효과는 기록한다.

### 2. 문헌 검색어 생성 (literature query generation)

- 변경: 첫 Literature Tool 검색 직전 전용 LLM을 한 번 호출한다. 입력은 원 질문이며 Router 분류와 무관하다.
- 출력: `has_personal_context`(bool), `literature_query`(str). false이면 생성문을 무시하고 원 질문을 쓴다.
- true이면 원 질문의 용어 표기(영어/한국어)를 유지한 독립된 문헌 질문을 만든다. 용어 번역은 하지 않는다. 날짜·범위, 세션 조건, 개인 중량·e1RM 값,
  canonical catalog 형식 운동명, plateau candidate 등 기록 판정을 검사한다. 실패하면 원 질문과 사유를 기록한다.
- 연구 조건 숫자(10분 준비운동, 주 2회 훈련, 8~12회 반복)는 허용한다.
  일반 패턴만 사용하며 잔존 문맥·주제 소실은 검색 전 사람 검토로 확인한다.
- Phase A 출력 계약은 이번에 고치지 않는다. Phase A → initial query 연결을 제거하고 서비스 기본값은 legacy/H0를 유지한다.
- dev/dev-ko 36문항을 한 번씩 생성해 새 버전으로 동결한다. 기존 v1 Phase A 동결본·진단·H0/H1은 보존한다.
- Dense와 BM25 모두 Step 2 실제 검색어를 사용해 H1과 비교한다. 첫 검색어 외 BM25, Dense, corpus,
  parent, tokenizer, 후보 수, RRF, Recovery와 근거 결합 설정은 그대로다.
- 구현 후 prompt 전문과 검증 규칙을 보여주고 멈춘다. 승인 후 생성·번역 동결 및 대응표 확인에서 다시 멈춘다.
  대응표 승인 전 Dense/BM25/Hybrid/RRF 검색, 검색 지표 계산과 성능 비교를 실행하지 않는다.
- 실행·중간 확인 안내: `docs/RETRIEVAL_V2_STEP2.md`.

### 3. 동결된 Step 2 검색어 영어 번역

- Step 2 실제 사용 검색어를 동결한 뒤 영어로 번역한다. 독립 생성이나 개인 문맥 재판정을 하지 않는다.
  Step 2가 원 질문을 사용했다면 그 원 질문 전체를 번역한다.
- 출력은 `literature_query`(str) 하나다. 숫자·단위·RIR/RPE/1RM/e1RM 보존을 검사한다.
  실패하면 Step 2 실제 검색어를 사용하고 사유를 기록한다.
- 호출 순서: 원 질문 → 한국어 생성 36회 → 동결 → 실제 한국어 검색어 번역 36회 → 별도 동결.
  평가 중 LLM을 다시 호출하지 않는다. 모델, prompt version/hash, 설정, response ID, 단계별 호출·실패·토큰·비용·지연을 기록한다.
- Step 3a: Dense=Step 2 한국어, BM25=영어. Step 2와 비교한다.
- Step 3b: Dense=영어, BM25=같은 영어. Step 3a와 비교하며 추가 LLM 호출은 없다.
- 채택 기준은 각 비교에 따로 적용한다. 앞 단계가 탈락하면 다음 조건은 직전 채택 설정과 직접 비교한다.
  결과를 본 뒤 주 조건을 바꾸지 않는다. 두 단계 실제 서비스 호출의 지연·토큰·비용을 각각 보고한다.

### 4. child search unit (Parent-Child Retrieval)

- 변경: Dense가 보는 단위만 바꾼다. BM25와 parent chunk는 그대로 둔다.
- parent chunk 안에서 문장 경계로 child를 만든다.
  MiniLM tokenizer로 재서 특수 토큰을 포함해 약 110토큰 이하로 한다.
- 110토큰 초과 문장만 토큰 경계로 강제 분할하고 같은 parent ID에 연결한다.
  강제 분할 문장 수, 생성 child 비율, child 토큰 수 분포를 보고한다.
- 이번 단계에서는 child 앞에 제목·섹션을 붙이지 않는다(6단계에서 따로 실험).
- Dense는 child를 임베딩하고 child 50개를 가져온다.
- 각 parent의 순위는 그 parent에 속한 child 중 가장 높은 순위로 정한다. 같은 parent는 한 번만 센다.
- BM25는 parent 단위 그대로다. RRF는 parent 단위 Dense 순위와 BM25 순위를 합친다.
  고유 parent 후보 수와 과거 명세 불일치는 아래 **Step 4 구현 보정** 절을 참조한다. 보정은 parent 상위 10개만 전달한다.
- parent chunk ID를 그대로 쓰므로 기존 Gold를 그대로 사용한다.
- child ID, 소속 parent ID, child 토큰 수 분포를 기록한다.

#### Step 4 구현 보정 및 입력 길이 독립 비교

- 원 지시의 후보 수는 고유 parent 10개다. 이전 Step 4 구현의 parent 20~47개 RRF 입력은 명세 불일치였다.
- 기존 child 동결본을 재사용해 고유 parent 10개를 채울 때까지 조회하고 상위 10개만 전달한다.
  이전 결과는 보존하고 보정 결과를 새 version으로 저장한다.
- 같은 MiniLM·parent에서 max_seq_length 256 / 512만 별도로 비교하고 새 embedding version을 만든다.
- 세 조건은 각각 Step 3b와 독립 비교한다. 합산 CE@10 비감소 AND 문항별 EGR 개선 > 악화가 채택 기준이다.
- 복수 통과 시 합산 CE → 합산 EGR → 단순성(512 > 256 > child) 순으로 선택한다. 미통과 시 Step 3b 유지.
- 사전 기준·보정의 성격·보존·실행 순서: `docs/decisions/RETRIEVAL_V2_CHILD_CORRECTION_LENGTH.md`.

### 5. candidate depth

- 변경: RRF에 넣는 Dense·BM25 후보 수만 바꾼다. 최종 Top-K는 10으로 고정한다.
- 비교: 10 / 20 / 50. 이 세 값 외의 미세 조정은 하지 않는다.
- 이 순서대로 매번 직전 채택 설정에 합산 사전 기준을 적용한다. child 경로의 검색 풀은 50개로 유지한다.
  parent 중복 제거 후 목표보다 후보가 적으면 추가 검색하지 않고 실제 수를 보고한다.

### 5.1. Reranker 후보 진단 및 독립 비교

- 사용자 추가 승인에 따라 Step 3b 영어 검색어와 parent 원문을 유지하고 reranker만 비교한다.
- 지정 모델 `cross-encoder/ms-marco-MiniLM-L-6-v2`의 canonical 저장소
  `cross-encoder/ms-marco-MiniLM-L6-v2`, revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`를 고정한다.
- A: Dense10+BM25_10, B: Dense50+BM25_50 중복 제거 후보 → reranker → 최종 10.
  동결된 같은 후보의 기존 RRF와 각각 비교하며 채택은 둘 다 Step 3b 대비 독립 판정한다.
- 검색 전 고정한 기준: 합산 CE@10 비감소 AND EGR 개선 수 > 악화 수 AND CPU 평균 지연 ≤ 3초.
  둘 다 통과하면 합산 CE → EGR → 후보가 적은 A 순으로 선택한다. 미통과 시 Step 3b 유지.
- Gold는 모든 A/B 순위를 저장한 뒤 후보 포함 진단·지표에만 쓴다. 누락 required group은
  깊이 10 후보 안 / 깊이 50에서만 발견 / 둘 다 밖으로 집계한다. 입력 잘림은 reranker tokenizer로 측정한다.
- 사전 기준·CPU 측정 범위: `docs/decisions/RETRIEVAL_V2_RERANKER.md`.
  실행 안내: `docs/RETRIEVAL_V2_RERANKER.md`. 서비스 기본값 legacy/H0와 held-out 제한은 유지한다.

### 6. metadata (이번 범위에서 제외)

- 변경: 검색 입력에 논문 제목과 섹션 경로를 추가하는 효과를 본다.
- child와 BM25 중 한 곳에만 적용하는 실험부터 한다.
- child에 붙이면 그 토큰도 110토큰 한도에 포함한다.

### 7. Retrieval v2 설정 확정

- 채택된 변경을 모아 V2 설정을 만들고 freeze한다.
- 설정 파일 hash와 채택·제외 근거를 기록한다.
- 최종은 Step 3b 기반: 생성·원문 보완 → 영어 번역·실패 보완 → parent MiniLM 128,
  Dense10 + BM25 positive_only10 → RRF Top-10. 두 검색 모두 실제 Step 3 query를 쓴다.
  child/reranker는 미사용, 서비스 기본값 legacy/H0 유지. corpus 경로는 명시 설정에서만 지정한다.
- [최종 결정](../decisions/RETRIEVAL_V2_FINAL.md)의 사후 선택 이력과 제외 이유를 config manifest에 연결한다.
- 7단계 이후에는 확정된 V2 설정을 pgvector 운영 경로에서도 재현 확인한다.
  개발용 메모리 검색과 운영 경로의 결과를 비교하고, 차이가 있으면 원인을 기록한다.
  이 확인은 0단계에서 구현하거나 실행하지 않는다.

### 8. held-out 평가

- 최종 V2 config와 manifest를 freeze한 이후에만 시작한다.
- held-out 24문항에 Step 2 생성 → freeze → Step 3 번역 → freeze를 각각 1회만 수행한다.
  입력은 `original_question`만 사용하며, 번역 입력은 동결된 Step 2 실제 query다.
  Gold, evidence, case ID, 평가 분류, held-out 결과는 생성기/번역기 입력이나 query 선택에 사용하지 않는다.
- 기존 동결 prompt, schema, validator, provider/model 설정을 그대로 사용한다. 사람이 생성/번역 검색어를
  검토하거나 수정하지 않는다. 검증 실패는 dev의 사전 보완 규칙을 그대로 적용한다.
- 검색 전에 두 query version을 동결하고 이후 재생성하지 않는다. 결과 확인 후 prompt·검증 규칙·검색어를 변경하지 않는다.
- corpus v2로 H0 / H1 / Step 2 기반 / 최종 V2(Step 3b 기반) 각각의 인덱스를 만든다.
- 네 설정의 전체 정의와 인덱스 참조를 manifest에 기록한다. H0/H1은 원 질문, Step 2는 동결된 실제 Step 2 query,
  Final V2는 동결된 실제 Step 3 query를 Dense/BM25에 같이 사용한다.
- held-out v2를 설정별로 한 번씩만 실행한다. 결과를 보고 설정을 바꾸지 않는다.
  설정을 바꿔야 한다면 새 held-out 평가가 필요하다.
- 네 설정의 모든 검색 순위를 먼저 저장한 뒤에만 Gold로 지표를 계산한다. Gold·corpus·깊이도 결과를 보고 수정하지 않는다.
- held-out 설계 기록에 있는 문항 구성(순수 한국어 문항 등)에 따라 별도로 집계한다.
- required 근거 없는 문항은 retrieval 지표에서 명시적으로 제외하고 boundary를 따로 집계한다.
  abstention correctness는 실제 schema/runtime에서 측정 가능한 경우에만 보고하며 검색 결과로 추정하지 않는다.
- 호출·입력/출력 token·실패/보완·추정 비용과 결과 manifest/hash를 함께 동결한다.

## 보고

- 단계별 dev / dev-ko 지표와 직전 단계 대비 변화, 문항별 변화, 채택 여부와 이유
- held-out에서 H0 / H1 / Step 2 기반 / 최종 V2 비교, boundary / abstention 별도 집계
- 영어 변환의 추가 호출, 토큰, 지연시간
- 한계: dev가 작다는 점, dev와 held-out의 코퍼스가 다르다는 점, 범위에서 제외한 항목

## 범위 밖으로 남기는 것

- 임베딩 모델 교체
- 표·그림 근거
- 중복 행정 안내문 chunk 처리
- 영문 약어+숫자 정규화(`RIR2`→`RIR 2`, `RPE8`→`RPE 8`): 범위 밖, 미실시
- Retrieval 변경 이후 Grader 재평가와 E2E 평가
- 향후 과제: BGE-M3 등 긴 입력 모델 비교, child 벡터 결합 parent 검색. 이번에는 구현·평가하지 않는다.
