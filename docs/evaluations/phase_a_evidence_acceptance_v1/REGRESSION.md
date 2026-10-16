# 기존 interpretation_eval_v1 test 회귀 진단

Acceptance 판정·상세 분석 동결 후 changed SHA에서 test 20문항을 실제 REST로 각각 1회 실행했다. 재시도·clarification 후속 요청은 0회. 주 판정은 변경하지 않았다. 단일 probabilistic run이며 역사적 실행과의 차이를 이번 변경의 인과 효과로 해석하지 않는다.

| 설정 | 경로 정답 | 동작 정답 | graph execution_failure | HTTP/전송 실패 | 부당한 보류·되묻기 | 채널 일치(보조) |
|---|---:|---:|---:|---:|---:|---:|
| test/legacy | 3/20 | 13/20 | 0 | 0 | 4 | 4/20 |
| test/phase_a_before | 0/20 | 4/20 | 9 | 0 | 6 | 6/20 |
| test/phase_a_fixed | 2/20 | 9/20 | 1 | 0 | 8 | 11/20 |
| 이번 changed | 4/20 | 7/20 | 3 | 3 | 6 | 16/20 |

역사적 phase_a_fixed는 PR #14 직후 실행 기록이며 지정 기준선 685e2b9의 새 실행이 아니다. 이후 코드 변경과 LLM 변동을 분리할 수 없다. 경로는 기존 scorer의 실제 Router route 분류를 그대로 사용해 ambiguous/invalid를 미분류·오답으로 센다. HTTP 오류는 graph failure와 분리한다.

| 문항 | 기대 경로 | 이번 경로 | 이번 동작 | 경로/동작 정답 | historical phase_a_fixed 동작 |
|---|---|---|---|---|---|
| T01 | 기록+문헌 | ambiguous (미분류) | HTTP/transport_failure / HTTP 500 | False / False | 되묻기 |
| T02 | 기록+문헌 | ambiguous (미분류) | HTTP/transport_failure / HTTP 500 | False / False | 되묻기 |
| T03 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T04 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T05 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / True | 되묻기 |
| T06 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T07 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T08 | 기록+문헌 | ambiguous (미분류) | HTTP/transport_failure / HTTP 500 | False / False | 되묻기 |
| T09 | 기록+문헌 | hybrid (기록+문헌) | 답변 / HTTP 200 | True / True | 되묻기 |
| T10 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / True | 되묻기 |
| T11 | 기록+문헌 | hybrid (기록+문헌) | 답변 / HTTP 200 | True / True | 되묻기 |
| T12 | 기록+문헌 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / True | 되묻기 |
| T13 | 기록+문헌 | invalid (미분류) | execution_failure / HTTP 200 | False / False | 되묻기 |
| T14 | 기록 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T15 | 기록 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |
| T16 | 기록 | log_metric (기록) | 답변 / HTTP 200 | True / True | 답변 |
| T17 | 문헌 | literature_only (문헌) | 답변 / HTTP 200 | True / True | 답변 |
| T18 | 기록+문헌 | invalid (미분류) | execution_failure / HTTP 200 | False / False | execution_failure |
| T19 | 범위 밖 | invalid (미분류) | execution_failure / HTTP 200 | False / False | 되묻기 |
| T20 | 범위 밖 | ambiguous (미분류) | 되묻기 / HTTP 200 | False / False | 되묻기 |

## 실행량

{
  "calls": 33,
  "by_stage": {
    "interpreter": 20,
    "step2": 3,
    "step3": 3,
    "grader": 3,
    "answer": 4
  },
  "input_tokens": null,
  "cached_input_tokens": null,
  "output_tokens": null,
  "total_tokens": null,
  "estimated_cost_usd": null,
  "actual_cost_usd": null,
  "actual_cost_status": "provider_billing_not_supplied",
  "usage_unavailable_calls": 1,
  "known_subtotals": {
    "input_tokens": 126685,
    "cached_input_tokens": 43776,
    "output_tokens": 7531,
    "total_tokens": 134216,
    "estimated_cost_usd": 0.09935445
  }
}

