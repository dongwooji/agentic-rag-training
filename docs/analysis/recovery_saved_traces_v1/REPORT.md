# 저장된 trace로 본 Recovery 관찰 보고서 v1

작성일: 2026-10-16. 분석 작업 기준 master: `e7a61b58526b0e2cc048091d7eed2b13f5a38aee`.

저장된 230개 실행 기록에서 문헌 검색 55건, Recovery 발동 17건을 확인했다. Grader의 부족→충분 전환은 2건이며, 최종 `answer_ready`까지 도달한 것은 1건이다. 문헌 Gold로 확인할 수 있는 과거 E2E에서는 최종 EGR@10·CE@10이 개선된 실행이 1건이었다. **버전·질문·실행 조건이 섞인 사후 관찰이며, Recovery의 일반적인 성공률이나 현재 head의 성능 평가가 아니다.**

## 분석 범위와 집계 방법

- 새 REST 요청, OpenAI/LLM 호출, DB 접근, embedding, 검색, 평가 실행은 하지 않았다. 기존 JSON을 읽어 횟수·합계·Gold 지표를 오프라인으로 계산했다.
- 코드·prompt·config·Grader·Recovery·Gold·corpus·기존 결과는 수정하지 않았다. 이 PR은 새 보고서와 그 집계 근거만 추가한다.
- 단위는 고유 질문 수가 아니라 **실행 기록 수**다. 동일 질문을 서로 다른 모드/시점에 실행한 기록은 각각 집계하되, 같은 실행의 복사본·개별 response 파일·중첩 API 로그는 중복 계산하지 않았다.
- acceptance는 master에 보존된 `docs/evaluations/phase_a_evidence_acceptance_v1/{baseline,changed,regression}/raw.json`을 사용했다. 서비스/interpretation은 각 `rest.json`, 과거 E2E는 각 `case_results.json` 하나씩을 사용했다.
- 170건은 저장된 REST 관찰, 60건은 과거 E2E graph 통합 실행이다. 후자는 REST 관찰로 부르지 않는다. v2 실행기의 `workflow.invoke` 경로를 과거 tag에서 읽었고, 파일 SHA-256이 당시 preregistration과 같음을 확인했다. 과거 코드를 현재 tree에 복원하거나 실행하지 않았다.
- **문헌 검색 실행**은 실제 Literature Tool 호출 기록이 있는 요청이다. Router가 문헌을 선택했어도 Tool 호출 전에 멈춘 요청은 포함하지 않는다. 전체 230건의 분모에서는 제외하지 않는다.
- **Recovery 발동**은 Recovery 결과와 실제 Recovery LLM 호출이 모두 확인되는 요청이다. 쿼리 생성 실패도 발동에 포함한다. `query_ready`, 재검색 Tool 호출, 재검사 Grader 호출을 따로 센다.
- **부족→충분**은 최초 Grader가 정상 완료·false이고 Recovery 뒤 Grader가 정상 완료·true인 경우다. Grader 실행 오류와 근거 부족을 구분한다. terminal은 공개 최종 응답 기준이다.
- 원본 경로·SHA-256·실행 SHA/환경 정보는 [sources.json](sources.json), 230건의 집계는 [case_audit.json](case_audit.json), 17건의 축약 trace는 [recovery_traces.json](recovery_traces.json), 정밀 수치와 Gold Top-10 ID는 [summary.json](summary.json)에 있다. ignored `reports/` 원본은 PR에 복사하지 않았다. 따라서 해당 원본이 없는 환경에서는 SHA 확인과 전체 원본 재검산에 로컬 보관본이 필요하다.

## 1. 문헌 검색과 Recovery 발동

