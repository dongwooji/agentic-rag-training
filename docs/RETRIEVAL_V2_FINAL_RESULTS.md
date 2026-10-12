# Retrieval V2 최종 설정·held-out 평가 결과

평가일: 2026-10-12. 설정은 corpus v1 dev에서 선택했고 최종 평가는 corpus v2 held-out에서 수행했다.
Final V2는 Step 3b를 사후 결정으로 선택한 경로이며, 역사적 사전 기준 경로인 Step 2도 별도 평가했다.
서비스 기본값은 legacy/H0 유지. held-out 결과에 따른 추가 튜닝·Gold/query 수정은 없다.

[사전 결정](decisions/RETRIEVAL_V2_FINAL.md), [실행 안내](RETRIEVAL_V2_FINAL.md), [설정 manifest](retrieval_v2_final_manifest_v1.json).

## 실행 순서

최종 설정·소스 동결 → Step 2 24회 생성·동결 → Step 3 24회 번역·동결 → corpus v2 parent index 생성 →
H0/H1/Step 2/Final V2 각각 24문항 1회 검색 → 네 조건의 모든 순위 동결 → Gold 평가·결과 동결.
검색어는 사람/AI가 의미를 검토하거나 수정하지 않았다. generator 입력은 원 질문만, translator 입력은 동결된 실제 Step 2 query만이다.
기존 prompt·schema·validator·provider/model은 그대로 사용했고 case ID·분류·Gold는 query 선택에 사용하지 않았다.

## 전체 검색 지표

동일 24문항을 모두 검색했다. required evidence가 있는 20문항만 지표의 분모다.
나머지 4문항은 0 recall로 처리하지 않고 제외했다. MRR은 첫 required chunk의 reciprocal rank다.

| 설정 | 실제 query | EGR@10 | CompleteEvidence@10 | MRR |
|---|---|---:|---:|---:|
| H0 | 원 질문 | 0.188333 | 0.050000 | 0.196389 |
| H1 | 원 질문 | 0.200833 | 0.050000 | 0.201389 |
| step2 | 동결 Step 2 실제 query | 0.200833 | 0.050000 | 0.201389 |
| final_v2 | 동결 Step 3 실제 query | 0.208333 | 0.050000 | 0.211865 |

Final V2는 20문항 중 1문항에서 required evidence 전체를 확보했다. H0/H1/Step 2와 CE가 동일하다.
이 결과는 서비스 기본값 전환의 충분한 성능 근거로 보지 않는다. 동결 설정은 보존하며 결과에 맞춰 튜닝하지 않는다.
Step 2 vs H1 문항별 EGR은 0개 개선/0개 악화/20개 동일이고, Final V2 vs Step 2는 3개 개선/2개 악화/15개 동일이다.

## 사전 분류별 검색 지표