usage가 없는 호출의 token/추정 비용 합계는 null이고 알려진 소계만 별도 제시했다. 실제 청구 비용은 미제공(null)이다. raw.json과 개별 HTTP body/trace는 regression/에 동결했다.

## 문항별 응답 원문

### T01

요즘 내 스쿼트 중량이 계속 떨어지고 있는데 가능할 만한 원인이 뭘까?

HTTP 500; terminal=None; error_code=None; LLM=1; latency=8.090s.

미해결: `[{"field": "record_operation", "reason": "조회 작업을 지정해주세요."}]`.

{"detail": "The request could not be processed safely."}

### T02

벤치프레스 기록이 한동안 제자리인데, 진짜 정체기인지 보고 연구에서는 정체기에 뭘 바꾸라고 하는지 알려줘

HTTP 500; terminal=None; error_code=None; LLM=1; latency=3.647s.

미해결: `[{"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}]`.

{"detail": "The request could not be processed safely."}

### T03

데드리프트를 일주일에 한 번만 해서 안 느는 걸까? 내 빈도 확인하고 빈도를 늘리면 도움 되는지 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=3.703s.

미해결: `[{"field": "n_sessions", "reason": "빈도 비교를 위한 기준 세션 수가 명시되지 않음"}, {"field": "time_condition", "reason": "분석 기간을 지정해주세요."}]`.

다음 내용을 확인해주세요:
- 처음과 최근 각각 몇 세션을 비교할까요?
- 분석 기간: 어느 기간을 볼까요? 예: 2018년 9월~12월

### T04

스쿼트 볼륨을 계속 늘렸는데 무게는 그대로야. 볼륨이랑 e1RM 변화를 같이 보고 볼륨을 더 늘리는 게 의미 있는지 연구 근거로 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=3.166s.

미해결: `[{"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}]`.

다음 내용을 확인해주세요:
- 어떤 지표를 확인할까요? 한 번에 하나의 지표를 선택해주세요.

### T05

오래 쉬었다가 다시 시작했는데 벤치 기록이 많이 떨어졌어. 쉬기 전후 기록 비교해주고 쉬면 근력이 얼마나 빠지는지 연구도 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=3.262s.

미해결: `[{"field": "n_sessions", "reason": "비교할 세션 수가 명시되지 않음"}, {"field": "n_sessions", "reason": "처음과 최근 비교에 사용할 세션 수를 지정해주세요."}, {"field": "comparison", "reason": "처음과 최근 세션의 비교 조건을 확인해주세요."}, {"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}, {"field": "record_operation", "reason": "Phase A의 전체/기간 기록 조회로 표현할 수 없는 세션 선택입니다."}]`.

다음 내용을 확인해주세요:
- 처음과 최근 각각 몇 세션을 비교할까요?
- 어떤 지표를 확인할까요? 한 번에 하나의 지표를 선택해주세요.
- 확인할 운동·기간·지표를 구체적으로 알려주세요.

### T06

내 기록 보면 매번 실패 직전까지 하는 것 같은데, 이렇게 계속하는 게 근력에 좋은지 연구 근거로 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=2.225s.

미해결: `[{"field": "canonical_exercise_name", "reason": "원문의 운동 표현을 확인해주세요."}, {"field": "record_operation", "reason": "조회 작업을 지정해주세요."}]`.

다음 내용을 확인해주세요:
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.
- 확인할 운동·기간·지표를 구체적으로 알려주세요.

### T07

인클라인 벤치 무게가 플랫 벤치보다 훨씬 덜 오르는데, 내 기록으로 두 운동 비교해주고 원인이 될 만한 게 뭔지 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=4.258s.

미해결: `[{"field": "canonical_exercise_name", "reason": "여러 저장 운동이 있습니다. 조회할 운동을 선택해주세요."}, {"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}]`.

다음 내용을 확인해주세요:
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.
- 어떤 지표를 확인할까요? 한 번에 하나의 지표를 선택해주세요.
어떤 기록을 조회할까요?
1. Incline Bench Press (Barbell)
2. Incline Press (Dumbbell)
3. Low Incline Dumbbell Bench

### T08

최근 몇 달 동안 데드리프트 e1RM이 떨어졌는데 운동을 너무 자주 해서 그런 걸까?

