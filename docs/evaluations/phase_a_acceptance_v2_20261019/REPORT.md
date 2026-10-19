# Phase A acceptance v2 — 2026-10-19

**고정 기준 판정: eligible_for_adoption=false. 서비스 기본값은 변경하지 않았다.**

21문항을 실제 REST API로 A/B 각 1회 실행했다. 질문·Gold·코드·prompt·config·채점 기준은 실행 중 수정하지 않았고 평가 재실행·자동 HTTP/SDK 재시도는 없었다. 기존 bounded Recovery는 양쪽 동일 정책으로 유지했다. 이 문서는 봉인된 raw와 원래 채점기의 결과만 요약한다.

## 1. SHA 확인과 실행 환경 동일성

- 평가 코드: `992b495024e2f3a5b2db2abd3890e9a4f356a094` detached HEAD, tracked clean
- A: legacy Grader / B: fixed_requirements_v1_1, 둘 다 phase_a
- 같은 Python 실행 파일·checkout·DB·corpus v1·retrieval 설정·모델. Grader 모델은 `gpt-5.4-mini-2026-03-17`이다.
- 고정 코드의 51개 config hash 및 corpus 파일 hash를 실행 전·A 후·B 후 비교하는 검사를 통과했다. DB 12개 테이블의 read-only 내용 fingerprint도 동일성 검사를 통과했다.
- tracked 파일 644개가 평가 전후 모두 동일했다. Grader 버전별 기존 prompt/config 차이(예: output token 상한)는 고정 설계 그대로이며 양쪽을 임의로 동일화하지 않았다.

| 입력 | SHA-256 |
|---|---|
| manifest | `c2fe070c0be6b18e1a2a397f3f316c2c068397c283ee07cce2204a0ef857dd63` |
| questions | `6eb8bb397d54c7c7f6cec6f6080c0be34d0edd9a7a007fb5877168828aa02713` |
| gold | `0c1d8f7dcb114cfe99600ccd2fe29ab29ac9bc05fce5154ff656cdb994c91b7e` |

DB fingerprint SHA-256: `60b9a863ad9aa874699fa80c9d06f53f3152bfda6bb52c20c8e877e259c27804`.
RAW_FREEZE SHA-256: `533b94a47303093657160e4fe286d7d053af42a66a798bd99b9f277b515e204f`. 봉인된 raw/observer 파일 86개를 재검증했다.
원래 SCORES SHA-256: `70f8420789e9eadc3ee68b6d581d9b590a8cd6ec67e88f6252f7add11c78f432`. 공개 scores.json도 같은 바이트다.

순서는 manifest/questions 검증 → A 21문항 → B 21문항 → raw 봉인 → 원래 score 명령에서 Gold hash 확인/파싱 → 오프라인 채점이었다. 실행기에 Gold를 전달하거나 runtime에서 Gold를 읽지 않았다.

**PR 상태 차이:** 요청에는 PR #28 미병합 유지라고 되어 있었으나, 시작 당시 GitHub에서 이미 2026-10-19 15:57(KST)에 `cbb92852b7ec8de3bce319b3323bcab2e51a1221`로 merge된 것을 확인했다. 이 작업에서는 PR #28을 merge하거나 상태를 변경하지 않았다. 지정 코드 SHA를 별도로 checkout하여 평가했고, 보고서 branch는 현재 master에서 생성했다. 평가 SHA와 보고서 branch base를 혼동하지 않는다.

## 2. 채택 기준 네 가지

| 고정 조건 | A | B | 통과 |
|---|---:|---:|---|
| answerable 보류 수 엄격 감소 (분모 10) | 0 | 1 | 아니오 |
| unanswerable 답변 수 비증가 (분모 4) | 0 | 1 | 아니오 |
| HTTP 포함 최종 실행 실패 비증가 (분모 21) | 0 | 0 | 예 |
| 최종 동작 정확도 비감소 | 14/21 (66.67%) | 12/21 (57.14%) | 아니오 |

`eligible_for_adoption=false`, `service_default_changed=false`. 분모·라벨·threshold는 바꾸지 않았다. A의 answerable 보류가 이미 0이어서 첫 조건의 엄격 감소를 달성할 여지가 없지만, 이를 이유로 기준을 완화하지 않았다.

## 3. 기존 acceptance 지표