| 설정 | 분류 필드 | 그룹 | 전체/평가 문항 | EGR@10 | CE@10 | MRR |
|---|---|---|---|---:|---:|---:|
| H0 | question_family_id | A01-Q02 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| H0 | question_family_id | A01-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A02-Q01 | 1/1 | 0.200000 | 0.000000 | 0.500000 |
| H0 | question_family_id | A02-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A03-Q01 | 1/1 | 0.250000 | 0.000000 | 0.111111 |
| H0 | question_family_id | A05-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A06-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A06-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A07-Q01 | 1/1 | 1.000000 | 1.000000 | 1.000000 |
| H0 | question_family_id | A07-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | A08-Q01 | 1/1 | 0.200000 | 0.000000 | 1.000000 |
| H0 | question_family_id | A09-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | B01-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | B01-Q03 | 1/1 | 0.500000 | 0.000000 | 0.250000 |
| H0 | question_family_id | B02-Q01 | 1/1 | 0.666667 | 0.000000 | 0.250000 |
| H0 | question_family_id | B03-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | B05-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H0 | question_family_id | B06-Q01 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| H0 | question_family_id | B07-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | question_family_id | B08-Q03 | 1/1 | 0.200000 | 0.000000 | 0.166667 |
| H0 | question_family_id | B09-Q01 | 1/1 | 0.250000 | 0.000000 | 0.250000 |
| H0 | question_family_id | B10-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | question_family_id | X01-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | question_family_id | X02-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | scope_class | OPTIONAL | 3/2 | 0.000000 | 0.000000 | 0.000000 |
| H0 | scope_class | OUT_OF_SCOPE | 2/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | scope_class | REQUIRED_LIMITED | 7/6 | 0.075000 | 0.000000 | 0.200000 |
| H0 | scope_class | REQUIRED_MUST_SUPPORT | 12/12 | 0.276389 | 0.083333 | 0.227315 |
| H0 | task_type | boundary | 3/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H0 | task_type | hybrid | 5/4 | 0.000000 | 0.000000 | 0.000000 |
| H0 | task_type | literature_only | 16/16 | 0.235417 | 0.062500 | 0.245486 |
| H1 | question_family_id | A01-Q02 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| H1 | question_family_id | A01-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A02-Q01 | 1/1 | 0.200000 | 0.000000 | 0.500000 |
| H1 | question_family_id | A02-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A03-Q01 | 1/1 | 0.250000 | 0.000000 | 0.111111 |
| H1 | question_family_id | A05-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A06-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A06-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A07-Q01 | 1/1 | 1.000000 | 1.000000 | 1.000000 |
| H1 | question_family_id | A07-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | A08-Q01 | 1/1 | 0.200000 | 0.000000 | 1.000000 |
| H1 | question_family_id | A09-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | B01-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | B01-Q03 | 1/1 | 0.500000 | 0.000000 | 0.250000 |
| H1 | question_family_id | B02-Q01 | 1/1 | 0.666667 | 0.000000 | 0.250000 |
| H1 | question_family_id | B03-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| H1 | question_family_id | B05-Q01 | 1/1 | 0.250000 | 0.000000 | 0.100000 |
| H1 | question_family_id | B06-Q01 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| H1 | question_family_id | B07-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | question_family_id | B08-Q03 | 1/1 | 0.200000 | 0.000000 | 0.166667 |
| H1 | question_family_id | B09-Q01 | 1/1 | 0.250000 | 0.000000 | 0.250000 |
| H1 | question_family_id | B10-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | question_family_id | X01-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | question_family_id | X02-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | scope_class | OPTIONAL | 3/2 | 0.000000 | 0.000000 | 0.000000 |
| H1 | scope_class | OUT_OF_SCOPE | 2/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | scope_class | REQUIRED_LIMITED | 7/6 | 0.075000 | 0.000000 | 0.200000 |
| H1 | scope_class | REQUIRED_MUST_SUPPORT | 12/12 | 0.297222 | 0.083333 | 0.235648 |
| H1 | task_type | boundary | 3/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| H1 | task_type | hybrid | 5/4 | 0.000000 | 0.000000 | 0.000000 |
| H1 | task_type | literature_only | 16/16 | 0.251042 | 0.062500 | 0.251736 |
| step2 | question_family_id | A01-Q02 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| step2 | question_family_id | A01-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A02-Q01 | 1/1 | 0.200000 | 0.000000 | 0.500000 |
| step2 | question_family_id | A02-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A03-Q01 | 1/1 | 0.250000 | 0.000000 | 0.111111 |
| step2 | question_family_id | A05-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A06-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A06-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A07-Q01 | 1/1 | 1.000000 | 1.000000 | 1.000000 |
| step2 | question_family_id | A07-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | A08-Q01 | 1/1 | 0.200000 | 0.000000 | 1.000000 |
| step2 | question_family_id | A09-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | B01-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | B01-Q03 | 1/1 | 0.500000 | 0.000000 | 0.250000 |
| step2 | question_family_id | B02-Q01 | 1/1 | 0.666667 | 0.000000 | 0.250000 |
| step2 | question_family_id | B03-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| step2 | question_family_id | B05-Q01 | 1/1 | 0.250000 | 0.000000 | 0.100000 |
| step2 | question_family_id | B06-Q01 | 1/1 | 0.250000 | 0.000000 | 0.200000 |
| step2 | question_family_id | B07-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | question_family_id | B08-Q03 | 1/1 | 0.200000 | 0.000000 | 0.166667 |
| step2 | question_family_id | B09-Q01 | 1/1 | 0.250000 | 0.000000 | 0.250000 |
| step2 | question_family_id | B10-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | question_family_id | X01-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | question_family_id | X02-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | scope_class | OPTIONAL | 3/2 | 0.000000 | 0.000000 | 0.000000 |
| step2 | scope_class | OUT_OF_SCOPE | 2/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | scope_class | REQUIRED_LIMITED | 7/6 | 0.075000 | 0.000000 | 0.200000 |
| step2 | scope_class | REQUIRED_MUST_SUPPORT | 12/12 | 0.297222 | 0.083333 | 0.235648 |
| step2 | task_type | boundary | 3/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| step2 | task_type | hybrid | 5/4 | 0.000000 | 0.000000 | 0.000000 |
| step2 | task_type | literature_only | 16/16 | 0.251042 | 0.062500 | 0.251736 |
| final_v2 | question_family_id | A01-Q02 | 1/1 | 0.250000 | 0.000000 | 0.142857 |
| final_v2 | question_family_id | A01-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A02-Q01 | 1/1 | 0.400000 | 0.000000 | 0.333333 |
| final_v2 | question_family_id | A02-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A03-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A05-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A06-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A06-Q02 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A07-Q01 | 1/1 | 1.000000 | 1.000000 | 1.000000 |
| final_v2 | question_family_id | A07-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | A08-Q01 | 1/1 | 0.400000 | 0.000000 | 1.000000 |
| final_v2 | question_family_id | A09-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | B01-Q02 | 1/1 | 0.500000 | 0.000000 | 0.111111 |
| final_v2 | question_family_id | B01-Q03 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | B02-Q01 | 1/1 | 0.666667 | 0.000000 | 0.200000 |
| final_v2 | question_family_id | B03-Q01 | 1/1 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | question_family_id | B05-Q01 | 1/1 | 0.250000 | 0.000000 | 0.125000 |
| final_v2 | question_family_id | B06-Q01 | 1/1 | 0.250000 | 0.000000 | 0.125000 |
| final_v2 | question_family_id | B07-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | question_family_id | B08-Q03 | 1/1 | 0.200000 | 0.000000 | 0.200000 |
| final_v2 | question_family_id | B09-Q01 | 1/1 | 0.250000 | 0.000000 | 1.000000 |
| final_v2 | question_family_id | B10-Q03 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | question_family_id | X01-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | question_family_id | X02-Q01 | 1/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | scope_class | OPTIONAL | 3/2 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | scope_class | OUT_OF_SCOPE | 2/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | scope_class | REQUIRED_LIMITED | 7/6 | 0.191667 | 0.000000 | 0.208995 |
| final_v2 | scope_class | REQUIRED_MUST_SUPPORT | 12/12 | 0.251389 | 0.083333 | 0.248611 |
| final_v2 | task_type | boundary | 3/0 | 해당 없음 | 해당 없음 | 해당 없음 |
| final_v2 | task_type | hybrid | 5/4 | 0.000000 | 0.000000 | 0.000000 |
| final_v2 | task_type | literature_only | 16/16 | 0.260417 | 0.062500 | 0.264831 |

