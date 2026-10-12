# Service Retrieval V2: pgvector 재현성 확인

실행일: 2026-10-12 (Asia/Seoul). Retrieval 성능 재평가가 아닌 offline ↔ 운영 경로 재현성 확인이다. Gold·정답·case label은 검색에 제공하지 않았다.

Top-10 parent/chunk ID와 순서 완전 일치: **36/36 (100%)**, mismatch **0문항**. dev 18문항 + dev-ko 18문항이다.

## 동일 조건과 corpus 결합

서비스와 DB의 기존 corpus는 `literature_corpus_v1`(488개 parent/chunk)이다. corpus 교체는 수행하지 않았다. 지정 Final V2 hash `004a65821809219bb2823cadcfe77dd98d6a329987a909cfcebcbb1507cd68fe`는 corpus v2 결합 설정이다.

서비스 기본값은 기존 동결 `config/retrieval_step3b_translation_v1.json`이며 SHA-256은 `02a7d398a7a1597e68a56a4e4d97bea5bb6a59fcd98ea7e7a6ae4bbf56a27330`이다. 검색 방식은 지정 Final V2와 같고 corpus·embedding run·자산 검증 hash는 기존 운영 v1에 결합한다. corpus v2 Final 설정 자체를 운영했거나 corpus v2 held-out 결과를 재현한 것은 아니다.

두 경로에 동일 config 객체, 같은 pinned CPU encoder, 동일 corpus의 텍스트·순서, 같은 BM25·RRF 구현을 사용했다. child와 reranker는 없다.

| 조건 | offline 및 운영 공통 값 |
|---|---|
| 모델 | sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 |
| revision | e8f8c211226b894fcb81acc59f3b34ba3efd5f42 |
| 최대 입력 길이 / 차원 | 128 / 384 |
| normalization / precision | normalize_embeddings=True / float32 |
| 검색 단위 | corpus v1 chunk를 parent 단위로 검색 |
| Dense / BM25 후보 깊이 | 10 / 10 |
| BM25 | positive_only, k1=1.2, b=0.75, 기존 tokenizer |
| RRF / 최종 Top-K | equal weights 1:1, k=60 / 10 |
| Dense tie | score 내림차순, chunk_id 오름차순 |
| RRF tie | score_desc_best_rank_rank_sum_chunk_id |

기존 동결 검색어 `data/evaluation/retrieval_step3_translation_queries_v2/{dev,dev-ko}.jsonl`의 `result.actual_query`를 그대로 사용했다. 재생성·재번역·재검증·수정하지 않았다. 표시용 ID는 검색어에 전달하지 않았다.

동결 query manifest SHA-256: `4568e63524f2bbe9b355e902ceac6fb2b225787cafa3bc2a859c45ec7ea0f3a6`. manifest의 모든 산출물 hash를 비교 전에 확인했다.

## 벡터와 수치 확인

offline는 DB 벡터를 복사하지 않고 고정 encoder로 운영 corpus 텍스트를 새로 인코딩했다. DB의 모든 chunk ID 및 text SHA-256 membership도 검증했다.

새 문서 벡터 ↔ DB float32 벡터 최대 절대 차이: `0.0`. 모델/revision, 라이브러리 version은 아래와 같다.

```json
{
  "model_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
  "model_revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
  "embedding_dimension": 384,
  "normalize_embeddings": true,
  "precision": "float32",
  "batch_size": 32,
  "max_sequence_length": 128,
  "device": "cpu",
  "sentence_transformers_version": "5.7.0",
  "transformers_version": "5.15.1",
  "torch_version": "2.8.0",
  "numpy_version": "1.26.4",
  "python_version": "3.12.7",
  "platform": "Windows-11-10.0.26200-SP0"
}
```

```json
{
  "postgresql_version": "18.6",
  "pgvector_version": "0.8.6",
  "driver": "psycopg",
  "driver_version": "3.3.4"
}
```

공통 Top-10 ID의 최대 score 절대 차이: Dense `1.1334249838679256e-07`, BM25 `0`, RRF `0`.

관찰된 Dense score 차이는 offline의 float64 normalization/dot와 pgvector vector(float32) cosine 수치 연산 차이에 부합한다. 정확한 개별 오차의 연산별 분해는 하지 않았다. 순위 mismatch는 없으므로 embedding/index version, SQL tie handling 또는 normalization 문제가 순위 불일치 원인이라고 주장하지 않는다.

운영 SQL은 `<=>` cosine distance와 chunk_id로 정렬하는 exact 검색이다. 운영 table의 index 정의:

```json
[
  "CREATE UNIQUE INDEX embedding_runs_pkey ON retrieval.embedding_runs USING btree (embedding_run_id)",
  "CREATE UNIQUE INDEX chunk_embeddings_pkey ON retrieval.chunk_embeddings USING btree (embedding_run_id, chunk_id)",
  "CREATE INDEX idx_chunk_embeddings_run ON retrieval.chunk_embeddings USING btree (embedding_run_id)"
]
```

LLM 호출 0회, token 0, 추정 비용 $0. PostgreSQL SELECT 및 로컬 encoder 계산을 사용했고 DB에 적재/갱신하지 않았다.

## 문서 보존

DB embedding run metadata에는 max sequence length 전용 컬럼이 없다. 최초 동결 Dense reproducibility의 입력 길이 128, 실제 encoder metadata 128 및 488개 문서 벡터 전 값 일치로 해당 입력 표현의 재현성을 확인했다. SQL metadata만으로 입력 길이를 직접 검증한 것으로 표현하지 않는다.

동결·보호 대상 1,267개 파일의 작업 전/후 SHA-256이 모두 같았다. 비교 이후 검색 코드·설정·prompt·validator·corpus·query를 바꾸지 않았다. 원본 결과 SHA-256은 `ba38f43447584f2c33836df5178a51e9544a83bd048d0ee2bffe0bb81ea08da8`이다.

문항별 동결 검색어·Top-10 ID·순서·score 차이는 로컬 `reports/service_retrieval_v2/REPRODUCIBILITY.md`와 `compare/comparison.json`에 보존했다. docs/PUBLISHING.md에 따라 동결 평가 입력과 문헌 원문을 공개 저장소에 재배포하지 않는다.
