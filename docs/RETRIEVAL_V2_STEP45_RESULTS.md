# Retrieval V2 Step 4·5 결과

평가일: 2026-10-10. 새 합산 기준은 검색 전에 [결정 기록](decisions/RETRIEVAL_V2_STEP45.md)에 고정했다.

**Step 4와 Step 5의 변경은 모두 제외됐다. 이후 누적 기준선은 Step 3b다.**
Step 3b 선택은 Step 2/3 결과를 확인한 뒤 사용자 승인으로 정한 사후 결정이며, 역사적 사전 기준 결과는 Step 2 채택 / Step 3a·3b 제외로 보존한다.

Step 4가 제외됐으므로 Step 5는 기존 parent 표현으로 실행했다. 10 → 20 → 50을 순서대로 비교했고, 탈락 조건을 다음 기준선으로 삼지 않았다. 모두 Step 3b와 비교한 결과다.

## 합산 채택 결과

채택 조건: dev+dev-ko 36회 측정의 CE@10 감소 없음, 문항별 EGR@10 개선 수 > 악화 수. 개별 언어 결과는 보고용이다. 동일 판정 허용 오차는 1e-12다.

| 조건 | EGR@10 | CE@10 | MRR | EGR 개선/악화/동일 | CE 감소 없음 | 개선 > 악화 | 채택 |
|---|---:|---:|---:|---|---|---|---|
| Step 3b 기준선 | 0.752778 | 0.666667 | 0.432011 | — | — | — | 기준선 |
| Step 4 child | 0.609259 | 0.527778 | 0.302392 | 0/7/29 | 미충족 | 미충족 | 제외 |
| Step 5 parent 10 | 0.752778 | 0.666667 | 0.432011 | 0/0/36 | 충족 | 미충족 | 제외 |
| Step 5 parent 20 | 0.715741 | 0.555556 | 0.483333 | 1/5/30 | 미충족 | 미충족 | 제외 |
| Step 5 parent 50 | 0.787500 | 0.666667 | 0.494709 | 4/4/28 | 충족 | 미충족 | 제외 |

깊이 10은 기준선과 동일해 변경으로 채택하지 않았다. 깊이 50은 합산 EGR과 MRR이 높아졌지만 개선 4건 = 악화 4건이므로 사전 기준에 따라 제외했다. 결과 확인 후 기준이나 child 규칙을 변경하지 않았다.

## dev / dev-ko 및 문항 종류별 지표

| 조건 | 평가셋 | 문항 수 | EGR@10 | CE@10 | MRR | ChunkRecall@10 | EGR 개선/악화/동일 |
|---|---|---:|---:|---:|---:|---:|---|
| Step 3b 기준선 | dev | 18 | 0.697222 | 0.611111 | 0.383333 | 0.583333 | — |
| Step 3b 기준선 | dev-ko | 18 | 0.808333 | 0.722222 | 0.480688 | 0.666667 | — |
| Step 4 child | dev | 18 | 0.581481 | 0.500000 | 0.295833 | 0.527778 | 0/3/15 |
| Step 4 child | dev-ko | 18 | 0.637037 | 0.555556 | 0.308951 | 0.555556 | 0/4/14 |
| Step 5 parent 10 | dev | 18 | 0.697222 | 0.611111 | 0.383333 | 0.583333 | 0/0/18 |
| Step 5 parent 10 | dev-ko | 18 | 0.808333 | 0.722222 | 0.480688 | 0.666667 | 0/0/18 |
| Step 5 parent 20 | dev | 18 | 0.697222 | 0.555556 | 0.418519 | 0.592593 | 1/2/15 |
| Step 5 parent 20 | dev-ko | 18 | 0.734259 | 0.555556 | 0.548148 | 0.592593 | 0/3/15 |
| Step 5 parent 50 | dev | 18 | 0.752778 | 0.611111 | 0.426455 | 0.620370 | 2/2/14 |
| Step 5 parent 50 | dev-ko | 18 | 0.822222 | 0.722222 | 0.562963 | 0.694444 | 2/2/14 |

