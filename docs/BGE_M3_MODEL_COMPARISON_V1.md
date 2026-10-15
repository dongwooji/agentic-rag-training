# MiniLM 128 / BGE-M3 모델 비교 실행 v1

실행일: 2026-10-14 (Asia/Seoul). 기존 경로 A의 3~5단계를 실행했다. **사전 채택 기준 판정: BGE-M3 비채택.** 운영 서비스의 모델·corpus·해석 기본값(legacy)은 변경하지 않았다.

dev/dev-ko는 같은 18개 의미 질문의 대응 입력 36개이며 개발 기준 결정이다. held-out v2는 이미 분석된 자료의 **diagnostic only** 결과다. 독립 일반화 성능이나 채택 평가로 표현하지 않는다.

## 1. MiniLM 기준선 재현

Hybrid Top-10 parent/chunk ID와 순서 **36/36 완전 일치**, mismatch 0개. Dense·BM25 후보 Top-10 ID·순서도 각각 36/36 일치했다. 기존 순위가 비교 가능한 형태로 있어 metric만 비교한 것은 아니다.

dev / dev-ko / 합산 36입력의 Hybrid EGR@10·CE@10·MRR이 기존 원정밀도 값과 일치했다(METRIC_ATOL=1e-12). 아래 표의 MiniLM 값은 이번 재현값이다.

Dense score 최대 절대 차이는 `1.5412992671048897e-07`, RRF score 차이는 `0`이었다. pinned 모델·라이브러리와 검색 조건은 동일하지만 새 CPU 계산과 기존 저장값에 작은 수치 차이가 있었다. 특정 연산이나 thread를 그 원인으로 확정하지 않는다. 순위나 metric 차이는 없었다.

기존 MiniLM v1 벡터는 파일로 저장되어 있지 않아 pinned encoder로 새 비교 폴더에 재계산했다. 기존 벡터·DB를 읽거나 갱신하지 않았다. 새 문서 벡터는 (488,384) float32이며 아래 manifest에 hash를 기록했다. baseline gate 통과를 저장한 뒤 BGE-M3 검색을 시작했다.

## 2. 모델 비교: corpus v1

| 검색 | 분할 | 입력 수 | 모델 | EGR@10 | CE@10 | MRR |
|---|---|---:|---|---:|---:|---:|
| Dense-only | dev | 18 | MiniLM 128 | 0.660185 | 0.444444 | 0.327381 |
| Dense-only | dev | 18 | BGE-M3 | 0.785185 | 0.666667 | 0.320833 |
| Dense-only | dev-ko | 18 | MiniLM 128 | 0.678704 | 0.500000 | 0.352381 |
| Dense-only | dev-ko | 18 | BGE-M3 | 0.757407 | 0.611111 | 0.345899 |
| Dense-only | 합산 | 36 | MiniLM 128 | 0.669444 | 0.472222 | 0.339881 |
| Dense-only | 합산 | 36 | BGE-M3 | 0.771296 | 0.638889 | 0.333366 |
| 최종 Hybrid | dev | 18 | MiniLM 128 | 0.697222 | 0.611111 | 0.383333 |
| 최종 Hybrid | dev | 18 | BGE-M3 | 0.734259 | 0.611111 | 0.441733 |
| 최종 Hybrid | dev-ko | 18 | MiniLM 128 | 0.808333 | 0.722222 | 0.480688 |
| 최종 Hybrid | dev-ko | 18 | BGE-M3 | 0.775926 | 0.666667 | 0.468519 |
| 최종 Hybrid | 합산 | 36 | MiniLM 128 | 0.752778 | 0.666667 | 0.432011 |
| 최종 Hybrid | 합산 | 36 | BGE-M3 | 0.755093 | 0.638889 | 0.455126 |

Dense-only 개선이 최종 Hybrid의 모든 지표 개선으로 이어지지는 않았다. 최종 Hybrid 합산 EGR@10은 +0.002315, CE@10은 −0.027778, MRR은 +0.023115였다. 이 관찰 뒤 BM25·RRF·후보 깊이 등을 조정하지 않았다.

## 3. 36입력별 최종 Hybrid EGR@10