| 저장 실행 묶음 | 전체 실행 | 문헌 검색 요청 | Recovery 요청 | Recovery 시도 | 재검색 | 부족→충분 |
|---|---:|---:|---:|---:|---:|---:|
| acceptance v1 baseline | 20 | 2 | 0 | 0 | 0 | 0 |
| acceptance v1 changed | 20 | 6 | 2 | 3 | 2 | 1 |
| acceptance 후 regression diagnostic | 20 | 3 | 0 | 0 | 0 | 0 |
| 과거 E2E baseline v1 | 30 | 18 | 7 | 14 | 14 | 0 |
| 과거 E2E baseline v2 | 30 | 18 | 4 | 8 | 6 | 0 |
| 서비스 정성 smoke / legacy | 10 | 1 | 0 | 0 | 0 | 0 |
| 서비스 정성 smoke / Phase A | 10 | 0 | 0 | 0 | 0 | 0 |
| interpretation v1 dev / legacy | 10 | 2 | 1 | 2 | 1 | 0 |
| interpretation v1 dev / phase_a_before | 10 | 0 | 0 | 0 | 0 | 0 |
| interpretation v1 dev / phase_a_fixed | 10 | 1 | 1 | 1 | 1 | 1 |
| interpretation v1 test / legacy | 20 | 3 | 2 | 4 | 2 | 0 |
| interpretation v1 test / phase_a_before | 20 | 0 | 0 | 0 | 0 | 0 |
| interpretation v1 test / phase_a_fixed | 20 | 1 | 0 | 0 | 0 | 0 |
| **기록 합계: 조건 혼합** | 230 | 55 | 17 | 32 | 26 | 2 |

55건 모두 최초 검색 Tool은 `success`였다. 과거 E2E v1은 그중 1건에서 최초 Grader가 정상 판정에 도달하지 못했으므로, 55건을 모두 정상 Grader 판정 표본으로 취급하지 않는다. 서비스 smoke의 Phase A처럼 검색에 도달한 요청이 0건인 묶음은 Recovery 효용을 관찰할 기회가 없었다.

Recovery는 총 32회 시도했고, 26회 `query_ready` 후 실제 재검색과 Grader 재검사가 실행됐다. 나머지 6회는 Recovery `failure`이며, 새 검색을 실행하지 않았다. 그중 과거 E2E v2 2회와 interpretation v1 3회는 저장 trace의 `duplicate_query`로 확인된다. acceptance changed 1회는 공개 trace에 사유 코드가 없어 **실패 원인을 확정하지 않는다**.

서비스 E2E의 10개 질문은 **AI가 작성한 사용자 스타일의 정성 smoke 질문**이다. Gold가 없으며 benchmark나 일반화 성능 평가셋이 아니다. legacy와 Phase A 실행은 같은 10개 질문의 서로 다른 실행이다.

### 서로 다른 실행 조건

| 묶음 | 저장된 기준/시점 | 해석 시 주의 |
|---|---|---|
| acceptance baseline | `685e2b9194419fbf757df2df2156299b77e4afe3`, 2026-10-15 UTC, Phase A | changed와 같은 corpus v1·MiniLM 128 환경 |
| acceptance changed / regression | `6c68ea996f967bcdda47bfed2671aae1ae583969`, 2026-10-15 UTC, Phase A | 이후 bugfix head의 평가가 아님. regression은 주 판정 후 진단 |
| 과거 E2E v1/v2 | 2026-09 실행, `eval_dataset_v1` 30개를 각 실행 | 당시 hybrid baseline과 직접 graph 통합 실행. v2에는 Metric adapter·문헌 질문 분리·답변 payload 변경도 있음 |
| 서비스 smoke | 2026-10-12 UTC, corpus v1, Final V2 서비스 config `02a7d398…` | config 파일 SHA이며 Final V2의 실험 config hash `004a6582…`와 혼동하지 않음 |
| interpretation v1 | before `f423265d…`, legacy/fixed `62b247ec…`, corpus v1 | dev/test에 동일 질문을 여러 해석 모드로 실행한 기존 진단 |

각 원본의 세부 환경은 `sources.json`에 기록했다. acceptance의 encoder revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, `paraphrase-multilingual-MiniLM-L12-v2`, max sequence length 128이다. 과거 E2E와 최신 acceptance를 같은 retrieval 조건의 대조 실험으로 묶지 않는다.

## 2. 발동 요청의 Grader 변화와 terminal

| 묶음 / case | 최초→마지막 Grader | 시도 / 재검색 | 최종 terminal |
|---|---|---:|---|
| acceptance_changed / EN-01B | 부족→충분 | 1 / 1 | `answer_ready` |
| acceptance_changed / EN-06B | 부족→부족 | 2 / 1 | `abstain_ready` |
| e2e_v1 / LIT-008 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-001 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-003 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-004 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-005 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-006 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v1 / HYB-007 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v2 / LIT-008 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v2 / HYB-004 | 부족→부족 | 2 / 2 | `abstain_ready` |
| e2e_v2 / HYB-006 | 부족→부족 | 2 / 1 | `abstain_ready` |
| e2e_v2 / HYB-007 | 부족→부족 | 2 / 1 | `abstain_ready` |
| interpretation_dev_legacy / D07 | 부족→부족 | 2 / 1 | `abstain_ready` |
| interpretation_dev_phase_a_fixed / D08 | 부족→충분 | 1 / 1 | `execution_failure` |
| interpretation_test_legacy / T02 | 부족→부족 | 2 / 1 | `abstain_ready` |
| interpretation_test_legacy / T11 | 부족→부족 | 2 / 1 | `abstain_ready` |

