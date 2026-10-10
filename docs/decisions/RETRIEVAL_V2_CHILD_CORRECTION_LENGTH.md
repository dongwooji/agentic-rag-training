# Step 4 구현 보정과 Dense 입력 길이 사전 결정

결정일: 2026-10-10. 아래 조건은 이번 검색 결과를 보기 전에 사용자 지시에 따라 고정한다.

## 구현 보정의 성격과 보존

사용자의 원래 지시는 고유 parent가 후보 수 10개에 찰 때까지 child를 가져오는 것이었다.
이전 구현은 child 50개에서 나온 parent 20~47개를 모두 RRF에 넣었다.
**이는 명세와 다른 구현이며, 이번 Step 4 실행은 구현 보정이다.**
이전 Step 4/5 결정·config·child·평가·보고서는 역사적 기록으로 보존한다.
이전 결과로 parent-child 기법 자체가 부적합하다고 판단하지 않는다.
이 기록은 기존 결정 기록의 해석을 정정하는 새 기록이며 기존 동결 파일을 덮어쓰지 않는다.

## 세 조건과 불변 항목

1. `child_corrected`: 기존 `retrieval_child_v1`의 child와 embedding을 그대로 사용한다.
   최고 child 순서로 고유 parent 10개를 채우고 상위 10개만 기존 RRF에 전달한다.
   첫 child 검색 요청은 50개다. 10개 parent가 부족하면 50개씩 요청 깊이를 늘린다.
   전체 child 소진 시에만 부족한 결과를 허용한다. 상위 10번째 고유 parent에서 소비를 멈춘다.
   반환 parent 연속 순위, 최고 child 순위, 요청 깊이·실제 조회/소비 child 수·충족 여부를 기록한다.
2. `parent256`: 같은 pinned MiniLM, parent 단위, max_seq_length 256만 변경한다.
3. `parent512`: 같은 pinned MiniLM, parent 단위, max_seq_length 512만 변경한다.

256·512 이외 새 길이는 시도하지 않는다. 128은 Step 3b 재현·잘림 진단의 기존 값이다.
모델 구조의 position capacity를 검사한 뒤 상한을 설정한다. 두 조건 모두 새 parent embedding version을
만들고 기존 embedding은 수정하지 않는다. 문헌 및 질문 embedding에 동일한 encoder 상한을 적용한다.
문헌 길이는 동일 tokenizer로 특수 토큰을 포함해 계산하고 길이별 잘린 parent 수·비율 및 토큰 보존 비율을 기록한다.

세 조건 모두 Step 3b의 동결된 영어 실제 검색어를 Dense/BM25에 사용한다.
모델 가중치/revision·tokenizer·정규화·precision·batch size·corpus·parent·BM25 positive_only·RRF·
Dense parent 후보 10·BM25 parent 후보 10·최종 Top-10·Recovery·근거 결합을 유지한다.
child 보정과 길이 변경을 결합하지 않는다. 추가 LLM 생성·번역 호출은 없다.
서비스 기본값 legacy/H0는 유지하며 pgvector 운영 전환은 이번 범위가 아니다.

## 비교·채택·선택

- 세 조건은 **각각 Step 3b와 비교한다. 누적하지 않는다.**
- dev+dev-ko 합산 36회 측정 CompleteEvidence@10 감소 없음 AND 문항별 EGR@10 개선 수 > 악화 수.
- dev/dev-ko 개별 지표와 문항별 변화는 보고만 한다.
- 통과한 조건 중 합산 CE@10 최대 → 같으면 합산 EGR@10 최대 → 같으면 512 > 256 > child 순서로 선택한다.
- 동일 및 동률 허용 오차는 기존 1e-12다. 통과 조건이 없으면 V2 기준은 Step 3b다.
- 두 셋은 대응 질문이므로 36개의 독립 질문이나 독립 일반화 성능으로 설명하지 않는다.

사전 조건/config/검증 코드의 소스 사본과 hash를 검색 전에 저장한다.
Gold는 검색 순위 저장 후 지표 계산에만 사용한다. held-out 질문·Gold는 열람/실행하지 않는다.
결과 후 재시도·규칙/기준 튜닝은 하지 않는다. 새 결과는 새 version으로 동결한다.

## 향후 과제만 기록

- BGE-M3 등 긴 입력 embedding 모델 비교: 이번에는 모델 로드·embedding·평가를 하지 않는다.
- Child 벡터를 결합한 parent 벡터 검색: 이번에는 구현·평가하지 않는다.

단위·통합·공개 CI·로컬 전체 테스트, 관련 파일 지정 commit, task branch `push -u`, master 대상 PR,
GitHub Actions 확인 후 보고하고 멈춘다. 다음 단계와 최종 held-out 평가는 시작하지 않는다.