| 분할 | case ID | MiniLM EGR@10 | BGE-M3 EGR@10 | 변화 |
|---|---|---:|---:|---|
| dev | LIT-001 | 1.000000 | 1.000000 | same |
| dev | LIT-002 | 1.000000 | 1.000000 | same |
| dev | LIT-003 | 0.750000 | 0.750000 | same |
| dev | LIT-004 | 1.000000 | 1.000000 | same |
| dev | LIT-005 | 1.000000 | 1.000000 | same |
| dev | LIT-006 | 1.000000 | 1.000000 | same |
| dev | LIT-007 | 1.000000 | 1.000000 | same |
| dev | LIT-008 | 0.800000 | 0.800000 | same |
| dev | LIT-009 | 1.000000 | 1.000000 | same |
| dev | LIT-010 | 1.000000 | 0.666667 | worsen |
| dev | HYB-001 | 0.000000 | 0.000000 | same |
| dev | HYB-002 | 0.000000 | 0.000000 | same |
| dev | HYB-003 | 0.000000 | 1.000000 | improve |
| dev | HYB-004 | 0.000000 | 0.000000 | same |
| dev | HYB-005 | 1.000000 | 1.000000 | same |
| dev | HYB-006 | 0.000000 | 0.000000 | same |
| dev | HYB-007 | 1.000000 | 1.000000 | same |
| dev | HYB-008 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-001 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-002 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-003 | 0.750000 | 0.000000 | worsen |
| dev-ko | LIT-004 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-005 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-006 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-007 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-008 | 0.800000 | 0.800000 | same |
| dev-ko | LIT-009 | 1.000000 | 1.000000 | same |
| dev-ko | LIT-010 | 1.000000 | 0.666667 | worsen |
| dev-ko | HYB-001 | 0.000000 | 0.000000 | same |
| dev-ko | HYB-002 | 0.000000 | 0.500000 | improve |
| dev-ko | HYB-003 | 1.000000 | 1.000000 | same |
| dev-ko | HYB-004 | 1.000000 | 1.000000 | same |
| dev-ko | HYB-005 | 1.000000 | 1.000000 | same |
| dev-ko | HYB-006 | 0.000000 | 0.000000 | same |
| dev-ko | HYB-007 | 1.000000 | 1.000000 | same |
| dev-ko | HYB-008 | 1.000000 | 1.000000 | same |

합계: **improve 2 / worsen 3 / same 31**. 동일 여부는 기존 METRIC_ATOL=1e-12로 비교했다. 같은 의미 질문의 두 언어 입력을 별도로 센 기존 36입력 분모를 유지했다.

## 4. 사전 채택 기준과 확정 판정

| 조건: 합산 36입력 최종 Hybrid만 사용 | 관찰값 | 충족 |
|---|---|---|
| CE@10 감소 없음 | 24/36 = 0.666667 → 23/36 = 0.638889 | 아니오 |
| 문항별 EGR 개선 수 > 악화 수 | 2 > 3은 거짓 | 아니오 |
| 실제 frozen retrieval query CPU embedding 평균 ≤ 1초 | 0.597194초 | 예 |

**비채택.** 세 조건 중 두 조건을 충족하지 않았다. dev/dev-ko 개별 결과나 Dense-only, MRR 상승, held-out 진단을 판정에 사용하지 않았다. threshold·분모·Gold·검색어를 수정하지 않았다. 비채택은 이번 검색 조건의 개발 기준 결정이며 모델의 일반적 우열 판정은 아니다.

CPU: 실제 동결 검색어 36개 각각 encode 1회, warm model, batch_size=1, intra-op 4 / inter-op 1. 평균 0.597194초, p95 0.968966초(linear interpolation), 최댓값 1.072661초. tokenizer를 포함한 encode 구간만 측정했고 모델 로드·고정 synthetic warmup 3회는 제외했다. 이전 원 질문 임베딩 평균은 판정에 사용하지 않았다. 각 입력의 시간과 warmup은 별도 JSON에 남겼다.

판정 확정 UTC: `2026-10-14T08:41:52.077040+00:00`. held-out protocol은 이 판정 hash `32543280f9a2e1907c33ecb4ca606bf450b505d455d1af6e15dd59d828d64256`를 참조한다. held-out 완료 후 동일 hash를 다시 확인했다.

## 5. held-out v2: diagnostic only