부족→충분 2건은 acceptance changed `EN-01B`와 interpretation dev fixed `D08`이다. 전자는 `answer_ready`, 후자는 답변 생성 뒤 `grounding_validation_failure`로 `execution_failure`다. D08의 저장된 최종 답변 provenance에는 `integrated_summary is required only for Hybrid evidence`가 남아 있다. **Grader 통과와 최종 답변 성공을 같은 지표로 세지 않는다.** 나머지 15건은 마지막 정상 Grader가 계속 부족이며 `abstain_ready`로 끝났다.

EN-01B는 전후 requirement 문구도 달라졌다. 이 전환은 저장된 Grader 판정의 관찰값이며, 같은 요구사항을 고정했을 때 근거가 충분해졌다는 독립 검증은 아니다. 아래 사례에서 이 차이를 그대로 보인다.

## 3. Gold가 연결되는 요청의 재검색 전후 EGR@10 / CE@10

`eval_dataset_v1.json`의 SHA-256 `1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509`가 과거 E2E v1/v2 preregistration에 기록된 값과 같다. 이 Gold의 required literature evidence group만 사용했다. `match=any`는 하나 이상, `match=all`은 모든 Gold chunk를 요구한다. EGR은 충족한 required group 비율, CE는 required group 전부 충족 시 1이다. required group이 없는 요청은 N/A이며 0으로 넣지 않는다.

**전**은 최초 검색 Top-10, **후**는 마지막 재검색까지 결합된 working evidence의 Top-10이다. 재검색 원본 Top-10 하나만을 쓰거나 전체 합집합을 분모 없이 쓰지 않았다. 각 회차 결합 결과도 따로 계산했다. 새 검색 없이 저장된 chunk ID만 Gold에 대조했다.

| 실행 묶음 | Gold 적용 입력 | 최초 EGR@10 | 최종 EGR@10 | 최초 CE@10 | 최종 CE@10 |
|---|---:|---:|---:|---:|---:|
| e2e_v1 | 18 | 0.660185 | 0.660185 | 0.555556 | 0.555556 |
| e2e_v2 | 18 | 0.660185 | 0.715741 | 0.555556 | 0.611111 |

회수 기회가 있었던 요청만 보면 다음과 같다. `회차`는 각 재검색 후 결합 Top-10이다.

| 실행 / case | EGR 최초→회차→최종 | CE 최초→회차→최종 |
|---|---|---|
| e2e_v1 / LIT-008 | 0.80 → 0.80 → 0.80 → 0.80 | 0 → 0 → 0 → 0 |
| e2e_v1 / HYB-001 | 0.00 → 0.00 → 0.00 → 0.00 | 0 → 0 → 0 → 0 |
| e2e_v1 / HYB-003 | 0.00 → 0.00 → 0.00 → 0.00 | 0 → 0 → 0 → 0 |
| e2e_v1 / HYB-004 | 0.00 → 0.00 → 0.00 → 0.00 | 0 → 0 → 0 → 0 |
| e2e_v1 / HYB-005 | 1.00 → 1.00 → 1.00 → 1.00 | 1 → 1 → 1 → 1 |
| e2e_v1 / HYB-006 | 0.00 → 1.00 → 0.00 → 0.00 | 0 → 1 → 0 → 0 |
| e2e_v1 / HYB-007 | 1.00 → 1.00 → 1.00 → 1.00 | 1 → 1 → 1 → 1 |
| e2e_v2 / LIT-008 | 0.80 → 0.80 → 0.80 → 0.80 | 0 → 0 → 0 → 0 |
| e2e_v2 / HYB-004 | 0.00 → 0.00 → 0.00 → 0.00 | 0 → 0 → 0 → 0 |
| e2e_v2 / HYB-006 | 0.00 → 1.00 → 1.00 | 0 → 1 → 1 |
| e2e_v2 / HYB-007 | 1.00 → 1.00 → 1.00 | 1 → 1 → 1 |