| 조건 | 평가셋 | 종류 | 문항 수 | EGR@10 | CE@10 | MRR |
|---|---|---|---:|---:|---:|---:|
| Step 3b 기준선 | dev | hybrid | 8 | 0.375000 | 0.375000 | 0.108333 |
| Step 3b 기준선 | dev | literature_only | 10 | 0.955000 | 0.800000 | 0.603333 |
| Step 3b 기준선 | dev-ko | hybrid | 8 | 0.625000 | 0.625000 | 0.334524 |
| Step 3b 기준선 | dev-ko | literature_only | 10 | 0.955000 | 0.800000 | 0.597619 |
| Step 4 child | dev | hybrid | 8 | 0.250000 | 0.250000 | 0.046875 |
| Step 4 child | dev | literature_only | 10 | 0.846667 | 0.700000 | 0.495000 |
| Step 4 child | dev-ko | hybrid | 8 | 0.375000 | 0.375000 | 0.101389 |
| Step 4 child | dev-ko | literature_only | 10 | 0.846667 | 0.700000 | 0.475000 |
| Step 5 parent 10 | dev | hybrid | 8 | 0.375000 | 0.375000 | 0.108333 |
| Step 5 parent 10 | dev | literature_only | 10 | 0.955000 | 0.800000 | 0.603333 |
| Step 5 parent 10 | dev-ko | hybrid | 8 | 0.625000 | 0.625000 | 0.334524 |
| Step 5 parent 10 | dev-ko | literature_only | 10 | 0.955000 | 0.800000 | 0.597619 |
| Step 5 parent 20 | dev | hybrid | 8 | 0.500000 | 0.500000 | 0.150000 |
| Step 5 parent 20 | dev | literature_only | 10 | 0.855000 | 0.600000 | 0.633333 |
| Step 5 parent 20 | dev-ko | hybrid | 8 | 0.625000 | 0.625000 | 0.379167 |
| Step 5 parent 20 | dev-ko | literature_only | 10 | 0.821667 | 0.500000 | 0.683333 |
| Step 5 parent 50 | dev | hybrid | 8 | 0.625000 | 0.625000 | 0.167857 |
| Step 5 parent 50 | dev | literature_only | 10 | 0.855000 | 0.600000 | 0.633333 |
| Step 5 parent 50 | dev-ko | hybrid | 8 | 0.750000 | 0.750000 | 0.412500 |
| Step 5 parent 50 | dev-ko | literature_only | 10 | 0.880000 | 0.700000 | 0.683333 |

## Child 구조와 실제 후보 수

동결 parent 488개는 그대로 보존하고 문장 경계 기반 child 2,608개를 별도 version `retrieval_child_v1`으로 만들었다. MiniLM의 정확한 pinned tokenizer를 사용했다. 110토큰을 넘는 문장만 토큰 경계에서 나누고 모든 조각은 원 parent ID를 유지한다.

- 문장 4,579개 중 강제 분할 112개(2.45%).
- 강제 분할에서 생긴 child 250개 / 전체 child 2,608개 = **9.59%**.
- 특수 토큰 포함 child 길이: 최소 4, 평균 80.064, 중앙값 85, p95 110, 최대 110.
- 기존 parent 원문의 문자 범위와 child 원문 일치, parent membership, 110토큰 상한을 검색 전에 재검증했다.

| Child 토큰 수 | 개수 | 비율 |
|---|---:|---:|
| 1–20 | 59 | 2.26% |
| 21–40 | 156 | 5.98% |
| 41–60 | 302 | 11.58% |
| 61–80 | 578 | 22.16% |
| 81–100 | 932 | 35.74% |
| 101–110 | 581 | 22.28% |

토큰별 전체 분포는 별도 child 동결본 `stats.json`에 기록했다. 긴 원문 문장을 세는 중 tokenizer가 모델 길이 경고를 출력했지만, 실제 embedding 입력 child는 전부 110토큰 이하이고 encoder 입력 상한은 128이다.

Step 4에서는 child 50개를 검색하고 parent마다 최고 순위 child로 중복 제거했다. 기존 RRF 계약에 따라 연속 parent 순위를 사용하며 원래 최고 child 순위도 따로 기록했다. BM25는 parent 10개 그대로다. Step 5는 parent Dense/BM25 후보 상한만 변경하고 최종 RRF는 계속 Top-10이다.