동결 corpus v2 683 parent와 기존 frozen Final V2 query를 사용해 evidence-bearing 20문항을 MiniLM/BGE-M3 각각 1회 실행했다. 기존 MiniLM v2 벡터와 준비한 BGE-M3 v2 벡터를 읽기만 했다. 두 모델의 BM25 후보와 score는 일치했다. 새 MiniLM Top-10은 기존 held-out Final V2 저장 순위와 20/20 일치했고 기존 세 지표도 재현했다.

| 검색 | 모델 | 입력 수 | EGR@10 | CE@10 | MRR |
|---|---|---:|---:|---:|---:|
| Dense-only | MiniLM 128 | 20 | 0.183333 | 0.050000 | 0.253750 |
| Dense-only | BGE-M3 | 20 | 0.333333 | 0.150000 | 0.263393 |
| 최종 Hybrid | MiniLM 128 | 20 | 0.208333 | 0.050000 | 0.211865 |
| 최종 Hybrid | BGE-M3 | 20 | 0.281667 | 0.100000 | 0.242341 |

| case ID | MiniLM EGR@10 | BGE-M3 EGR@10 | 변화 |
|---|---:|---:|---|
| HOV2-001 | 0.000000 | 0.250000 | improve |
| HOV2-002 | 1.000000 | 1.000000 | same |
| HOV2-003 | 0.400000 | 0.200000 | worsen |
| HOV2-004 | 0.000000 | 0.000000 | same |
| HOV2-005 | 0.000000 | 0.000000 | same |
| HOV2-006 | 0.000000 | 0.000000 | same |
| HOV2-007 | 0.666667 | 0.333333 | worsen |
| HOV2-008 | 0.250000 | 0.250000 | same |
| HOV2-009 | 0.250000 | 0.750000 | improve |
| HOV2-010 | 0.200000 | 0.200000 | same |
| HOV2-011 | 0.250000 | 0.250000 | same |
| HOV2-012 | 0.000000 | 0.500000 | improve |
| HOV2-013 | 0.250000 | 0.000000 | worsen |
| HOV2-014 | 0.000000 | 0.000000 | same |
| HOV2-015 | 0.400000 | 0.400000 | same |
| HOV2-016 | 0.000000 | 0.000000 | same |
| HOV2-017 | 0.500000 | 0.000000 | worsen |
| HOV2-018 | 0.000000 | 0.000000 | same |
| HOV2-020 | 0.000000 | 1.000000 | improve |
| HOV2-021 | 0.000000 | 0.500000 | improve |

진단용 EGR 변화: improve 5 / worsen 4 / same 11. 채택 판단에는 사용하지 않았다. required evidence가 없는 `HOV2-019`, `HOV2-022`, `HOV2-023`, `HOV2-024`는 검색하지 않았고 20문항 분모에도 포함하지 않았다. 경로·답변·보류 정확도는 이번 retrieval-only 실행에서 측정하지 않았다.

## 6. 실행 조건과 hash

공통 코드: FrozenHybridRetriever + MemoryCosineStore + 기존 BM25/RRF/any-all Gold metric. parent exact cosine, Dense 후보 10, BM25 positive_only 후보 10(k1=1.2,b=0.75), 기존 tokenizer, equal-weight RRF k=60, 최종 Top-10. child·reranker·metadata 없음. 두 모델에 같은 actual query를 그대로 전달했다. 모델·encoder 및 해당 벡터 외 검색 조건은 같았다.

| encoder | MiniLM | BGE-M3 |
|---|---|---|
| revision | e8f8c211226b894fcb81acc59f3b34ba3efd5f42 | 5617a9f61b028005a4858fdac845db406aefb181 |
| max_seq_length / 차원 / pooling | 128 / 384 / mean | 8192 / 1024 / CLS |
| device / precision / normalization | CPU / float32 / L2 | CPU / float32 / L2 |
| 기존 설정 batch_size; query 호출 입력 수 | 32; 1개 | 1; 1개 |

query의 tokenizer 길이는 dev 최대 67, held-out 최대 78로 MiniLM 128 기준 query 잘림은 없었다. document encoder의 길이 차이와 pooling 차이는 모델 설정의 일부이며, 단독 원인 효과를 분리한 실험은 아니다. 검색어 생성·번역·재검증은 하지 않았다.

Python 3.12.7, torch 2.8.0, sentence-transformers 5.7.0, transformers 5.15.1, tokenizers 0.22.2, numpy 1.26.4, Windows CPU. 모델 파일별 hash·실제 pooling·warmup·load 시간은 encoder.json에 기록했다.

