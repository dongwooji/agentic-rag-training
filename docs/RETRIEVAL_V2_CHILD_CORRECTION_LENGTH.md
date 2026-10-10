# Step 4 구현 보정·입력 길이 실행 안내

[사전 결정](decisions/RETRIEVAL_V2_CHILD_CORRECTION_LENGTH.md)을 먼저 확인한다.
언어별·문항별 지표, 채택 여부와 동결 hash는 [결과 보고서](RETRIEVAL_V2_CHILD_CORRECTION_LENGTH_RESULTS.md)에 있다.
이전 Step 4의 명세와 다른 parent 20~47개 입력을 보정하는 작업이다. 이전 동결본은 보존한다.
세 조건은 각각 Step 3b와 비교하며 child와 입력 길이를 함께 변경하지 않는다.

## 실행 구조

- `ParentQuotaChildStore`: 최고 child 순서로 고유 parent 10개를 채워 반환한다.
  첫 요청 50개, 부족하면 50개씩 확장하며 전체 child 소진까지 유한하게 반복한다.
  중복 parent는 한 번만 세고 최고 child 순위와 연속 parent 순위를 모두 기록한다.
- `MiniLMEncoder(max_seq_length=...)`: 동일 pinned 모델의 position capacity 안에서
  선언된 128/256/512 상한만 허용한다. 인자를 생략한 기존 경로는 그대로 128이다.
- 새 schema v3 config: child 보정은 128, parent 길이는 256/512만 허용하며
  Step 3b 검색어·BM25 positive_only·Dense/BM25 후보 10·RRF Top-10을 고정한다.
- 서비스 기본값은 legacy/H0를 유지한다. 운영 pgvector 적재·전환·재현은 후속 작업이다.
  child pgvector는 지원되지 않은 상태로 DB/모델 초기화 전에 거부한다.

## 준비와 평가

기존 frozen corpus v1·dev/dev-ko·승인된 생성 v1/번역 v2·Step 2/3 결과 v2,
child v1, pinned 모델 캐시가 필요하다. LLM 키와 PGPASSWORD는 사용하지 않는다.

아래는 이번 version의 경로다. 스크립트는 기존 출력 경로가 있으면 중단한다.
새 실행에는 새 version 경로를 쓰고 기존 embedding·query·평가를 덮어쓰지 않는다.

```powershell
python scripts/build_retrieval_parent_lengths.py --length 256 --output data/literature/representations/retrieval_parent_length256_v2
python scripts/build_retrieval_parent_lengths.py --length 512 --output data/literature/representations/retrieval_parent_length512_v1
python scripts/run_retrieval_child_length.py --output reports/experiments/retrieval_v2_child_length_evaluation_v1
```

Embedding 준비는 질문·Gold 없이 수행한다. `parent_tokens.json`에 parent 순서·길이·잘림 여부를 기록하고
`embeddings.npy`, `stats.json`, `sources/`, `manifest.json`을 새 version으로 동결한다.
길이 계산은 특수 토큰 포함이며 토큰 보존 비율은 `sum(min(length,limit))/sum(length)`다.
이는 입력 길이 진단 지표로 의미적 근거 보존을 보장하는 수치는 아니다.

평가는 승인·모든 동결 산출물 해시와 parent membership을 확인하고 protocol/소스 사본을 저장한다.
Step 3b·보정 child·256·512의 순위를 모두 저장한 뒤 Gold를 읽어 지표를 계산한다.
Step 3b 순위·점수·지표 재현과 조건별 BM25 결과 동일성을 검사한다.
`result.json`에 개별·합산·문항 종류별 지표, 문항별 변화, 독립 채택 판정과 최종 선택을 기록한다.
Child 대응 파일에는 실제 parent 수·child 조회/소비 수·추가 요청 깊이·충족 여부를 보존한다.

여러 조건이 통과하면 합산 CE → EGR → 사용자 지정 단순성(512 > 256 > child)을 적용한다.
모두 미통과하면 Step 3b다. 결과 후 기준을 바꾸거나 추가 길이를 시도하지 않는다.

## 테스트와 범위

```powershell
python -m pytest tests/test_child_length_correction.py tests/test_child_depth.py tests/test_retrieval_config.py tests/test_dense_retrieval.py tests/test_phase8_tools.py -q
python -m pytest -m "not requires_local_artifacts" -q
python -m pytest -q
```

합성 테스트는 parent 상한·추가 조회·소진·잘못된 결과 거부·빈 BM25 RRF·입력 상한·동률 선택·
역사적 config 직렬화·승인 전 검색 차단·동결 embedding 변조 검출을 확인한다.
실제 연구 평가는 pinned MiniLM과 corpus v1을 사용하고 DB/LLM 호출 테스트와 구분해 보고한다.

BGE-M3 모델 교체와 child 벡터 결합은 향후 과제로만 기록했다. 구현·실행하지 않는다.
held-out 질문·Gold는 열람/실행하지 않는다. 다음 단계는 별도 결정 후 시작한다.