| 지표 | A | B |
|---|---:|---:|
| 필수 해석 채널 누락 수 | 0 | 0 |
| 필수 실행 채널 누락 수 | 12 | 12 |
| 부당한 되묻기·보류 수 | 6 | 7 |
| 최종 동작 정답 수 | 14 | 12 |
| HTTP 포함 최종 실행 실패 수 | 0 | 0 |
| Record operation 일치 수 | 14 | 14 |
| Metric operation 집합 일치 수 | 14 | 14 |
| answer_type 일치 수 | 16 | 17 |
| 기본 최근 기간 적용 일치 수 | 21 | 21 |
| 실제 답변/보류/되묻기 수 | 9 / 4 / 8 | 9 / 4 / 8 |
| HTTP 200 응답 수 | 21/21 | 21/21 |

최종 동작 정확도는 기존 v1 채점 정의를 그대로 쓴다. 답변 내용의 사실 정확도나 answer_ready 비율과 다르다. 부당한 되묻기·보류는 Gold가 answer인 경우만 세므로 A의 6건은 되묻기 6건, B의 7건은 같은 되묻기 6건과 보류 1건이다. Gold가 abstain인데 clarification인 V2-14는 최종 동작 오답이나 이 특정 부당 지표에는 포함되지 않는다.

필수 실행 채널 누락 수는 채널 개수이며 문항 수가 아니다. 원래 채점기는 의도된 clarification에서도 실행되지 않은 채널을 집계하므로 이 12개 전부를 별도 기능 오류로 해석하지 않는다.

## 4. 그룹별 결과

| 그룹 | 문항 수 | A 정답 | B 정답 | A 답변/보류/되묻기 | B 답변/보류/되묻기 |
|---|---:|---:|---:|---|---|
| G-LA | 5 | 5 | 5 | 5/0/0 | 5/0/0 |
| G-LU | 3 | 3 | 2 | 0/3/0 | 1/2/0 |
| G-HA | 5 | 2 | 1 | 2/0/3 | 1/1/3 |
| G-HU | 1 | 0 | 0 | 0/0/1 | 0/0/1 |
| G-R | 5 | 2 | 2 | 2/0/3 | 2/0/3 |
| G-C | 1 | 1 | 1 | 0/0/1 | 0/0/1 |
| G-O | 1 | 1 | 1 | 0/1/0 | 0/1/0 |

G-LA=문헌 답변 가능, G-LU=문헌 답변 불가, G-HA=기록+문헌 답변 가능, G-HU=기록+문헌 답변 불가, G-R=기록, G-C=되묻기, G-O=범위 밖. 그룹은 frozen Gold의 pair_id를 그대로 썼다. 모든 그룹의 최종 실행 실패는 0이다.

## 5. 문항별 A/B 비교

R은 성공한 실제 재검색 횟수(retry_count)다. 질의 생성 시도와는 다르다. 정답 표시는 원래 frozen scorer의 final_behavior_correct다. 모든 행 HTTP 200이다.

| Case | 그룹 | 기대 | A 동작 | B 동작 | A/B 정답 | A/B R | A/B 지연(초) |
|---|---|---|---|---|---|---|---|
| V2-01 | G-LA | 답변 | 답변 | 답변 | O/O | 0/0 | 145.80/70.40 |
| V2-02 | G-LA | 답변 | 답변 | 답변 | O/O | 0/0 | 10.33/11.60 |
| V2-03 | G-LA | 답변 | 답변 | 답변 | O/O | 0/0 | 8.98/8.97 |
| V2-04 | G-LA | 답변 | 답변 | 답변 | O/O | 0/0 | 9.92/10.40 |
| V2-05 | G-LA | 답변 | 답변 | 답변 | O/O | 0/0 | 8.92/8.81 |
| V2-06 | G-LU | 보류 | 보류 | 보류 | O/O | 1/2 | 11.15/14.53 |
| V2-07 | G-LU | 보류 | 보류 | 답변 | O/X | 2/1 | 10.96/11.98 |
| V2-08 | G-LU | 보류 | 보류 | 보류 | O/O | 2/2 | 14.61/11.29 |
| V2-09 | G-HA | 답변 | 답변 | 답변 | O/O | 1/0 | 14.40/14.89 |
| V2-10 | G-HA | 답변 | 답변 | 보류 | O/X | 0/1 | 13.07/11.77 |
| V2-11 | G-HA | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 3.94/3.72 |
| V2-12 | G-HA | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 2.69/2.54 |
| V2-13 | G-HA | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 3.64/3.94 |
| V2-14 | G-HU | 보류 | 되묻기 | 되묻기 | X/X | 0/0 | 3.38/3.78 |
| V2-15 | G-R | 답변 | 답변 | 답변 | O/O | 0/0 | 5.24/5.58 |
| V2-16 | G-R | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 2.48/2.80 |
| V2-17 | G-R | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 2.83/3.39 |
| V2-18 | G-R | 답변 | 되묻기 | 되묻기 | X/X | 0/0 | 3.38/2.94 |
| V2-19 | G-R | 답변 | 답변 | 답변 | O/O | 0/0 | 5.72/5.97 |
| V2-20 | G-C | 되묻기 | 되묻기 | 되묻기 | O/O | 0/0 | 3.17/3.32 |
| V2-21 | G-O | 보류 | 보류 | 보류 | O/O | 0/0 | 2.07/2.30 |

