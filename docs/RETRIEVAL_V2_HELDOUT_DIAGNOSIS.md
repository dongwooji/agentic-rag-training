# Held-out 동결 순위 진단

진단일: 2026-10-12. PR #10의 최종 평가를 변경하지 않는 별도 진단이다.
새 검색·embedding·LLM·DB 호출은 모두 0회. 설정·Gold·검색어·prompt·검증 규칙·PR #10 파일을 변경하지 않았다.

## 실행과 해석 범위

`python scripts/diagnose_retrieval_heldout.py --output reports/diagnostics/retrieval_v2_heldout_diagnosis_v1`
실행기는 기존 순위와 Gold를 읽고 재채점하는 평가 전용 코드다. 모델·검색기·provider/secret loader 경로는 없다.
기존 결과의 재실행이 아닌 동결 순위 재채점이며, v2.1 재채점은 PR #10과 정확히 일치한다.
진단을 근거로 설정이나 Gold를 변경하지 않는다. 기존 결과 문서는 그대로 보존한다.

## 1. 정합성과 코퍼스 크기

필수 근거 71개, chunk 연결 71개, 고유 required chunk ID 61개가 모두 인덱스에 있다. 누락 ID: [].
인덱스 parent ID 집합과 corpus v2 chunk ID 집합이 같고 저장된 embedding matrix 행 수도 일치한다.

| corpus | 논문 문서 수 | parent chunk 수 | chunk가 있는 논문 수 |
|---|---:|---:|---:|
| v1 | 22 | 488 | 22 |
| v2 | 26 | 683 | 26 |

코퍼스 크기 비교이며 corpus v1에서 새 평가를 실행하지 않았다.

## 2. Gold 버전 영향

동일 20문항·동일 네 설정의 동결 순위를 각각 원래 Gold와 최종 Gold로 채점했다.
| 설정 | v1 EGR@10 | v2.1 EGR@10 | v1 CE@10 | v2.1 CE@10 |
|---|---:|---:|---:|---:|
| H0 | 0.212500 | 0.188333 | 0.050000 | 0.050000 |
| H1 | 0.225000 | 0.200833 | 0.050000 | 0.050000 |
| step2 | 0.225000 | 0.200833 | 0.050000 | 0.050000 |
| final_v2 | 0.187500 | 0.208333 | 0.050000 | 0.050000 |

원래 required 57개 → 최종 71개. 실제 추가는 14개다.
이전 frozen snapshot(heldout_v2_v1_1)에 추가된 13개와 최종 추가 ['HOV2-015-E05']를 구분한다.
요청의 13개 집합은 이전 snapshot에 존재하는 추가 required ID와 최종 Gold의 교집합으로 정의했다. 특정 ID를 임의로 빼지 않았다.
최종 추가 HOV2-015-E05는 기존 최종 Gold 수정 기록의 restored_proposal과 일치한다.
아래는 전체 group 수를 분모로 한 회수율이며 EGR의 문항별 macro 평균과 구분한다.

| 설정 | 이전 추가 13개 회수 | 최종 추가 1개 회수 | 전체 추가 14개 회수 |
|---|---|---|---|
| H0 | 2/13 (15.38%) | 0/1 (0.00%) | 2/14 (14.29%) |
| H1 | 2/13 (15.38%) | 0/1 (0.00%) | 2/14 (14.29%) |
| step2 | 2/13 (15.38%) | 0/1 (0.00%) | 2/14 (14.29%) |
| final_v2 | 5/13 (38.46%) | 0/1 (0.00%) | 5/14 (35.71%) |

Gold 버전 차이는 추가 근거 수뿐 아니라 의미·chunk 연결 수정도 포함한다. 수 증가만의 인과 효과로 해석하지 않는다.
원래 Gold에서도 CE는 1/20(5%)이므로 낮은 CE를 Gold 강화만으로 설명할 수 없다. 설정 채택 판단은 하지 않는다.

## 3. Final V2 문항별 진단

| 문항 | task type | required | Top-10 회수 | 누락 |
|---|---|---:|---:|---:|
| HOV2-001 | literature_only | 4 | 0 | 4 |
| HOV2-002 | literature_only | 4 | 4 | 0 |
| HOV2-003 | literature_only | 5 | 2 | 3 |
| HOV2-004 | hybrid | 4 | 0 | 4 |
| HOV2-005 | literature_only | 8 | 0 | 8 |
| HOV2-006 | literature_only | 3 | 0 | 3 |
| HOV2-007 | literature_only | 3 | 2 | 1 |
| HOV2-008 | literature_only | 4 | 1 | 3 |
| HOV2-009 | literature_only | 4 | 1 | 3 |
| HOV2-010 | literature_only | 5 | 1 | 4 |
| HOV2-011 | literature_only | 4 | 1 | 3 |
| HOV2-012 | literature_only | 2 | 0 | 2 |
| HOV2-013 | literature_only | 4 | 1 | 3 |
| HOV2-014 | hybrid | 2 | 0 | 2 |
| HOV2-015 | literature_only | 5 | 2 | 3 |
| HOV2-016 | hybrid | 2 | 0 | 2 |
| HOV2-017 | literature_only | 2 | 1 | 1 |
| HOV2-018 | hybrid | 2 | 0 | 2 |
| HOV2-020 | literature_only | 2 | 0 | 2 |
| HOV2-021 | literature_only | 2 | 0 | 2 |