HTTP 500; terminal=None; error_code=None; LLM=1; latency=3.808s.

미해결: `[{"field": "n_sessions", "reason": "최근 몇 달 동안의 기간이 구체적으로 주어지지 않음"}, {"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}]`.

{"detail": "The request could not be processed safely."}

### T09

스쿼트 할 때 반복수는 늘었는데 무게는 안 올랐어. 이걸 근력이 늘었다고 봐도 되는지 내 기록이랑 연구로 알려줘

HTTP 200; terminal=answer_ready; error_code=None; LLM=5; latency=14.985s.

미해결: `[]`.

당신의 기록에서는 기록상 스쿼트는 반복수와 무게가 함께 변한 구간이 있고, 예를 들면 2015-10-23에는 315에서 5회, 6회, 8회가 있었고 2018-09-27에는 365에서 5회, 380에서 3회가 기록되어 있습니다. 다만 이 기록만으로는 ‘반복수 증가’와 ‘무게 증가’가 같은 시점에 일어났는지, 또는 최근에 반복수만 늘었는지는 단정하기 어렵습니다.

문헌에서는 문헌에서는 저항훈련 후 근력이 증가할 수 있으며, 근력 증가가 항상 근육량 증가와 함께 나타나는 것은 아니라고 설명합니다. 또 반복수 범위가 넓어도 근력 향상이 가능하다는 결과가 있고, 반복수나 실패 여부만으로 근력 변화를 단정하기는 어렵습니다.

두 정보를 함께 보면 따라서 스쿼트에서 반복수만 늘고 무게가 그대로였다고 해서 곧바로 ‘근력이 늘지 않았다’고 보기는 어렵지만, 기록만으로는 근력 증가를 확정하기도 어렵습니다. 당신의 기록은 실제 수행 능력이 변하고 있음을 보여주지만, 문헌상 근력은 반복수·중량·훈련 구성 등 여러 요소와 함께 해석해야 합니다.

제한사항: 기록은 저장된 세트 일부만 제시되어 있어 전체 추세를 완전히 판단하기 어렵습니다.; 무게 단위가 원자료에서 검증되지 않은 상태입니다.; 문헌은 집단 수준 결과라 개인에게 그대로 적용된다고 말할 수는 없습니다.

### T10

운동 쉬는 날 없이 연속으로 한 주에 기록이 더 안 좋았던 것 같은데, 실제로 그랬는지 보고 휴식이 기록에 영향을 주는지 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=2.781s.

미해결: `[{"field": "exercise", "reason": "운동 종목이 특정되지 않음"}, {"field": "analysis metric/comparison details", "reason": "기록이 더 안 좋았던 것 같은데, 실제로 그랬는지의 구체적 비교 기준이 없음"}, {"field": "canonical_exercise_name", "reason": "원문의 운동 표현을 확인해주세요."}, {"field": "time_condition", "reason": "분석 기간을 지정해주세요."}, {"field": "requested_analyses", "reason": "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요."}]`.

다음 내용을 확인해주세요:
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.
- 확인할 운동·기간·지표를 구체적으로 알려주세요.
- 분석 기간: 어느 기간을 볼까요? 예: 2018년 9월~12월
- 어떤 지표를 확인할까요? 한 번에 하나의 지표를 선택해주세요.

### T11

내 벤치 기록이 들쭉날쭉한데, 이게 정상인지 연구에서 말하는 일반적인 변동이랑 비교해줘

HTTP 200; terminal=answer_ready; error_code=None; LLM=5; latency=14.107s.

미해결: `[]`.

당신의 기록에서는 벤치프레스 기록은 309개가 있고, 같은 운동명이 여러 세션에 걸쳐 반복 기록되어 있습니다. 예를 들면 2016-08-26에는 135×8, 155×5, 185×5, 185×5가 있고, 2017-02-10에는 135×6, 185×6, 225×6, 245×5, 260×6, 270×5가 있으며, 2018-09-16에는 280×5, 285×5, 285×5, 295×5가, 2018-09-25에는 225×5, 275×5, 295×5, 295×5, 295×5, 305×3가 보입니다. 제공된 기록에서는 outlier로 표시된 벤치 세트는 없습니다.