case별 route·실행 Tool·Record/Metric operation·clarification 필드·Grader 판정 순서는 [cases.json](cases.json)과 [DETAILS.md](DETAILS.md)에 기록했다. 공개 문서에는 frozen 질문·Gold 전체와 개인 기록·원문 chunk·raw 응답 전문을 재배포하지 않는다. 해당 원본은 기존 로컬 입력 폴더와 reports에 보존한다.

주요 관찰:

- V2-07: A는 Grader 부족→부족→부족으로 보류, B는 부족→충분으로 답변했다. B 답변에는 당일 최대근력 저하를 직접 검증한 근거가 없다는 제한 설명이 있다. 그 내용의 신중함과 별개로 frozen unanswerable 및 answer_ready 기준에서는 불일치 1건으로 센다. 이 수치를 곧바로 생리학적 허위 주장 1건과 동일시하지 않는다.
- V2-10: 최초 한국어·영어 query와 Top-10 ID/순서는 A/B가 같았다. A는 일반적인 볼륨 증가 근거를 충분으로 판정했으나 B는 특정 운동의 주간 볼륨 적정 범위를 고정 요구사항으로 두고 부족을 유지했다. 더 구체적인 요구 범위가 보류와 연결되는 모습이 관찰됐지만, 이를 근거로 prompt/정책을 수정하지 않았다.
- V2-11/12/13/16/17/18은 양쪽에서 Grader 전에 되묻기로 중단되어 Gold 기준 답변 가능 질문의 부당한 clarification 6건을 이룬다. V2-14도 Grader 전에 되묻기로 끝났다. 이 관찰을 검색이나 Grader 실패로 합치지 않는다.
- V2-11은 정규화된 Metric 목록에 같은 operation이 중복되고, V2-12/13/14는 둘 이상의 operation이 나타난다. V2-17은 요약상 단일 operation인데도 requested_analyses 미해결이 남아 공개 trace만으로 더 세부 원인을 확정하지 않았다. V2-16/18은 time_condition 되묻기다. 여기서는 진단만 기록하고 수정하지 않는다.

## 6. LLM 호출·token·추정 비용·지연

| 단계 | A | B | 관측 방식 |
|---|---:|---:|---|
| Interpreter | 21 | 21 | 저장 api_requests 직접 집계 |
| Step 2 | 10 | 10 | 최초 검색 trace + 고정 translated_both 코드 경로로 재구성 |
| Step 3 | 10 | 10 | 같은 방식 |
| Grader | 16 | 16 | 완료된 Grader 판정 trace |
| Recovery 질의 생성 | 7 | 7 | 고정 budget 미만 호출 경로 + trace |
| 최종 답변 생성 | 9 | 9 | answer 종료와 고정 단일 호출 경로 |
| 합계 | 73 | 73 | **SDK HTTP 원장이 아닌 provider 호출 경로 재구성** |

Recovery는 양쪽 모두 실제 재검색 6회, 질의 생성 시도 7회다. A V2-06/B V2-10의 마지막 Recovery는 공개 trace에서 failure로 기록되지만 최종 보류다. 고정 nodes.py의 종료 분기상 중복 query 거절 경로와 일치하며, 상세 내부 error code/provenance는 observer가 저장하지 않아 직접 인용할 수 없다. 이를 최종 실행 실패로 재분류하지 않는다. 재검색은 고정 graph 정책이며 자동 HTTP/SDK 재시도나 문항 재실행이 아니다.

**전체 단계 token과 전체 비용은 N/A다.** 고정 observer가 Interpreter 이외의 usage provenance를 저장하지 않았다. 값을 복원하려고 provider를 재호출하거나 실행 코드를 바꾸지 않았다. 다음은 Interpreter에 한정한 실제 저장 값이며 전체 평가 비용으로 읽으면 안 된다.

| Interpreter 관측값 | A | B |
|---|---:|---:|
| input_tokens | 70429 | 70429 |
| cached_input_tokens | 53760 | 67200 |
| output_tokens | 5190 | 5254 |
| total_tokens | 75619 | 75683 |
| estimated_cost_usd | 0.03988875 | 0.03110475 |

