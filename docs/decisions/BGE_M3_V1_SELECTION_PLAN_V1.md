# BGE-M3 모델 선택 경로 A와 검색 전 준비 v1

결정일: 2026-10-14 (Asia/Seoul). 사용자 승인에 따라 모델 선택은 동결 corpus v1과 기존 dev/dev-ko Gold를 사용한다. 기존 corpus v2 remap 규칙·검토표·결과는 보존한다. v2에서 원 Gold 논문 부재를 다른 논문으로 채우거나 기존 Gold를 수정하지 않는다.

## 이번 승인 범위와 멈춤 조건

1. v1 22편과 v2 26편을 DOI/PMID/제목으로 비교해 겹치는 논문, v1에만 있는 논문, v2에만 있는 논문의 목록과 편수를 기록한다. 이전 검토표의 같은 ID 10개 원문 일치 및 변경 ID 14개 중 동일 원문 후보 1개·원 논문 부재 13개 결과는 보존하며 새 remapped Gold는 만들거나 freeze하지 않는다.
2. 동결 v1 parent 488개를 BGE-M3로 임베딩해 기존 MiniLM/v2 BGE-M3 벡터와 분리한 새 경로에 저장한다. model revision·실제 설정·시간·메모리를 기록한다.

2단계를 끝내면 결과와 무결성을 보고하고 검색 전에 멈춘다. dev 검색·채택 평가와 held-out 진단은 이번 승인 범위에 포함하지 않는다. 운영 corpus·서비스 설정·DB는 다루지 않는다. 이후 v1과 v2를 합친 별도 corpus version을 검토할 예정이라는 계획만 남기며 이번에는 합친 corpus를 만들지 않는다.

## 논문 동일성 비교 규칙

- corpus의 실제 built 논문을 비교한다. v1 processed papers/chunks와 manifest, v2 build manifest/chunks의 논문 수·ID·metadata를 대조한다.
- DOI는 casefold하고 doi.org/dx.doi.org URL·doi: prefix와 주변 whitespace를 제거한다. PMID는 주변 whitespace를 제거하고 정확한 ID로 비교한다.
- 제목은 Unicode NFKC·casefold·whitespace 정규화 후 비교한다. 문자/숫자 단어가 동일한 punctuation 차이는 별도로 기록한다. 부분 제목·주제 유사성·embedding similarity로 동일 논문이라고 판정하지 않는다.
- 같은 DOI 또는 PMID를 우선 사용한다. 제목만 일치하는 경우 year·기타 식별자의 모순과 1:1 대응 여부를 확인하며, 충돌/복수 후보는 ambiguous로 남기고 자동 합치지 않는다. 같은 stable ID인데 제목만 바뀌어도 원문 metadata 둘 다 보존하고 차이를 기록한다.
- 표에는 양쪽 paper ID, 원문 제목, DOI, PMID, PMCID, year와 일치 근거를 남긴다. 원문 metadata는 변경하지 않는다. 합친 corpus의 selection/build 결정을 대신하지 않는다.

## embedding 설정

- BAAI/bge-m3 revision: 5617a9f61b028005a4858fdac845db406aefb181. 이전 v2 준비에서 검증한 같은 공개 모델 파일을 사용하며 파일 SHA-256을 다시 확인한다.
- CPU, float32, CLS pooling, L2 정규화, max_seq_length=8192, batch_size=1, intra-op 4 / inter-op 1 threads. 결과를 보고 설정을 바꾸지 않는다.
- 입력은 v1 chunk 원문 text만 사용한다. metadata/prompt/질문/Gold를 포함하지 않는다. 모든 입력의 특수 토큰 포함 길이와 원문 hash를 검증하고 잘림 유무를 기록한다.
- 새 경로: data/literature/embeddings/bge_m3_v1_parent_v1/. parent ID와 원문 순서, float32 (488, 1024), 유한값·정규화를 파일 재로드로 검증한다.
- 모델 로드와 corpus 처리 시간을 분리한다. process RSS/private commit, Windows lifetime peak, 시스템 가용 RAM을 기록한다. 기존 서버/다른 프로세스를 종료하지 않는다.
- CPU 질문 지연은 이전 36입력의 고정 조건 측정(평균 0.265006초, p95 0.365894초)을 보존한다. 이번에는 평가 질문을 로드하거나 재측정하지 않는다. 이는 warm model·원 질문의 지연이며 검색 품질/실제 번역 검색어 지연과 구분한다.

## 사용자 확인 후 진행할 3~4단계

3. 동일 v1 corpus·parent·동결 Final V2 영어 실제 검색어로 기존 MiniLM 128과 BGE-M3를 비교한다. 검색어 생성/번역을 재호출하지 않는다. Dense10, BM25 positive_only10, equal-weight RRF k=60, 최종 Top-10, child/reranker 없음. 두 조건은 모델과 해당 encoder 설정만 다르며 기존 Gold·검색어·BM25/RRF는 유지한다. 모든 순위를 저장한 뒤 기존 Gold로 평가한다.
4. 채택 기준: dev/dev-ko 36입력 CE@10 감소 없음 AND 문항별 EGR@10 개선 수 > 악화 수 AND 질문 embedding CPU 평균 1초 이하. dev 18개 의미 질문과 대응 dev-ko 18입력을 독립적인 36개 질문으로 표현하지 않는다. 개발 기준 결정이며 독립 일반화 성능이라고 표현하지 않는다.

## 채택 결정 후 별도 진행할 5단계

기존 held-out v2의 근거 있는 20문항을 진단용으로 사용한다. MiniLM과 BGE-M3를 같은 동결 corpus v2, 같은 동결 Final V2 query, parent·BM25·후보 깊이·RRF·Top-K 조건에서 **각 1회** 검색해 결과를 나란히 보고한다. 각 모델의 pinned revision과 입력 길이·차원 등 encoder 차이를 명시한다. 기존 MiniLM 평가 결과는 역사적 기록으로 보존하고 이번 진단은 별도 run으로 기록한다.

이 20문항은 이미 관찰한 자료이므로 독립 검증이라고 부르지 않는다. 진단 결과에 따라 채택을 반복하거나 코드·prompt·Gold·검색어를 바꾸지 않는다. 이번에는 held-out 질문·Gold를 열거나 검색하지 않는다. 운영 전환과 corpus 통합은 별도 작업으로 남긴다.
