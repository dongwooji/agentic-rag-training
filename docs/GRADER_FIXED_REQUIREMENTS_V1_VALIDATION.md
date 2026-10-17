# 고정 요구사항 Grader v1 검증·제한 기록

기준 master: `e7a61b58526b0e2cc048091d7eed2b13f5a38aee`.
계약 동결: `a5a4b44`. 평가한 구현: `454177751180e4a88853e1a3e09e40899e6ef0c3`.
결과는 [동결 판정 보고서](evaluations/grader_fixed_requirements_v1/REPORT.md)와
[dev decision](evaluations/grader_fixed_requirements_v1/dev_decision.json)에 있다.
이 기록은 평가 이후의 설명이며 코드·prompt·설정·채점·판정을 수정하지 않는다.

## 채택 보류 이유

기존→신규의 A(CE=1인데 부족)는 8→1, B(CE=0인데 충분)는 4→6,
실행 실패는 0→11이다. 합계는 12→7이지만 B와 실패가 늘어 동결 기준상 **신규 Grader 비채택**이다.
기존은 36/36, 신규는 25/36에서 정상 판정이 나왔다. 실패 때문에 A/B 집계 대상이 달라지므로
불일치 합계 감소만을 의미적 성능 개선으로 해석하지 않는다. 실패한 11입력도 전체36 분모에서 제외하지 않았다.

실패는 다음 두 범주로 구분된다.

| 범주 | 수 | 관찰된 원인 |
|---|---:|---|
| input_failure | 7 | 기존 동결 Step 2 검증 실패(dev5/dev-ko2). 원 질문 보완 경로의 문자열을 검증 성공으로 바꾸지 않았다. 동결 입력에 검증된 Phase A 대체 질문은 제공되지 않아 신규 Grader 호출 전 거절했다. |
| schema_failure | 4 | LLM이 출력한 source_span이 문헌 질문의 실제 부분 문자열이 아니어서 새 계약 검증에서 거절했다. |

후자는 dev HYB-003, dev-ko HYB-001/HYB-003/HYB-006이다. source_span 검증의 구조적 취약성/형식 부담과
근거 충분성 의미 판정을 분리해서 봐야 한다. 오류를 통과시키거나 문자열을 맞추기 위한 사후 수정은 하지 않았다.
이는 API 장애가 아니다. 전체 실제 provider 호출의 API 오류 기록은 0건이었다.

이번 비교는 기존 Grader와 신규 Grader 모두 동일한 원문·동결 Step 2 실제 질문·최초 Top-10을 받았다.
신규 버전에는 질문 출처 검증이 추가되어 입력을 거절할 수 있으므로, 모델의 의미 판정만 단독 비교한 결과라고 부르지 않는다.

## Recovery와 진단의 범위

신규 Grader의 정상 부족 판정은 1건으로 Recovery 관찰 기회도 1건이었다. 쿼리 생성·재검색·재검사 각1회로
최종 동작은 보류에서 답변으로 바뀌었으나, EGR/CE는 켬/끔 모두 0.752778/0.666667이었다.
실행 실패도 각각11건으로 같았다. 비악화 기준은 충족하지만 근거 회수 지표 개선이 확인된 것은 아니다.
신규 Grader가 비채택이므로 새로운 서비스 기본 동작을 채택하거나 배포하지 않았다.

dev decision SHA-256은 `e46555cb6a923430322113918bebae9d37f3e6db08fe3c66535d2148c797f8e2`이며
이를 봉인한 뒤 선택된 기존 Grader/Recovery 켬으로 held-out20을 각1회 진단했다.
CE는 0.05→0.05, EGR은 0.208333→0.195833, 마지막 Grader 기준 답변12/보류8/실행 오류0이다.
이 진단을 신규 Grader의 held-out 성능으로 표현하지 않는다. 진단 후 dev 판정 hash가 그대로임을 확인했다.

## 실제 호출과 테스트

- 구현 중: synthetic/unit/mock만 사용. 신규 Grader dev 사전 실행 0회.
- 평가 전 테스트: 신규 synthetic33, 공개1171(로컬 미추적 사용자 테스트 포함), 로컬 전체1252 통과.
- 실제 평가: dev36 및 판정 후 held-out20. dev 기존36회, 신규 초기29회/재검사1회, Recovery1회로 67 LLM 호출.
- held-out: 기존 Grader 초기20회/재검사16회, Recovery20회로 56 LLM 호출.
- 합계: **123회**, input634,117 / cached input12,288 / output23,098 token.
- 저장 가격 snapshot 기준 추정 비용 **USD 0.57123435**, LLM 호출 지연 합계 **208.891697초**.
- 최종 답변 생성·Interpreter·Step 2/3의 실제 LLM 호출은 모두0회. DB/실제 REST 요청도0회.
- 최초 검색은 기존 ranking 재생, Recovery 검색은 기존 pinned MiniLM128/벡터/동일 BM25·RRF의 offline 경로다.

공개/전체 회귀는 구현 완료 후에 수행했으며 신규 Grader dev 판정 실행과 구분한다.
CI는 같은 소스의 mock/offline 회귀이며 평가 결과의 성능 근거가 아니다. CI 결과와 최종 PR head는 PR 설명 및 완료 보고에 기록한다.

기존 Grader 코드·prompt·config와 corpus·Gold·검색어·벡터·기존 평가 결과는 보존했다.
보호 snapshot 1,702개 중 의도한 graph 전달 코드2개 외 **1,700개 hash 불변**,
동결 protocol 입력/설정30개 hash 불변이다. 기존 acceptance v1은 재실행하지 않았다.
서비스 기본값은 기존 Grader/legacy이며 optional Literature blocking 정책도 바꾸지 않았다.
Merge하지 않는다.