표는 최초·각 회차·최종을 모두 표시하므로 마지막 회차와 최종 값이 반복될 수 있다.

최종 지표가 개선된 것은 v2 `HYB-006` 1건이다(EGR 0→1, CE 0→1). v1 `HYB-006`은 첫 결합에서 0→1이었지만 두 번째 결합에서 Gold chunk가 Top-10 밖으로 밀려 최종 0으로 돌아왔다. 모든 재검색의 효과가 누적 개선되는 것은 아니다. 나머지 Gold 적용 Recovery 요청은 최종 지표가 동일했고, 최초 대비 최종 악화는 없었다.

v2 HYB-006도 Grader는 계속 부족이었다. 반대로 최초 Gold CE=1인데 Recovery가 발동한 v1 `HYB-005`, `HYB-007` 및 v2 `HYB-007`도 있다. 이는 고정 Gold 충족과 Runtime Grader가 구성한 요구사항 판정의 불일치 관찰이며, 이 자료만으로 어느 쪽 판정이 옳다고 확정하거나 Grader를 수정하지 않는다. 기존 frozen 보고서의 `recovery.success_count=1`은 **Gold CE의 불완전→완전 전환** 정의이며, 이 보고서의 Grader 부족→충분 수와 다른 지표다.

acceptance/interpretation에는 채널·동작 Gold가 있지만 문헌 evidence-group Gold가 없다. 따라서 해당 요청에 retrieval EGR/CE를 임의로 부여하지 않았다. 서비스 smoke도 Gold가 없어 N/A다.

### held-out 기록을 분모에 넣지 않은 이유

저장된 `retrieval_v2_heldout_evaluation_v1/result.json`과 `bge_m3_model_comparison_v1/heldout_result.json`은 corpus v2의 검색 순위/Gold 지표이며 Grader·Recovery 실행 이력이 없다. 후자는 evidence-bearing 20개를 MiniLM/BGE-M3 각각 실행한 기존 diagnostic only 결과다. **Recovery가 0회였다는 뜻이 아니라 관찰 대상 실행 자체가 없다.** 재검색 전후 EGR/CE, 추가 LLM 호출, Recovery 지연은 모두 N/A다. 과거 E2E의 30개 통합 입력을 이 held-out 20개와 혼동하지 않는다.

## 4. Recovery로 추가된 호출과 지연

최초 Grader 뒤 Recovery 쿼리 생성 LLM과 재검색 후 Grader LLM만 추가 호출로 집계했다. 실패한 쿼리 생성도 포함한다. 최초 Interpreter·Step 2·Step 3·Grader와 최종 답변 생성 호출은 포함하지 않았다. 최종 답변이 없었을 가상 실행과의 차이를 관찰할 수 없으므로 답변 생성 시간을 Recovery 추가분으로 단정하지 않는다.

| 묶음 | 발동 요청 | Recovery LLM + 재검사 LLM | LLM 기록 지연 합계(s) | 발동 요청당 LLM 지연(s) | 재검색 지연 합계(s) | 별도 pacing 기록(s) |
|---|---:|---:|---:|---:|---:|---:|
| acceptance_changed | 2 | 3 + 2 | 6.339 | 3.169 | 미기록 | 0.000 |
| e2e_v1 | 7 | 14 + 14 | 47.128 | 6.733 | 3.098 | 124.820 |
| e2e_v2 | 4 | 8 + 6 | 22.663 | 5.666 | 1.136 | 63.341 |
| interpretation_dev_legacy | 1 | 2 + 1 | 4.174 | 4.174 | 0.132 | 0.000 |
| interpretation_dev_phase_a_fixed | 1 | 1 + 1 | 3.326 | 3.326 | 0.123 | 0.000 |
| interpretation_test_legacy | 2 | 4 + 2 | 11.756 | 5.878 | 0.383 | 0.000 |

총 **58회 = Recovery LLM 32회 + 재검사 Grader LLM 26회**, 기록된 LLM 호출 지연 합계 **95.387초**다. usage가 누락된 추가 호출은 없고 input 180,499 / output 6,063 token, 당시 저장된 가격표 기준 추정 비용 합계는 **USD 0.162658**이다. 새 비용이 발생한 것이 아니라 기존 실행 비용을 분리 집계한 값이다.

