# Retrieval v2 — 1단계 BM25 zero-match 수정

H1은 H0에서 BM25 점수가 0 이하인 후보를 제외하는 변경만 적용한다.
**서비스 기본값은 H0를 유지하며, V2 설정 확정 시 기본값을 전환한다.**
이번 단계는 H1을 명시적으로 선택한 실행만 검증한다.

## 설정과 실행

`config/retrieval_h1_v1.json`은 기존 H0 설정과 같은 모델 revision, corpus 해시,
tokenizer, BM25 계산식, 후보 상한, RRF 공식·가중치·동점 처리를 사용한다.
설정 식별자와 BM25 `score_policy: positive_only`만 다르다.
H0 파일과 기존 동결 결과는 보존한다. H1이 참조하는 `hybrid_baseline_v1`은
검증할 기존 자산의 version이며 새 실행 설정은 `retrieval_h1_v1`로 구분한다.

Dense·BM25 후보 상한과 최종 Top-K는 각각 10을 유지한다. 양수 BM25 후보가
10개 미만이면 그만큼만 반환하며, 없으면 빈 목록이다. 점수 0 후보로 채우지 않는다.
BM25가 비면 RRF는 Dense 순서를 그대로 사용한다. BM25 순위·점수는 `None`,
BM25 기여도는 0이다. Dense 점수의 부호에는 필터를 적용하지 않는다.

기존 평가 실행기에 H1 config와 같은 데이터의 H0 결과 경로를 지정한다.
출력은 항상 `reports/experiments/` 아래 새 경로를 사용한다.

```powershell
python scripts/run_retrieval_v2_preparation.py --dataset dev --config config/retrieval_h1_v1.json --reference-run reports/experiments/retrieval_v2_step0_v1/h0_after_final --output reports/experiments/retrieval_v2_step1_v1/h1_dev
python scripts/run_retrieval_v2_preparation.py --dataset dev-ko --config config/retrieval_h1_v1.json --reference-run reports/experiments/retrieval_v2_step0_v1/h0_dev_ko_verified --output reports/experiments/retrieval_v2_step1_v1/h1_dev_ko
```

H0 재현도 같은 실행기를 사용한다. H0 config를 유지하고 새 출력 경로와 기존
동일 데이터 H0 경로를 지정하면 순위·점수·지표를 비교한다. 고정 모델은 로컬
캐시에서 오프라인 CPU로 실행하고 검색은 메모리 exact cosine을 사용한다.
실제 PostgreSQL/pgvector, OpenAI 및 외부 provider 호출은 하지 않는다.

## 검증과 해석

- 검색 순위를 먼저 저장한 뒤 원 Gold로 평가한다. Runtime에 Gold·case label을 전달하지 않는다.
- H1 비교는 입력·모델·Dense 순위/점수/지표 유지와 BM25가 H0의 양수 후보만
  남기는지를 검사한다. 빈 BM25 문항은 Hybrid 순서가 Dense와 같은지도 확인한다.
- H0와 다른 Hybrid 순위를 H1 실행 실패로 처리하지 않는다. `passed`는 실행·변경 범위
  검증이며 성능 향상 판정이나 accuracy가 아니다.
- dev와 dev-ko를 따로 집계한다. EGR@10, Complete@10, MRR의 H0/H1 값과 차이,
  각 문항의 지표별 좋아짐·나빠짐·동일 및 개수를 기록한다. BM25 후보 수와 빈 결과도 남긴다.
- dev-ko Hybrid EGR@10이 H0 Dense의 정확한 값 `0.26666666666666666` 이상인지
  별도로 확인한다. 반올림된 0.267을 판정 기준으로 쓰지 않는다. 미달해도 결과를 기록하고
  Dense 강제 선택이나 추가 검색 조정을 하지 않는다. 버그 수정은 일반 성능 채택 기준과 구분한다.

Recovery는 동일한 H1 retriever를 연결한 Literature Tool로 재검색한다. 빈 BM25는
빈 문헌 검색 전체를 의미하지 않는다. Dense 결과를 기존 evidence_fusion에 전달한다.
공개 합성 테스트는 실제 H1 BM25·RRF·Hybrid·Literature Tool·LangGraph 재검색을
연결하고 근거 결합, 재검사, 답변 준비 또는 제한된 재시도 후 보류를 검증한다.
Recovery 질의 생성과 `retry_evidence_fusion_v1` 정책·근거 상한은 변경하지 않는다.

단위 → 통합 → 공개 CI → 로컬 전체 테스트를 실행한다. 실제 모델을 사용한
개발 측정은 합성 테스트와 구분해 보고한다. 기존 H0, dev/dev-ko, corpus,
Gold와 과거 보고서의 해시를 보존한다. 실제 별도 검증용 평가 질문·Gold는 열람하거나 실행하지 않는다.
질문 분리, 약어·숫자 정규화, 영어 검색어 변환, 청킹, 후보 깊이 변경은 후속 단계다.
PR과 GitHub Actions 결과를 보고한 뒤 멈추며 다음 단계는 시작하지 않는다.