합계 71개 중 16개 회수, 55개 누락.
동일 chunk가 여러 group을 지원할 수 있다. group 수를 고유 chunk 수로 읽지 않는다.

| 누락 문단 유형 | group 수 |
|---|---:|
| discussion | 6 |
| limitations | 3 |
| methods | 14 |
| results | 32 |

문단 유형은 source.section의 명시 heading을 가장 깊은 하위 heading부터 규칙으로 분류했다.
예: Discussion > Limitations는 limitations. 원 section·paragraph ID·group별 상태는 진단 JSON에 모두 보존한다.
본문 의미를 LLM으로 판정한 분류가 아니다. 알려진 heading이 없으면 other로 둔다.

### Dense50+BM25_50 후보 포함 여부

held-out 저장본은 Dense10과 RRF Top-10만 저장했다. BM25 index metadata는 검색 순위가 아니며 깊이 50 후보를 복원할 수 없다.
dev의 깊이 50 후보는 질문과 코퍼스가 달라 held-out에 대신 사용하지 않는다. 새 검색이 금지돼 깊이 50 진단 전체는 측정 불가다.
누락 group 중 3개는 저장된 Dense10/노출된 BM25 prefix 안에 있어 깊이 50 pool 안임을 확인할 수 있다.
나머지 52개는 **판정 불가**다. 후보 밖 수나 CompleteEvidence 후보 포함 상한은 보고하지 않는다.
이는 dev의 a/b/c 완전 분류와 같지 않다. 확인 가능한 포함 하한만 보고하며 unknown을 후보 밖이나 corpus coverage 부족으로 처리하지 않는다.

## 4. 질문 언어 사후 기술 통계

20문항 중 영문 표기(약어 포함)가 있는 문항 17개, Latin 표기 없는 순수 한국어 3개.
보조 정의: RIR/RPE/1RM/e1RM 및 명시 단위를 허용하면 그 밖의 영어 표기가 있는 문항 13개, 없는 문항 7개.
허용 표기: ['1rm', 'cm', 'e1rm', 'g', 'kg', 'km', 'lb', 'lbs', 'm', 'min', 'ml', 'mm', 'rir', 'rpe', 's', 'sec'].
원 질문만 정규식으로 집계했다. 숫자·구두점은 영어로 세지 않는다. 이는 표기 통계이며 전문용어 의미에 대한 완전한 분류는 아니다.
사후 기술 통계로만 보고하며 query 선택·채택 기준·생성/번역 검증에 연결하지 않는다.

| 문항 | 영문 표기 |
|---|---|
| HOV2-001 | failure, RIR |
| HOV2-002 | RTEV, RTUV, 1RM, MRI |
| HOV2-003 | sdRM, failure, RIR |
| HOV2-004 | session-best, e1RM |
| HOV2-005 | 1RM |
| HOV2-006 | 1RM |
| HOV2-007 | lengthened, partial, full, ROM, lat, pulldown, 1RM |
| HOV2-008 | 없음 |
| HOV2-009 | 없음 |
| HOV2-010 | deload |
| HOV2-011 | taper |
| HOV2-012 | lengthened, partial, partial, ROM, lengthened, partial |
| HOV2-013 | RIR, velocity, loss, RIR, 1RM |
| HOV2-014 | RIR |
| HOV2-015 | 없음 |
| HOV2-016 | volume, load, tonnage, volume |
| HOV2-017 | full, ROM, 1RM |
| HOV2-018 | kg |
| HOV2-020 | RIR, volume, autoregulation |
| HOV2-021 | volume, network, meta-analysis |

## 보존·검증

새 진단 manifest SHA-256: `88737688e8dea0fab815ad371ae1299fcb3205a16488773635e26633160c2f9d`.
보호 파일 1334개 SHA-256 변경 0. PR #10 설정·결과·소스·Gold·query 동결본을 보존했다.
테스트: {'unit': {'tests': 17, 'failures': 0, 'errors': 0, 'skipped': 0}, 'public': {'tests': 822, 'failures': 0, 'errors': 0, 'skipped': 0}, 'full': {'tests': 903, 'failures': 0, 'errors': 0, 'skipped': 0}}. 로컬에는 commit에서 제외한 사용자 JATS 테스트 19개가 포함된다.
실제 provider·DB·모델 호출은 0회. 테스트는 invented fixture이며 실제 검색 성능 실험이 아니다.
branch: eval/retrieval-v2-heldout-diagnosis. PR #10이 아직 열려 있어 그 commit 위에 진단만 추가한다.
master 대상 PR은 PR #10 선행 변경을 포함한다. #10 병합 후 브랜치 동기화로 진단만의 diff를 확인해야 한다. #10은 수정·병합하지 않는다.
