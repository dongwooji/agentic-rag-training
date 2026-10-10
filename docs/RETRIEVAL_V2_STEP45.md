# Retrieval V2 Step 4·5 실행 안내

[사전 결정](decisions/RETRIEVAL_V2_STEP45.md)과 [실행 결과](RETRIEVAL_V2_STEP45_RESULTS.md)를 함께 확인한다.
기존 Step 2/3의 사전 채택 결과는 Step 2만 채택이다. 사용자 승인에 따른 이후 기준선 Step 3b는
결과 확인 후 정한 사후 결정이며, Step 4·5의 합산 채택 기준과 실행 순서는 검색 전에 별도 고정했다.

## 구성과 적용 범위

- `src/retrieval/child.py`: frozen parent의 문장 경계 child 구성과 최고 순위 child에 따른 parent 중복 제거.
- `src/evaluation/child_depth.py`: dev/dev-ko 합산 CE@10 감소 없음 및 문항별 EGR@10 개선 > 악화 판정.
- `config/retrieval_step4_child_v1.json`: Dense child 풀 50, BM25 parent 10, 기존 RRF Top-10.
- `config/retrieval_step5_{child,parent}_d{10,20,50}_v1.json`: 결과를 보기 전에 준비한 두 표현의 깊이 조건.
  Step 4 채택 시 child, 제외 시 parent 조건만 실행한다. child의 검색 풀은 50으로 고정한다.

별도 child version을 만들며 frozen corpus·parent·평가 질문·Gold·query·역사적 결과는 수정하지 않는다.
일반 문장은 경계에서 묶고, 단일 문장이 110토큰을 초과할 때만 tokenizer 경계에서 강제 분할한다.
동일 parent ID를 유지하고 원문 문자 범위, 토큰 수, 강제 분할 여부를 기록한다.

RRF에는 최고 순위 child로 정렬한 연속 parent 순위를 전달한다. 중복 child로 parent 점수를 가산하지 않는다.
원래 최고 child 순위는 대응표에 별도 보존한다. parent 수가 상한에 못 미쳐도 추가 검색하지 않는다.

**서비스 기본값은 legacy/H0를 유지하며, V2 설정 확정 시 기본값을 전환한다.**
현재 pgvector는 기존 parent 표현만 지원한다. child 설정으로 서비스를 구성하면 DB·모델 초기화 전에 거부한다.
운영 pgvector 재현 확인은 Step 7 이후 별도 작업이며 이번 평가에 포함하지 않는다.

## 동결과 실행

로컬 frozen corpus v1, 과거 Hybrid 자산, dev/dev-ko, 승인된 query 생성 v1·번역 v2,
Step 2/3 평가 v2 및 pinned MiniLM 모델 캐시가 필요하다. 외부 LLM 키나 PGPASSWORD는 필요하지 않다.

아래는 이번 실행의 출력 경로다. 이미 동결돼 있으므로 그대로 재실행하면 중단한다.
새 실행은 새 version 출력 경로를 쓰고, 기존 version을 삭제하거나 덮어쓰지 않는다.

```powershell
python scripts/build_retrieval_children.py --output data/literature/representations/retrieval_child_v1
python scripts/run_retrieval_child_depth.py --children data/literature/representations/retrieval_child_v1 --output reports/experiments/retrieval_v2_step45_evaluation_v1
```

첫 스크립트는 평가 질문·Gold 없이 child와 embedding을 만들어 동결한다.
두 번째 스크립트는 사용자 승인과 동결 query·모델·config·코드 해시를 확인하고,
사전 protocol과 소스 사본을 저장한 뒤 Step 3b 재현 → Step 4 → Step 5(10/20/50)를 실행한다.
각 조건의 순위를 저장한 뒤에만 Gold로 지표를 계산한다. held-out 입력 경로는 없다.
생성·번역 모델은 다시 호출하지 않는다. 평가 중 설정·코드가 바뀌면 중단한다.

산출물:

- child: `children.jsonl`, `embeddings.npy`, `stats.json`, `sources/`, `manifest.json`.
- 평가: `protocol.json`, `sources/`, 조건별 `rankings.json`, `source_rankings.json`, `child_mappings.json`,
  `result.json`, `manifest.json`.
- `result.json`: dev/dev-ko·Hybrid/문헌 전용·합산 지표, 문항별 변화, 조건별 비교 기준과 자동 채택 여부,
  선택된 설정, 분할 통계와 실행 범위.

## 검증

```powershell
python -m pytest tests/test_child_depth.py tests/test_retrieval_config.py tests/test_retrieval_preparation.py tests/test_phase8_tools.py tests/test_literature_query_generation.py -q
python -m pytest -m "not requires_local_artifacts" -q
python -m pytest -q
```

공개 테스트는 합성 입력으로 문장/토큰 경계, 강제 분할, parent membership·중복 제거,
빈 BM25의 Dense-only RRF, 후보 상한·부족 처리, parent 원문 반환, 역사적 config 직렬화,
승인 전 검색 차단과 합산 채택 기준을 검증한다. 실제 MiniLM·corpus 평가와 DB/LLM mock 테스트를 구분해서 보고한다.

이번 결과는 모든 변경 제외, 누적 기준선 Step 3b 유지다. Step 6(metadata)는 범위 밖이고,
Step 7 및 held-out 최종 평가는 아직 실행하지 않았다.