언어 관련 사전 필드: schema에 별도 필드 없음. 질문 문장을 보고 사후 그룹을 만들지 않음.

## Boundary / abstention

Boundary 3문항: {'abstain': 3}.
required group이 없는 boundary에는 EGR/CE/MRR을 정의하지 않는다. 검색 수는 결과 JSON에 별도 보존했다.
실제 Router·Grader·Recovery·답변 생성은 실행하지 않았으므로 abstention correctness는 측정하지 않았다.
expected_behavior 분포는 설계 설명이며 실제 abstention 성공 수가 아니다.

## 실제 LLM 호출과 추정 비용

| 단계 | 호출 | 입력/출력 token | API 실패 | 검증 실패/보완 | 추정 USD | 평균/중앙값/p95 초 |
|---|---:|---|---:|---|---:|---|
| generation | 24 | 17353/1849 | 0 | 0/0 | 0.02133525 | 1.736/1.176/2.468 |
| translation | 24 | 11641/1430 | 0 | 1/1 | 0.01516575 | 1.363/1.034/1.721 |

합계 48회, 입력 28,994/출력 3,279 tokens, 추정 $0.03650100.
생성 보완 사유: {}; 번역 보완 사유: {'number_preservation': 1, 'unit_preservation': 1}.
한 문항에 복수 사유가 있을 수 있어 사유별 수를 실패 문항 수로 합산하지 않는다. 재시도·재생성은 0회다.
비용은 기존 동결 가격 snapshot 기반 추정이며 실제 청구액이 아니다. usage 누락 호출은 두 단계 모두 0회다.
실제 OpenAI 호출 48회, offline MiniLM CPU 추론을 실행했다. 실제 DB/pgvector/REST 호출과 새 모델 다운로드는 없다.