재검색 지연은 확인 가능한 합계 4.872초이며 acceptance changed의 재검색 2회 시간은 저장되지 않아 포함하지 않았다. 과거 E2E에는 별도 `pacing_ms` 합계 188.161초가 있으므로 실제 사용자 서비스의 검색/LLM 지연과 섞지 않았다. 위 LLM 합계는 저장된 호출 `latency_ms` 합계이며, 결합/검증/graph 처리 등 전체 Recovery 구간의 벽시계 지연이나 Recovery를 끈 경우 대비 인과적 지연 차이가 아니다. acceptance의 전체 요청 시간에서 초기 단계를 빼서 누락된 검색 지연을 추정하지도 않았다.

## 5. 저장된 구체 trace

### 개선 관찰: acceptance changed EN-01B

질문: “2017년 바벨 인클라인 벤치프레스 주별 볼륨, 근비대가 목표였다면 모자라진 않았어?”

원본: `docs/evaluations/phase_a_evidence_acceptance_v1/changed/raw.json#/cases/1`. 평가 SHA는 `6c68ea996f967bcdda47bfed2671aae1ae583969`다.

1. Step 2: `바벨 인클라인 벤치프레스 주별 볼륨, 근비대가 목표였다면 모자라진 않았어?`
2. Step 3 / 최초 검색: `Barbell incline bench press weekly volume, if hypertrophy was the goal, wasn't it insufficient?`
3. 최초 Grader: C1 “근비대가 목표였다면 **2017년 바벨 인클라인 벤치프레스의** 주별 볼륨이 충분했는지” → missing.
4. Recovery 1: `barbell incline bench press weekly volume hypertrophy sufficient dose` → query_ready → 새 Top-10 → 기존 근거와 결합.
5. 재검사 Grader: C1 “근비대가 목표였다면 주별 볼륨이 부족했는지 여부” → supported. 최종 `answer_ready`, HTTP 200.

재검사에서 지원 근거로 든 문헌:

| 문헌 | section | chunk ID | 최초→재검색→결합 순위 |
|---|---|---|---|
| Dose-Response Modelling of Resistance Exercise Across Outcome Domains in Strength and Conditioning: A Meta-analysis. | Discussion | `paper_pmc11239729_discussion_007_01077b25` | 2→4→1 |
| Resistance Training Variables for Optimization of Muscle Hypertrophy: An Umbrella Review. | Discussion > Frequency | `paper_pmc9302196_discussion_frequency_001_6a7a4717` | 5→10→5 |
| Effects of High-Volume Versus High-Load Resistance Training on Skeletal Muscle Growth and Molecular Adaptations. | Materials and Methods > Resistance Training | `paper_pmc8962955_materials_and_methods_resistance_training_001_7fd0f416` | 3→6→2 |

**세 supporting chunk는 모두 최초 Top-10에도 있었다.** 재검색으로 새 chunk 3개가 들어왔고 결합 순위도 바뀌었으나, 지원 판정은 기존에도 있던 근거를 사용했다. requirement에서 연도·운동 특정 조건이 빠진 변화도 있어 “새 정답 근거를 찾아 해결했다”고 단정할 수 없다. 문헌 Gold가 없어 EGR/CE는 N/A다. 이는 Grader 판정과 terminal이 개선된 관찰 사례다.

추가 LLM 2회(Recovery 1 + Grader 1), 기록 호출 지연 2.588초, 전체 요청 18.963초. 재검색 시간은 별도로 저장되지 않았다.

### 개선되지 않음: 같은 changed 실행의 EN-06B

질문: “바벨 인클라인 벤치프레스 처음 3세션과 마지막 3세션의 추정 1RM 중앙값을 비교해줘. 비슷한 주제의 논문이 있다면 참고로 덧붙여줘”

원본: 같은 `changed/raw.json#/cases/11`.

1. Record/Metric Tool 호출은 success. 최초 Grader는 “바벨 인클라인 벤치프레스의 추정 1RM 변화” C1을 missing, “비슷한 주제의 논문” C2를 supported로 판정했다.
2. Recovery 1: `barbell incline bench press estimated 1RM change OR progression OR adaptation` → query_ready → 재검색·결합.
3. 재검사: “바벨 인클라인 벤치프레스의 추정 1RM 변화와 관련된 비슷한 주제” C1이 계속 missing.
4. Recovery 2: failure, query=null. 이 실패 원인 코드는 저장 공개 trace에 없으므로 중복 쿼리 등으로 추정하지 않는다.
5. 최종 `abstain_ready`, HTTP 200. 추가 LLM 3회(Recovery 2 + Grader 1), 지연 3.751초, 전체 요청 13.515초. EGR/CE는 N/A.

