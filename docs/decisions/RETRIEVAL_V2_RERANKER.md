# Reranker 진단·실험 사전 결정

사용자 승인일: 2026-10-10. 후보 진단·reranker 검색 결과를 보기 전에 고정한다.

## 비교와 보존

- 기준선은 사용자 승인 사후 결정인 Step 3b다. 과거 Step 2/3 채택 기록은 보존한다.
- Corpus v1의 동결 parent 원문, 승인된 Step 3b 실제 영어 검색어, 기존 MiniLM embedding,
  BM25 positive_only를 유지한다. 기존 Step 3b 및 parent 깊이 50의 동결 후보를 재사용한다.
- A: Dense10 + BM25_10 중복 제거 후보 → reranker → 최종 10.
- B: Dense50 + BM25_50 중복 제거 후보 → 같은 reranker → 최종 10.
- A는 같은 후보의 Step 3b RRF, B는 같은 후보의 기존 깊이 50 RRF와 효과를 비교한다.
  채택은 두 조건 모두 Step 3b와 독립 비교한다. 후보 확대 효과와 재정렬 효과를 섞어 해석하지 않는다.
- 모델은 사용자 지정 `cross-encoder/ms-marco-MiniLM-L-6-v2` 하나다. Hugging Face의 이 별칭은
  `cross-encoder/ms-marco-MiniLM-L6-v2`로 연결된다. revision
  `233902d25c440f23af6f7d6e94d2946bac0bee0a`와 파일 해시를 고정한다. 다른 모델은 시도하지 않는다.

## CPU 및 입력 계약

- `config/reranker_minilm_v1.json`: CPU, float32, batch 16, PyTorch intra-op thread 4, 길이 512.
  속도 결과를 보고 batch·thread·precision을 조정하지 않는다.
- 입력은 영어 검색어와 parent 원문 전체다. 사전 요약·child 분할·metadata 추가는 하지 않는다.
  tokenizer의 `only_second`로 parent 뒷부분만 자르며 검색어를 보존한다.
  질문만으로 입력 한도가 소진되면 실행 실패로 처리하고 조용히 질문을 자르지 않는다.
- raw logit 내림차순, 같은 점수는 기존 RRF 순위 → chunk ID로 정한다. 점수 임계값을 두지 않는다.
- 잘림은 모델 tokenizer 기준 질문+parent+특수 토큰의 512 초과 여부다.
  문항별/조건별 후보 쌍 잘림 수·비율, 고유 parent의 본문 잘림 비율, 토큰 분포를 기록한다.
- 모델 적재와 synthetic 1쌍 warm-up은 별도 기록한다. 채택용 문항 지연은 warm-up 후 각 문항의
  입력 준비·길이 검사·tokenization·전체 후보 추론·정렬 시간을 포함한다. 모델 다운로드·적재,
  기존 검색 및 파일 쓰기는 제외한다. dev/dev-ko 36문항을 조건별로 모두 다시 계산한다.
  A/B를 각 문항에서 번갈아 실행하며 문항 간 score cache나 후보 축소를 하지 않는다.
- CPU 평균/중앙값/p95/최대와 문항별 값, 라이브러리·CPU 환경을 기록한다.

## 진단 정의

Step 3b Top-10이 덮지 못한 required evidence group만 다음 상호 배타적 범주로 나눈다.

1. a: Dense10+BM25_10 합집합이 group의 기존 `any`/`all` 규칙을 만족한다.
2. b: a가 아니며 Dense50+BM25_50 합집합이 그 규칙을 만족한다.
3. c: 두 합집합으로도 그 규칙을 만족하지 못한다.

조건 B의 전체 후보 포함 group 수는 a+b다. 문항 수는 해당 범주의 group이 하나 이상인
문항 수로 집계하므로 범주 간 문항 수를 합산하지 않는다. dev/dev-ko 별도 및 합산을 보고한다.
후보 전체의 EGR·CE를 진단 상한으로 보고하되, 10개 선택 제약과 모델 오류 때문에 달성 보장은 없다.
Gold는 모든 A/B 순위를 저장한 뒤 평가 진단/지표 계산에서만 읽고 모델·후보 선택에 전달하지 않는다.

## 채택 기준

- dev+dev-ko 합산 36회 측정 CompleteEvidence@10 비감소.
- Step 3b 대비 문항별 EGR@10 개선 수 > 악화 수.
- 조건별 CPU 평균 reranker 지연 ≤ 3,000ms. 정확히 3초는 허용한다.
- 둘 다 통과하면 합산 CE → 합산 EGR → 후보가 적은 A 순으로 선택한다.
  지표 동일 허용 오차는 기존 1e-12를 사용한다. 미통과 시 Step 3b 유지.
- dev/dev-ko와 Hybrid/문헌 전용 각각의 결과는 보고하되 별도 채택 조건을 추가하지 않는다.

기존 corpus·query·Gold·embedding·실험·결정은 덮어쓰지 않는다. 새 산출물에 protocol과 실행
소스를 보존하고 freeze한다. 서비스 기본값은 legacy/H0이며, 운영 경로 연결·최종 V2 동결은 후속 결정이다.
held-out 질문·Gold는 열람하거나 실행하지 않는다. 개발셋 개선을 독립 일반화 성능으로 표현하지 않는다.