| 입력/판정 | SHA-256 |
|---|---|
| 실행 조건 문서 | `401eb852f65921c24476b9dd62e05f0b32ef8be33a592754620186520b06049c` |
| v1 corpus | `640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177` |
| v2 corpus | `b790f71e0b9fac423553f2bdd7ab7cb68d70790ef965ffa792fe0ae63aac8fc3` |
| dev Final V2 query manifest | `4568e63524f2bbe9b355e902ceac6fb2b225787cafa3bc2a859c45ec7ea0f3a6` |
| dev Gold dataset | `1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509` |
| held-out query manifest | `c09722c34ee3c66506bc4712494ba3dfb1840ae26b27334e4670b3858978ec32` |
| held-out Gold | `7c491a55e96b70b26b10ce7625c535e1dc521b4d83ab9d4e984d5d2c84df4b42` |
| BGE-M3 v1 벡터 | `b2dda9f2c25c9487fbfac07aa5bfbdb2a6db16707ac8ddb5bea76bfea89735c6` |
| BGE-M3 v2 벡터 | `1c72654bc3bf02792fe817426fdecdf602be0d9cf1634118b1a722aac6cd3914` |
| 확정 dev 판정 | `32543280f9a2e1907c33ecb4ca606bf450b505d455d1af6e15dd59d828d64256` |

실행 산출물: `reports/experiments/bge_m3_model_comparison_v1/`. protocol, 모든 query·ranking·Dense/BM25 score·원정밀도 per-case metric·CPU 시간·판정 봉인·보호 파일 snapshot·테스트 XML과 실행 스크립트를 별도 경로에 보존했다. 기존 정책상 ignored 산출물을 강제로 Git에 추가하지 않는다. 최종 artifact manifest는 모든 새 파일 hash와 Git/CI 확인 기록을 포함한다.

## 7. 검증과 제한점

| 검증 | 결과 |
|---|---|
| 관련 회귀 테스트 | 50 passed; failure/error 0; 23.29초 |
| 실행 gate synthetic | 4 passed; failure/error 0; 0.85초 |
| 공개 범위 로컬 테스트 | 936 passed; failure/error 0; 192.71초 |
| 로컬 전체 테스트 | 1017 passed; failure/error 0; 202.71초 |

보호 snapshot 1604개 중 변경 0개. 기존 frozen corpus·Gold·MiniLM/BGE-M3 벡터·query·baseline·prompt·Router·운영 설정과 기존 decision 문서는 보존했다. 공개 범위 로컬 테스트에는 사용자의 미추적 JATS audit 테스트 19개가 포함되어 GitHub CI보다 19개 많다. 해당 파일을 commit하지 않았다.

실행 호출: MiniLM/BGE-M3 각 dev 36개 + held-out 20개 query 검색 = 총 112회. LLM/OpenAI/REST/provider 호출 0회, token 0, 추정 LLM 비용 $0. PostgreSQL/pgvector를 사용하지 않은 offline exact 검색이다. 관련 서비스 테스트는 mock/synthetic 경로이며 실제 REST E2E를 수행한 결과가 아니다.

CPU는 이 Windows PC의 warm model 단일 실행 측정으로, 다른 프로세스가 존재하는 환경이다. 서버 p95나 동시 요청 성능을 보장하지 않는다. dev는 반복 개발 자료이고 held-out도 이미 관찰한 자료다. EGR/CE/MRR은 문헌 검색 지표이며 답변 품질·안전성·Grader/Recovery 성능은 측정하지 않았다. 문서 잘림을 줄이는 것이 모든 질문의 근거 검색 개선을 보장하지 않았으며, 이번 결과 뒤 추가 튜닝은 수행하지 않았다.

운영 서비스 전환과 v1/v2 통합 corpus는 이번 범위 밖이다. 이후 별도 작업으로 검토한다. 준비 PR #17이 merge된 사실을 실행 완료 후 확인했으며, 최신 master의 파일 내용이 실행 당시 tree와 동일함을 확인했다. 최신 master에서 `eval/bge-m3-model-comparison` branch와 새 PR로 실행 조건·결과 문서만 제출하며 merge하지 않는다. 최종 commit·push·GitHub Actions 상태는 별도 git_verification.json 및 완료 보고에서 확인한다.
