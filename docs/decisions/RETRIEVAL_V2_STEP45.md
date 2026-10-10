# Step 4·5 이전 결정과 사전 기준

결정일: 2026-10-10. PR #6 결과를 확인한 뒤 사용자 승인에 따라 기록한다.

## Step 2/3의 역사적 결과와 사후 결정

- 사전 기준에 따른 결과는 **Step 2 채택, Step 3a·3b 제외**다. 기존 결과와 결정 기록은 그대로 보존한다.
- 사전 기준은 dev를 주 기준으로 삼았다. 그러나 언어 변환의 목적은 한국어 질문(dev-ko)이었으며
  실제 사용자 질문도 한국어다. 기준이 변경의 목적과 맞지 않았음을 인정한다.
- **이후 누적 기준선은 Step 3b로 한다. 이는 결과를 본 뒤의 사후 결정이다.**
  Step 3b가 과거 사전 기준을 통과했다고 다시 쓰지 않는다. 새 기준으로 Step 2/3 결과를 소급 채택하지 않는다.
- held-out 최종 평가는 H0 / H1 / Step 2 기반(과거 사전 기준 경로) /
  최종 V2(Step 3b + 이후 채택 변경)를 각각 1회 실행한다. Step 2 조건은 역사적 채택 경로로 별도 보존한다.
  최종 설정 확정 전 held-out 질문·Gold는 열람하거나 실행하지 않는다. 이번 작업은 최종 평가가 아니다.

## Step 4·5의 새 사전 채택 기준

Step 4·5 검색 결과를 보기 전에 고정한다.

1. dev + dev-ko 합산 36문항의 CompleteEvidence@10이 직전 채택 설정보다 감소하지 않는다.
2. 합산 문항별 EGR@10의 개선 수가 악화 수보다 많다.
3. dev와 dev-ko 각각의 지표와 문항별 변화는 모두 보고하되 개별 셋을 채택 조건으로 쓰지 않는다.

두 셋의 문항은 같은 의미를 공유하는 대응 문항이다. 합산은 36회 측정의 개발 기준이며
36개의 독립 질문이나 독립 일반화 성능으로 표현하지 않는다. 동일 판정 허용 오차는 기존 1e-12다.

## 고정한 실행 순서와 동일 변수

- 기준선 Step 3b는 이미 승인·동결된 번역 v2의 실제 영어 query를 Dense/BM25 모두에 사용한다.
  모델 호출·재생성·재번역은 없다. 원문 보완과 생성 실패 문항도 그대로 포함한다.
- Step 4: frozen parent를 수정하지 않고 별도 child representation을 만든다.
  문장 경계로 묶고 pinned MiniLM tokenizer의 특수 토큰 포함 길이를 110 이하로 제한한다.
  문장 하나가 초과하는 경우에만 tokenizer의 토큰 경계로 강제 분할한다(사용자 명시 승인).
  모든 조각은 같은 parent ID를 유지한다. 제목·섹션 metadata는 넣지 않는다.
- 강제 분할 문장 수, 강제 분할에서 생긴 child 수/전체 child 비율, child 토큰 수 분포를 기록한다.
  child ID, parent ID, parent 내 원문 문자 범위와 최선 child 검색 순위를 저장한다.
- Dense는 child 50개를 검색한다. parent는 그 소속 child 중 가장 앞선 순위에 따라 중복 제거해 정렬한다.
  RRF에는 이 parent 목록의 연속 순위를 넣으며 원래 최선 child 순위도 진단 자료로 남긴다.
  이는 기존 RRF의 순위 목록 계약을 유지하는 방식이며 child 중복으로 parent 기여를 늘리지 않는다.
- Step 4 BM25는 원래 parent 단위 후보 10개다. RRF Top-K 10, BM25 positive_only,
  tokenizer, embedding 모델·revision·정규화, corpus, frozen parent, query, Recovery·근거 결합은 유지한다.
- Step 4가 탈락하면 이후 기준은 Step 3b의 parent 검색이다. child 변경을 탈락 후 몰래 유지하지 않는다.
- Step 5는 10 → 20 → 50 순서로 Dense/BM25의 RRF 입력 parent 후보 상한만 바꾼다.
  매번 직전 채택 조건에 새 기준을 적용한다. 탈락 조건은 다음 비교 기준으로 삼지 않는다.
  child 경로가 채택됐다면 child 검색 풀은 계속 50개다. 중복 제거 후 parent가 부족해도 추가 검색하지 않는다.
  실제 Dense parent 수와 BM25 후보 수를 문항별 기록한다. parent 경로라면 Dense parent를 해당 깊이만큼 검색한다.
- depth 10이 직전 설정과 같으면 동일 결과로 기록하고 개선 수 > 악화 수 조건에 의해 변경으로 채택하지 않는다.
  이 세 깊이 외 미세 조정이나 결과에 맞춘 child 규칙 변경은 하지 않는다.
- 서비스 기본값은 legacy/H0다. pgvector 운영 경로 확인과 V2 최종 확정은 후속 단계로 남긴다.

## 보존과 검증

기존 corpus·평가 질문·Gold·H0/H1·Step 2/3 결과와 query 동결본은 덮어쓰지 않는다.
새 representation과 새 평가 결과는 새 version으로 저장한다. 검색 순위를 저장한 뒤에만 Gold를 평가에 사용한다.
합성 테스트는 문장 경계·토큰 상한·초과 문장 처리·parent 중복 제거·RRF·깊이·기본값 회귀를 검증한다.
Step 3b 순위/점수/지표를 재현하고 BM25 parent 결과·query와 고정 모델 설정의 불변을 확인한다.
단위·통합 → 공개 CI → 로컬 전체 테스트 후 관련 파일만 commit하고 task branch에 push한다.
작업 branch는 `feature/retrieval-child-depth`, master 대상 PR과 GitHub Actions 확인 후 멈춘다.