input에는 cached input이 포함된다. 비용은 frozen config의 가격 snapshot에 따른 추정이며 실제 청구액 검증이 아니다. B의 더 많은 cache 사용은 실행 순서의 영향을 받을 수 있어 새 Grader 비용 절감으로 해석하지 않는다.

| 요청 지연(초) | A | B |
|---|---:|---:|
| 21문항 합계 | 286.679 | 214.922 |
| 평균 | 13.651 | 10.234 |
| 중앙값 | 5.724 | 5.968 |
| p95 (nearest rank) | 14.615 | 14.892 |
| 첫 요청 | 145.801 | 70.398 |
| 나머지20 평균 — 참고용 | 7.044 | 7.226 |

평균·합계에는 첫 요청의 lazy runtime/모델 구성 시간이 들어 있다. 첫 요청 A 145.801초/B 70.398초의 차이와 cache 때문에 전체 평균 차이를 Grader 속도 차이로 단정하지 않는다. 나머지20 평균은 지연 설명용 추가 통계일 뿐 채점 분모와 채택 기준은 21문항 그대로다.

## 7. 한계와 보존

1. 단일 실행, 21문항, 고정 A→B 순서다. 통계적 유의성이나 폭넓은 일반화 성능을 주장하지 않는다.
2. 사후 수정 유형과 비슷한 문항이 포함돼 있다. 새 질문의 freeze 사실만으로 기존 수정 이력과 독립적인 강한 일반화 검증이라고 표현하지 않는다.
3. V2-07은 최종 상태와 설명 내용의 경계 사례다. 라벨·문항·분모를 바꾸거나 제외하지 않았고 다른 기준의 재채점도 하지 않았다.
4. 7문항이 양쪽 모두 Grader 전 clarification에 걸렸고, 의도된 clarification 1문항이 별도로 있다. unanswerable G-HU 1문항도 이 단계에 막혀 Grader 안전성 관찰에 도달하지 않았다.
5. 독립 Interpreter/Step 2/3를 포함하는 전체 REST 시스템 비교다. 최초 한국어 query는 10/10 같았으나 영어 query와 Top-10 순서는 8/10만 같았다(V2-03/05 차이). 순수 Grader만의 인과 효과로 해석하지 않는다. 최종 동작이 달라진 V2-07/10의 최초 검색 조건은 같았다.
6. B의 최초 요구사항 출처는 문헌 실행 10문항 모두 step2였다. 이번 실행에서 original_fallback 경로는 발동하지 않았으므로 과거 HYB 7건의 재현 검증으로 해석하지 않는다. B의 16회 Grader 판정은 모두 completed였지만 이는 과거11건 전체 재평가가 아니다.
7. answerable_abstain 지표는 clarification을 포함하지 않는다. A의 값이 0이라고 해서 답변 가능한 질문을 모두 처리했다는 뜻이 아니다. 전체 동작 정확도와 부당한 되묻기를 함께 읽어야 한다.
8. 전체 token/비용 및 모든 단계의 LLM 지연 원장이 관측되지 않았다. 고정 실행기의 관측 누락이며 0으로 채우거나 재실행해 보완하지 않았다.
9. PR #28은 시작 전에 이미 merge돼 있었다. 이 작업에서는 merge하지 않았으며 정확한 지정 SHA를 평가에 사용했다. 새 결과 PR도 merge하지 않는다.
10. 초기 실행 승인 요청은 프로세스 생성 전에 자동 승인 검토에서 차단됐다. 사용자 제공 공개 실험 데이터 설명·기존 provider payload·표준 OpenAI 목적지를 읽기 전용으로 확인한 후 같은 명령의 재검토가 승인됐다. 실제 평가 프로세스는 한 번만 실행했고 API 문항 재실행은 없다.

실행 검증: 42개 응답/관찰 자료, frozen raw 86개 hash, 원래 채점 결과, 그룹·문항 합계 및 입력 SHA를 오프라인으로 대조했다. 기존 실행기/채점기 synthetic 테스트는 실제 평가 재실행과 구분한다. 단위 테스트 및 GitHub Actions의 최종 상태는 결과 PR의 최신 check와 사용자 보고에 기록한다.

서비스 기본 Grader·interpretation은 legacy 그대로이며 corpus·retrieval·embedding·Tool·prompt·validator·Recovery·Gold를 수정하지 않았다. 기존 dev/held-out/acceptance v1 결과도 덮어쓰지 않았다. runtime/config/script/test의 변경은 이 보고서 branch에 없다.

로컬 원본: `reports/phase_a_acceptance_v2_20261019/` 및 사용자가 제공한 외부 frozen 입력 폴더. 공개용 파일은 [manifest.json](manifest.json)의 SHA-256으로 식별한다.
