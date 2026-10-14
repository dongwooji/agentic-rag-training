# BGE-M3 모델 비교 실행 조건 v1

고정일: 2026-10-14 (Asia/Seoul). 기존 경로 A 준비 문서는 보존하며, 사용자가 승인한 3~5단계의 실행 조건을 검색 전에 기록한다. 결과를 본 뒤 조건·Gold·검색어·채택 기준을 변경하지 않는다.

## 기준선과 중단 조건

동결 corpus v1(488 parent), `retrieval_step3_translation_queries_v2/{dev,dev-ko}.jsonl`의 `result.actual_query`, `retrieval_step3b_translation_v1.json`을 사용한다. 기존 generation/translation manifest 및 승인 연결을 검증한다. 기존 순위는 `retrieval_v2_child_length_evaluation_v1/step3b/rankings.json`과 source 순위로 비교한다. MiniLM 128을 BGE-M3와 같은 FrozenHybridRetriever·MemoryCosineStore 실행 경로로 실행한다. encoder와 해당 벡터만 모델별로 바꾼다.

MiniLM Hybrid 기준선은 dev EGR@10 0.697222 / CE@10 0.611111 / MRR 0.383333, dev-ko 0.808333 / 0.722222 / 0.480688, 합산 36입력 0.752778 / 0.666667 / 0.432011이다. 기존 저장 순위와 Top-10 ID·순서가 일치하고 기존 원정밀도 metric도 일치해야 다음 모델을 실행한다. 원정밀도 비교에는 기존 METRIC_ATOL=1e-12를 사용하고 6자리 표시값도 확인한다. score 차이는 별도로 기록한다. 기준선 불일치 시 BGE-M3 검색과 held-out 진단을 시작하지 않고 차이만 보고한다.

## 공통 조건과 encoder 차이

- parent, exact cosine, Dense Top-10, BM25 positive_only Top-10, k1=1.2, b=0.75, 기존 tokenizer.
- equal-weight RRF k=60, 최종 Top-10, 기존 tie ordering. child·reranker·metadata 없음.
- 두 모델 모두 CPU float32, L2 정규화, 원문 text/query만 입력. 새 instruction prefix 없음.
- MiniLM revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42, mean pooling, max_seq_length=128, 384차원. 기존 v1 파일형 벡터가 없으면 동일 pinned encoder로 새 비교 폴더에 재계산하며 기존 벡터·DB는 변경하지 않는다.
- BGE-M3 revision 5617a9f61b028005a4858fdac845db406aefb181, CLS pooling, max_seq_length=8192, 1024차원. 이미 준비한 v1/v2 벡터와 manifest를 읽기만 한다.
- single-query 호출; intra-op 4 / inter-op 1 threads. 두 모델은 같은 encode 호출·검색·평가 함수 경로를 사용한다. 설치된 라이브러리 version과 모델 파일 hash를 기록한다.

## 채택 판정

최종 Hybrid 합산 36입력만 사용한다. dev 18개 의미 질문과 대응 dev-ko 18입력이며 독립적인 36개 질문으로 표현하지 않는다.

1. CE@10 감소 없음(기존 수치 비교 허용오차 1e-12).
2. 문항별 EGR@10 개선 수 > 악화 수(동일 판정도 기존 허용오차 1e-12).
3. **실제 frozen retrieval query** 36개의 BGE-M3 CPU embedding 평균 ≤ 1초.

이 세 조건을 모두 만족해야 채택한다. 분할별 결과와 Dense-only 결과는 참고용으로만 기록한다. 이전 원 질문 임베딩 시간은 이번 판정에 사용하지 않는다. CPU 시간은 warm model에서 query마다 encode를 1회 호출해 tokenizer 포함 encode 구간만 측정한다. 모델 로드와 고정 synthetic warmup 3회는 제외하고 별도 기록한다. batch_size=1, 평균·p95(linear interpolation)·최댓값과 전체 개별 시간을 기록한다. 결과에 따라 실행 설정을 바꾸거나 재측정하지 않는다.

## 판정 이후 held-out 진단

채택/비채택 판정 파일과 SHA-256을 먼저 저장한다. 이후 판정과 무관하게 기존 held-out v2의 evidence-bearing 20문항을 MiniLM/BGE-M3 각 1회 실행한다. 동결 corpus v2(683 parent), 기존 frozen Final V2 실제 query, 기존 Gold와 동일 공통 검색 조건을 사용한다. 준비된 BGE-M3 v2 및 기존 MiniLM v2 parent 벡터를 읽는다. diagnostic only이며 이미 분석된 자료이므로 독립 검증·채택 평가로 표현하지 않는다. 이 결과로 앞선 판정을 변경하지 않는다.

## 보존 및 산출물

모든 순위는 평가용 Gold를 적용하기 전에 저장한다. 평가 전후 입력·보호 파일 hash를 확인하며 새 실행 산출물은 `reports/experiments/bge_m3_model_comparison_v1/`에만 저장한다. 운영 서비스·기존 Gold·기존 벡터·corpus·prompt·Router와 역사적 보고서는 수정하지 않는다. LLM/API/DB 호출 없이 로컬 encoder 계산과 offline 검색을 수행한다. v1/v2 통합 corpus와 운영 전환은 이후 별도 작업이다. PR은 merge하지 않는다.