| 조건 | 평가셋 | 실제 Dense parent 최소/평균/최대 | 실제 BM25 parent 최소/평균/최대 |
|---|---|---|---|
| Step 3b 기준선 | dev | 10/10.00/10 | 10/10.00/10 |
| Step 3b 기준선 | dev-ko | 10/10.00/10 | 10/10.00/10 |
| Step 4 child | dev | 20/34.39/47 | 10/10.00/10 |
| Step 4 child | dev-ko | 22/34.67/45 | 10/10.00/10 |
| Step 5 parent 10 | dev | 10/10.00/10 | 10/10.00/10 |
| Step 5 parent 10 | dev-ko | 10/10.00/10 | 10/10.00/10 |
| Step 5 parent 20 | dev | 20/20.00/20 | 20/20.00/20 |
| Step 5 parent 20 | dev-ko | 20/20.00/20 | 20/20.00/20 |
| Step 5 parent 50 | dev | 50/50.00/50 | 50/50.00/50 |
| Step 5 parent 50 | dev-ko | 50/50.00/50 | 50/50.00/50 |

Child 풀의 parent 수가 50보다 적어도 추가 검색으로 채우지 않았다. 문항별 실제 후보 수, child 50개 순위, 최고 child ↔ parent 대응은 평가 동결본의 `source_rankings.json` / `child_mappings.json`에 보존한다.

## 문항별 변화

각 셀은 기준선 대비 **EGR / CE / RR** 변화(↑ 개선, ↓ 악화, = 동일)다. 절대값·차이·ChunkRecall 변화와 모든 순위는 동결된 `result.json`과 조건별 검색 산출물에 있다. 각 비교 기준은 Step 3b다.

### dev

| 문항 | Step 4 | Step 5 parent 10 | Step 5 parent 20 | Step 5 parent 50 |
|---|---|---|---|---|
| LIT-001 | = / = / ↓ | = / = / = | ↓ / ↓ / = | ↓ / ↓ / = |
| LIT-002 | = / = / = | = / = / = | = / = / = | = / = / = |
| LIT-003 | ↓ / = / ↓ | = / = / = | = / = / ↑ | = / = / ↑ |
| LIT-004 | = / = / = | = / = / = | = / = / = | ↓ / ↓ / = |
| LIT-005 | = / = / ↑ | = / = / = | = / = / = | = / = / = |
| LIT-006 | = / = / = | = / = / = | = / = / = | = / = / = |
| LIT-007 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-008 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-009 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-010 | ↓ / ↓ / ↓ | = / = / = | ↓ / ↓ / = | = / = / = |
| HYB-001 | = / = / = | = / = / = | = / = / = | = / = / = |
| HYB-002 | = / = / = | = / = / = | = / = / = | = / = / = |
| HYB-003 | = / = / = | = / = / = | = / = / = | ↑ / ↑ / ↑ |
| HYB-004 | = / = / = | = / = / = | = / = / = | = / = / = |
| HYB-005 | ↓ / ↓ / ↓ | = / = / = | = / = / ↑ | = / = / ↑ |
| HYB-006 | = / = / = | = / = / = | ↑ / ↑ / ↑ | ↑ / ↑ / ↑ |
| HYB-007 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| HYB-008 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
### dev-ko

| 문항 | Step 4 | Step 5 parent 10 | Step 5 parent 20 | Step 5 parent 50 |
|---|---|---|---|---|
| LIT-001 | = / = / ↓ | = / = / = | ↓ / ↓ / = | ↓ / ↓ / = |
| LIT-002 | = / = / = | = / = / = | = / = / = | = / = / = |
| LIT-003 | ↓ / = / ↓ | = / = / = | = / = / ↑ | ↑ / ↑ / ↑ |
| LIT-004 | = / = / = | = / = / = | ↓ / ↓ / = | ↓ / ↓ / = |
| LIT-005 | = / = / ↑ | = / = / = | = / = / ↑ | = / = / ↑ |
| LIT-006 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-007 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-008 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| LIT-009 | = / = / ↑ | = / = / = | = / = / = | = / = / = |
| LIT-010 | ↓ / ↓ / ↓ | = / = / = | ↓ / ↓ / = | = / = / = |
| HYB-001 | = / = / = | = / = / = | = / = / = | = / = / = |
| HYB-002 | = / = / = | = / = / = | = / = / = | = / = / = |
| HYB-003 | ↓ / ↓ / ↓ | = / = / = | = / = / = | = / = / = |
| HYB-004 | = / = / = | = / = / = | = / = / ↑ | = / = / ↑ |
| HYB-005 | ↓ / ↓ / ↓ | = / = / = | = / = / = | = / = / ↑ |
| HYB-006 | = / = / = | = / = / = | = / = / = | ↑ / ↑ / ↑ |
| HYB-007 | = / = / ↓ | = / = / = | = / = / = | = / = / = |
| HYB-008 | = / = / ↓ | = / = / = | = / = / ↑ | = / = / ↑ |