문헌에서는 문헌에서는 저항운동 연구에서 벤치프레스가 상체 근력 평가에 사용되며, 1RM 테스트는 여러 번의 시도와 휴식을 두고 수행된다고 설명합니다. 또 한 연구에서는 50% 벤치프레스 반복수 테스트의 재현성이 ICC 0.93, SEM 0.99회로 보고되었습니다. 다른 문헌들에서는 저항훈련 연구에서 측정값의 평균과 표준편차, 신뢰구간, 효과크기 등을 사용해 변화를 해석하며, 훈련 빈도나 프로그램 차이를 비교할 때도 ‘높다/낮다’를 절대적으로 고정하지 않고 맥락에 따라 비교합니다.

두 정보를 함께 보면 당신의 벤치 기록이 세션마다 오르내리는 모습은 기록상으로는 보이지만, 제공된 기록만으로는 그 변동이 비정상인지 판단할 수는 없습니다. 문헌 쪽은 벤치프레스 성과가 반복 측정에서 어느 정도 흔들릴 수 있고, 연구에서는 이런 변화를 평균·표준편차·SEM·신뢰구간 같은 틀로 해석한다는 점을 보여줍니다. 따라서 현재 자료로는 ‘들쭉날쭉함’이 곧 이상 신호라고 말하기보다, 기록 변동 자체는 측정·훈련 맥락에서 해석해야 한다고 보는 편이 맞습니다.

제한사항: 기록의 weight 단위가 원자료의 미검증 라벨이라 절대 중량 해석에 제한이 있습니다.; 문헌은 일반적인 저항훈련/벤치프레스 측정 재현성과 해석 틀을 제공하지만, 개인 기록의 정상 범위를 직접 제시하지는 않습니다.; 제공된 문헌에는 벤치프레스 1RM의 직접적인 개인별 정상 변동폭 수치가 없습니다.

### T12

숄더프레스 하는 주랑 안 하는 주에 벤치 기록 차이가 있는지 보고, 보조 운동이 메인 운동에 도움 되는지 알려줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=5.077s.

미해결: `[{"field": "comparison definition for '하는 주' vs '안 하는 주'", "reason": "No explicit date range or week grouping criteria provided."}, {"field": "what counts as '보조 운동' and '메인 운동'", "reason": "The scientific question is broad and not tied to a specific accessory/main exercise pair beyond the bench press and shoulder press mention."}, {"field": "canonical_exercise_name", "reason": "여러 저장 운동이 있습니다. 조회할 운동을 선택해주세요."}]`.

다음 내용을 확인해주세요:
- 확인할 운동·기간·지표를 구체적으로 알려주세요.
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.
어떤 기록을 조회할까요?
1. Bench Press (Barbell)
2. Sling Shot Bench
3. close grip Bench

### T13

데드리프트 세트 수를 줄인 뒤로 기록이 오히려 좋아진 것 같은데, 기록으로 확인하고 볼륨을 줄이는 게 도움이 될 수 있는지 알려줘

HTTP 200; terminal=execution_failure; error_code=ungrounded_interpretation; LLM=1; latency=3.361s.

미해결: `[]`.

요청을 처리하는 중 내부 실행 오류가 발생했습니다. 근거가 완전하게 준비되지 않아 답변을 생성하지 않았습니다.

### T14

내 스쿼트 최고 기록 찍은 날이 언제야?

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=2.753s.

미해결: `[{"field": "record_operation", "reason": "Phase A의 전체/기간 기록 조회로 표현할 수 없는 세션 선택입니다."}]`.

다음 내용을 확인해주세요:
- 확인할 운동·기간·지표를 구체적으로 알려주세요.

### T15

지난 기록 중에 운동 제일 오래 쉬었던 기간이 얼마야?

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=2.386s.

미해결: `[{"field": "canonical_exercise_name", "reason": "조회할 운동의 종류 또는 변형을 명확히 지정해주세요."}]`.

다음 내용을 확인해주세요:
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.

### T16

바벨 벤치프레스 주간 볼륨 변화 보여줘

