# Step 7/8 최종 실행 안내

이 실행기는 승인된 최종 V2와 H0/H1/Step 2 비교만 수행한다. 추가 튜닝·Gold/query 수정·Phase A 변경은 없다.
서비스 기본값은 legacy/H0다. [최종 결정](decisions/RETRIEVAL_V2_FINAL.md)을 먼저 확인한다.

`scripts/run_retrieval_final.py`는 `--stage config / generation / translation / rankings / evaluation`을 순서대로 실행한다.
각 `--output`은 새 경로여야 한다. 기존 output이 있으면 재호출·덮어쓰기를 거부한다.
중단된 호출 파일도 재생성하지 않는다. `--configuration`은 모든 후속 단계에서 같은 동결 폴더를 가리킨다.

1. `config`: corpus v2 build와 Gold 파일의 승인 hash를 검증한다(질문·Gold 의미 열람 없음).
   corpus 경로가 지정된 네 새 config를 `config/`에 저장하고 전체 정의·과거 설정·실행 소스·prompt/model hash를 동결한다.
   공개 manifest는 `docs/retrieval_v2_final_manifest_v1.json`, 실행 manifest는 새 실험 폴더에 저장한다.
2. `generation`: original_question만 provider에 전달한다. 24회 단일 호출, 기존 검증과 원문 보완 규칙을 그대로 적용한다.
   `--secrets-json .env.local.json`은 이 단계와 번역에서만 허용되는 opt-in loader다. 비밀값은 저장·출력하지 않는다.
   PGPASSWORD·DB 연결은 필요 없다. 생성 결과·실제 query·사용량·실패 사유·manifest를 동결한다.
3. `translation`: `--generation`으로 동결된 실제 Step 2 query를 전달한다. 24회 단일 호출 후 별도 동결한다.
   사람이 검색어를 검토·수정하지 않으며 기존 번역 검증 실패는 Step 2 query로 보완한다.
4. `rankings`: `--generation`, `--translation` 두 동결본을 검증한다. corpus v2 parent embedding을 새 version으로 생성한다.
   네 설정에서 같은 embedding과 corpus를 공유하고, 각 BM25 정책·메타데이터·인덱스 참조를 저장한다.
   네 조건 각각 24회 검색하고 모든 Top-10 순위와 index manifest를 먼저 동결한다. LLM·Gold 의미·DB 접근은 없다.
5. `evaluation`: `--rankings`의 모든 설정별 순위와 hash를 확인한 뒤 Gold를 읽는다.
   기존 any/all evidence group 정의와 MRR(첫 required chunk)을 사용한다.
   required group이 있는 문항만 검색 지표 분모에 포함한다. 사전 분류별 결과·boundary를 따로 기록한다.

최종 설정은 Dense/BM25 모두 실제 영어 query, parent 단위 MiniLM 길이 128, 각 10후보, BM25 positive_only,
RRF k=60 동일 가중, Top-10이다. H0/H1은 원 질문, Step 2는 실제 생성 query를 쓴다.
schema 4 corpus 경로는 `data/literature/` 안의 파일만 허용한다. schema 1~3 기본 경로·동결 검사·기존 설정 파일은 보존한다.

검색 지표를 abstention correctness나 답변 정확도로 해석하지 않는다. 이 실행은 Router·Grader·Recovery·답변 생성을 수행하지 않는다.
pgvector 운영 재현과 새 검색 결과의 Grader/Recovery·E2E 검증은 후속 단계다.

합성 검증: `python -m pytest tests/test_retrieval_final.py tests/test_retrieval_config.py -q`.
합성 테스트는 실제 held-out·API·DB·모델을 읽거나 호출하지 않는다.