## 동결·검증·실행 범위

- Step 3b의 dev/dev-ko 검색 순위·점수·지표 재현 성공. Step 4의 BM25 parent 순위·점수 동일 확인.
- 기존 보호 대상 405개 파일의 SHA-256 동일 확인. 생성 v1, 번역 v1/v2, 과거 Step 2/3 평가, 새 child/평가 동결본의 모든 manifest 산출물 해시 검증 성공.
- 관련 단위·통합 테스트 123 passed. 공개 CI 명령 로컬 실행 739 passed / 81 deselected. 로컬 전체 820 passed. 기존 Starlette/AnyIO DeprecationWarning 1건.
- 공개 테스트는 합성 tokenizer·검색·Tool 구성으로 실행하고, 로컬 연구 평가는 실제 pinned MiniLM 및 frozen 문헌·질문·Gold를 사용했다. Gold는 각 조건의 순위 저장 후 지표 계산에만 썼다.
- 로컬 테스트 수에는 기존 사용자 미추적 JATS 테스트 19개가 포함된다. 해당 파일은 이 PR에 포함하지 않는다. GitHub Actions는 저장소에 commit된 공개 테스트 전체를 실행한다.
- 이번 작업 LLM 호출 0회 / 토큰 0 / 비용 $0 / 실패 호출 0. DB·pgvector 호출 없음. API 요청 없음. `.env.local.json`은 읽지 않았다.
- 서비스 기본값 legacy/H0, BM25 positive_only 정책, 모델/revision, frozen parent/corpus/query, RRF, Recovery·근거 결합은 변경하지 않았다. 새 설정을 명시적으로 선택하는 실험 경로만 추가했다.
- pgvector child 설정은 parent 테이블로 잘못 실행하지 않도록 DB/모델 초기화 전에 거부한다. 운영 재현 확인은 Step 7 이후 작업이다.
- held-out 질문·Gold 열람/검색/평가 없음. dev/dev-ko는 같은 의미의 대응 문항이며 36개의 독립 질문이나 독립 일반화 성능이 아니다.
- Step 6(metadata)는 제외 상태이며 Step 7과 최종 held-out 평가는 시작하지 않았다.

### SHA-256

| 대상 | Manifest SHA-256 |
|---|---|
| `data/evaluation/retrieval_step2_generation_queries_v1` | `efe3a90a9e8408d9dd9bd2fe1b83682d3b40422fa1616cba43f91c9f316b5d79` |
| `data/evaluation/retrieval_step3_translation_queries_v1` | `bd1eca7270a208e67f0eff44e42076831926a7f471a78c3b4ed895abb100150f` |
| `data/evaluation/retrieval_step3_translation_queries_v2` | `4568e63524f2bbe9b355e902ceac6fb2b225787cafa3bc2a859c45ec7ea0f3a6` |
| `reports/experiments/retrieval_v2_step23_evaluation_v2` | `dc6a4b573855bbe233128266cbe667cc8771d91ddc6304015323dfd4104ad887` |
| `data/literature/representations/retrieval_child_v1` | `5ab75628e68801b113e0bddccfd7b9fd8624daa711c0df5885442096a8412883` |
| `reports/experiments/retrieval_v2_step45_evaluation_v1` | `2911f02759749ee1c5759815cb67d6185b336f125df509a4929045819fd543b7` |
| 평가 사전 protocol.json | `fe96a23a6c77fedd8f361c7f7996d7122ca1150e287856fe04c59a382c4ff61e` |

동결 산출물은 ignored `data/` / `reports/`에 보존하고 Git에는 코드·설정·테스트·공개 문서만 포함한다. 기존 산출물을 덮어쓰지 않는다. 다시 실행할 때는 반드시 새 version 출력 경로를 사용한다.