## 동결 hash

Final V2 config SHA-256: `004a65821809219bb2823cadcfe77dd98d6a329987a909cfcebcbb1507cd68fe`.
Gold `heldout_v2_v1_1_final` SHA-256: `7c491a55e96b70b26b10ce7625c535e1dc521b4d83ab9d4e984d5d2c84df4b42`.

| 동결본 | manifest SHA-256 |
|---|---|
| configuration | `8b8c88e68f468d642bff8f543a1dd4b241217f14ac01c21d5d2d142621789d1b` |
| generation | `5d845aeed13cc0e043e043be660e44d250f06bcfa6a53f166d0f4e02805d46ea` |
| translation | `c09722c34ee3c66506bc4712494ba3dfb1840ae26b27334e4670b3858978ec32` |
| rankings | `108f4b5e7214c9451bb52e235762a5b5e1c3dec770c3148625b75481946afee9` |
| evaluation | `67e4cb2595ca07f398ecce81f4c7b899546b1889bf4fdd2ab6a6fea9448ad343` |

## 검증과 한계

새 기능은 합성 fixture·mock provider/encoder로 호출 횟수·원문 보완·번역 연결·경로/hash 검증·Gold 읽기 순서·재실행 거부를 검증했다.
관련 회귀 115 passed 후 최종 추가 검증을 실행했다. 공개·로컬 전체 결과:
- 공개 테스트: {'tests': 805, 'failures': 0, 'errors': 0, 'skipped': 0}
- 로컬 전체: {'tests': 886, 'failures': 0, 'errors': 0, 'skipped': 0}
각 실행의 기존 Starlette deprecation warning 1건은 유지했다. 로컬 결과에는 이번 commit에서 제외한 사용자 JATS 테스트 19개가 포함된다.
기존 protected/frozen 1289개 해시 변경 0. 새 설정·index·query·검색 순위·평가 결과만 새 version으로 생성했다.
최초 config 사전 검사에서 BUILD_FROZEN 표기 호환성을 발견해 호출 전 보정했다. 첫 실제 동결 이전이며 LLM 재호출·동결본 수정은 없었다.
held-out 검색 지표는 답변 정확도가 아니다. 개발셋과 코퍼스가 달라 dev 수치를 같은 조건의 전후 비교로 해석하지 않는다.
서비스 기본값은 legacy/H0다. pgvector 운영 재현 및 Grader/Recovery·E2E 재평가는 시작하지 않았다.
새 embedding/reranker·query 튜닝·Phase A 재설계·Gold 수정은 수행하지 않았다.