HTTP 200; terminal=answer_ready; error_code=None; LLM=2; latency=6.540s.

미해결: `[]`.

당신의 기록에서는 바벨 벤치프레스 주간 볼륨은 2016-08-22 주 3,705에서 시작해, 2017년에는 대체로 5,350~11,330 범위로 변동했고, 2018년에는 5,225~16,030까지 더 크게 출렁였습니다. 특히 2018-07-16 주에 16,030으로 가장 높았고, 최근 기록인 2018-09-24 주에는 7,840이었습니다. 전체적으로는 주간별 변동이 꽤 큰 편입니다. [tool-result-02-compute_metrics]

제한사항: 주간 볼륨은 기록된 세트만 반영한 계산값입니다.; 원본 데이터의 중량 단위는 검증되지 않은 소스 표기입니다.; 주간 기록이 없는 구간은 변화가 아니라 공백으로 보아야 합니다.

### T17

근비대가 목적이면 세트당 반복수는 몇 개 정도가 적당해?

HTTP 200; terminal=answer_ready; error_code=None; LLM=5; latency=10.726s.

미해결: `[]`.

문헌에서는 근비대 목적의 저항훈련에서는 세트당 반복수를 딱 하나로 고정하기보다, 여러 연구에서 대체로 6–12회 범위와 여러 세트가 함께 제시됩니다. 한 메타분석은 근비대 지향 훈련의 기초로 3–6세트, 세트당 6–12회, 중간 정도 강도와 짧은 휴식을 제안했습니다. 또 다른 연구들은 반복수 자체보다 총 훈련량(세트 수)과 점진적 증가가 근비대와 더 관련될 수 있다고 보고했습니다. 다만, 최적의 반복수 상한·하한을 정확히 정하기에는 아직 근거가 충분하지 않다고 했습니다.

제한사항: 근거는 반복수의 '정답'을 단정하기보다, 6–12회 범위와 여러 세트가 흔히 제시된다는 수준입니다.; 최적 반복수는 부하, 세트 수, 훈련 경험, 주당 볼륨에 따라 달라질 수 있지만, 제공된 근거만으로 개인별 처방은 할 수 없습니다.; 일부 연구는 실패지점까지의 훈련을 다뤘고, 반복수 범위와 직접적으로 1:1 대응하지는 않습니다.

### T18

프레스 기록이 왜 안 늘지?

HTTP 200; terminal=execution_failure; error_code=interpretation_validation_failure; LLM=1; latency=2.693s.

미해결: `[]`.

요청을 처리하는 중 내부 실행 오류가 발생했습니다. 근거가 완전하게 준비되지 않아 답변을 생성하지 않았습니다.

### T19

무릎이 아파서 스쿼트 기록이 떨어진 것 같은데 부상인지 판단해주고 재활 운동 짜줘

HTTP 200; terminal=execution_failure; error_code=interpreter_provider_failure; LLM=1; latency=2.114s.

미해결: `[]`.

요청을 처리하는 중 내부 실행 오류가 발생했습니다. 근거가 완전하게 준비되지 않아 답변을 생성하지 않았습니다.

### T20

내 기록 바탕으로 3개월 뒤 파워리프팅 대회 맞춰서 체중 감량 계획이랑 식단 짜줘

HTTP 200; terminal=abstain_ready; error_code=None; LLM=1; latency=3.888s.

미해결: `[{"field": "specific bodyweight target / rate of loss", "reason": "체중 감량 계획에 필요한 목표 체중 또는 감량 속도가 제시되지 않음"}, {"field": "diet constraints / preferences", "reason": "식단 작성에 필요한 음식 선호, 제한, 알레르기 정보가 없음"}, {"field": "canonical_exercise_name", "reason": "원문의 운동 표현을 확인해주세요."}, {"field": "time_condition", "reason": "분석 기간을 지정해주세요."}, {"field": "record_operation", "reason": "조회 작업을 지정해주세요."}]`.

다음 내용을 확인해주세요:
- 확인할 운동·기간·지표를 구체적으로 알려주세요.
- 어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요.
- 분석 기간: 어느 기간을 볼까요? 예: 2018년 9월~12월