문헌으로 개인 기록 자체의 변화를 증명할 수는 없다는 채널 문제가 trace의 미충족 요구에 드러나지만, 이 보고서는 원 질문·Grader requirement·실행 상태만 보존하며 정책 변경이나 새로운 정답 판정을 하지 않는다.

### Gold 개선과 Grader 유지가 갈린 보조 사례: 과거 E2E v2 HYB-006

원본: `reports/baselines/end_to_end_baseline_v2/case_results.json#/21`.

- 최초 Grader: “proximity-to-failure 연구를 함께 보면 매 세트를 failure까지 해야 하는가” → missing.
- Recovery 1: `proximity to failure resistance training set to failure muscle hypertrophy strength systematic review`.
- 결합 Top-10에 Gold `paper_pmc9935748_abstract_001_47d8e9e3`가 새로 들어와 EGR/CE가 0→1. 문헌: *Influence of Resistance Training Proximity-to-Failure on Skeletal Muscle Hypertrophy: A Systematic Review with Meta-analysis.* / Abstract.
- 재검사 requirement는 “매 세트를 failure까지 해야 한다는 결론을 뒷받침하는지”로 표현되고 여전히 missing. Recovery 2는 `duplicate_query`로 실패. 최종 abstain_ready.
- Gold 지표의 개선과 “매 세트 실패가 필요하다”는 방향의 Grader requirement 충족은 다른 문제다. 답변 가능성/Grader 정확도를 새로 판정하거나 수정하지 않는다.

전후 Top-10 전체 ID·각 Grader component·재검색어·추가 LLM 기록은 `recovery_traces.json`에 있고, Gold 요구별 집합 대조 결과는 `summary.json`에 있다.

## 제한과 검증

- 소수의 반복 질문, 하나의 확률적 실행, 서로 다른 해석·검색·graph 버전, 캐시·서버 상태·pacing이 섞여 있다. 무작위 대조나 Recovery on/off 재실행을 하지 않았으므로 개선의 인과관계, 일반화 성능, 현재 운영 효과를 추정하지 않는다.
- 문헌 검색에 이르기 전 clarification/오류로 끝난 요청이 많다. 해당 기록은 Recovery의 실패/성공 표본이 아니다. 새로운 입력으로 다시 실행하지 않았다.
- Grader가 회차마다 requirement를 다르게 표현한 사례가 있다. 같은 고정 요구의 근거 충족 개선인지와 Grader 판정 변화인지를 분리했다.
- interpretation·acceptance Gold를 retrieval Gold로 간주하지 않았다. held-out은 Recovery trace가 없어서 N/A다. 없는 지표·시간·실패 원인은 만들지 않았다.
- acceptance changed의 HTTP 500 3건도 원본과 전체 실행 분모에 보존했다. 이번 계산으로 frozen behavior 점수·decision을 수정하지 않았다.
- 36개 Gold 적용 실행의 최초/최종 지표 72쌍을 저장된 평가 값과 재대조해 일치했다. 회차별 지표는 저장 fusion ID를 이용한 새 오프라인 집계이며 기존 평가 결과를 덮어쓰지 않았다.
- 원본 source SHA-256 32개, 기존 tracked 파일 597개, 앞선 보호 snapshot의 파일 1,698개가 그대로임을 확인했다. 이 PR의 새 보고서 파일만 변경 대상으로 삼았다.
- 검증은 파일 무결성·집계 일관성·Gold 지표 재계산으로 한정했다. 로컬 서비스/LLM/DB/전체 E2E 테스트는 실행하지 않았다. PR의 자동 공개 CI는 기존 mock/offline 회귀 검사이며 실제 E2E 재평가와 구분한다. CI 결과와 최종 문서 commit은 PR 및 완료 보고에 기록한다.

Phase A evidence acceptance v1의 주 평가 결과는 `6c68ea996f967bcdda47bfed2671aae1ae583969` 기준이다. 그 이후의 수정은 해당 acceptance 평가를 다시 실행하지 않았으므로 기존 acceptance 결과를 현재 head의 성능 근거로 해석하지 않는다.

보고서 작성과 별도 PR 제출까지만 수행한다. Grader·Recovery 튜닝, 새 실험, PR merge는 하지 않는다.
