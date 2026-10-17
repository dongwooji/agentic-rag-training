# 고정 문헌 요구사항 Grader v1: 동결 후 최초 판정 실행

기준 master: `e7a61b58526b0e2cc048091d7eed2b13f5a38aee`. 계약 동결 commit: `a5a4b44`.
평가한 구현 SHA: `454177751180e4a88853e1a3e09e40899e6ef0c3`. protocol SHA-256: `2155eb9fefb2440664153d4caa304b5c17a15119cd9bf9674a3d24216f37651e`.

## 결과와 채택 판정

신규 Grader: **비채택**. 신규 Recovery 조건: **기준 충족**.
서비스 기본값은 판정과 무관하게 기존 Grader/legacy로 유지한다. 새 버전은 명시적 선택만 가능하다.

| 분할 | 입력 | 기존 A | 신규 A | 기존 B | 신규 B | 기존 합계 | 신규 합계 | 기존 실패 | 신규 실패 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 18 | 3 | 0 | 2 | 3 | 5 | 3 | 0 | 6 |
| dev-ko | 18 | 5 | 1 | 2 | 3 | 7 | 4 | 0 | 5 |
| combined | 36 | 8 | 1 | 4 | 6 | 12 | 7 | 0 | 11 |

A=CE=1인데 부족, B=CE=0인데 충분. 정상 완료 판정만 A/B로 집계하며 실패는 별도 계수하고 분모에서 제외하지 않는다.
판정은 합산36입력만 사용한다: A+B strict 감소 AND A/B 각각 증가 없음 AND 실행 실패 증가 없음. dev-ko는 동일 원본18개의 표현 변형으로 독립36문항이 아니다.

신규 Grader의 입력 출처 검증 실패(input_failure): 7건. 이 경우 LLM을 호출하지 않았으며 정상 판정을 얻은 것처럼 처리하지 않는다. 동결 Step 2 검증 실패와 Grader 의미 판정의 실패를 구분해서 해석해야 한다.

## Recovery 켬/끔

| 분할/조건 | 최종 EGR@10 | 최종 CE@10 | 답변/보류/판정 실패 | 실제 실행 오류 요청 | LLM 호출(공유 포함) | 평균 처리 지연(ms) |
|---|---:|---:|---|---:|---:|---:|
| dev/new_off | 0.697222 | 0.611111 | 12/0/6 | 6 | 13 | 1305.2 |
| dev/new_on | 0.697222 | 0.611111 | 12/0/6 | 6 | 13 | 1305.2 |
| dev-ko/new_off | 0.808333 | 0.722222 | 12/1/5 | 5 | 16 | 1743.9 |
| dev-ko/new_on | 0.808333 | 0.722222 | 13/0/5 | 5 | 18 | 1940.6 |
| combined/new_off | 0.752778 | 0.666667 | 24/1/11 | 11 | 29 | 1524.6 |
| combined/new_on | 0.752778 | 0.666667 | 25/0/11 | 11 | 31 | 1622.9 |

신규 최초 판정과 요구사항은 각 입력에서 1회만 실행·저장하고 켬/끔이 공유한다. 조건별 호출 합계에는 공유 호출이 중복되므로 전체 실제 호출 비용은 아래 별도 집계를 사용한다.
최종 동작은 마지막 Grader 판정에서 산출한다. 후속 검색/Recovery 오류는 별도 실행 오류 플래그에 남겨 감추지 않는다.
CE(on)>=CE(off) AND 실행 오류(on)<=실행 오류(off)를 만족해야 Recovery 기준을 충족한다. 검색은 기존 bounded Recovery와 runtime fusion을 그대로 사용한다.

## 문항별 결과

| 분할/case | 최초 CE | 기존→신규 초기 판정 | 켬/끔 EGR | 켬/끔 CE | 켬 최종 동작 |
|---|---:|---|---|---|---|
| dev/LIT-001 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-002 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-003 | 0 | 충분→충분 | 0.750/0.750 | 0/0 | answer_ready |
| dev/LIT-004 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-005 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-006 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-007 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-008 | 0 | 부족→충분 | 0.800/0.800 | 0/0 | answer_ready |
| dev/LIT-009 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/LIT-010 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/HYB-001 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev/HYB-002 | 0 | 충분→충분 | 0.000/0.000 | 0/0 | answer_ready |
| dev/HYB-003 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev/HYB-004 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev/HYB-005 | 1 | 부족→실패 | 1.000/1.000 | 1/1 | execution_failure |
| dev/HYB-006 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev/HYB-007 | 1 | 부족→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev/HYB-008 | 1 | 부족→실패 | 1.000/1.000 | 1/1 | execution_failure |
| dev-ko/LIT-001 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-002 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-003 | 0 | 충분→충분 | 0.750/0.750 | 0/0 | answer_ready |
| dev-ko/LIT-004 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-005 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-006 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-007 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-008 | 0 | 부족→충분 | 0.800/0.800 | 0/0 | answer_ready |
| dev-ko/LIT-009 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/LIT-010 | 1 | 충분→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/HYB-001 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev-ko/HYB-002 | 0 | 충분→충분 | 0.000/0.000 | 0/0 | answer_ready |
| dev-ko/HYB-003 | 1 | 부족→실패 | 1.000/1.000 | 1/1 | execution_failure |
| dev-ko/HYB-004 | 1 | 부족→충분 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/HYB-005 | 1 | 부족→실패 | 1.000/1.000 | 1/1 | execution_failure |
| dev-ko/HYB-006 | 0 | 부족→실패 | 0.000/0.000 | 0/0 | execution_failure |
| dev-ko/HYB-007 | 1 | 부족→부족 | 1.000/1.000 | 1/1 | answer_ready |
| dev-ko/HYB-008 | 1 | 부족→실패 | 1.000/1.000 | 1/1 | execution_failure |

고정 요구사항·문항별 판정·실패 코드·chunk ID는 [dev_results.json](dev_results.json)에 보존했다. 최초 요구 생성과 첫 판정은 같은 호출이므로 요구 생성이 검색 근거로부터 완전히 독립적이지 않다.

## 판정 후 held-out 진단

선택된 버전: `legacy`, Recovery: `True`. corpus v2 evidence-bearing 20입력 각1회. **diagnostic only**, 독립 검증/채택 평가 아님.
최초 EGR/CE: 0.208333/0.050000; 최종 EGR/CE: 0.195833/0.050000.
최종 동작: {'answer_ready': 12, 'abstain_ready': 8}. 실제 실행 오류 요청: 0.
dev decision을 먼저 hash로 봉인한 뒤 진단을 실행했다. 결과를 보고 설정·코드·prompt·Gold·결정을 변경하거나 다시 실행하지 않았다. 세부 결과는 [heldout_diagnostic.json](heldout_diagnostic.json).

## 호출·비용·지연

| 단계 | 실제 LLM 호출 | input/cached input/output token | LLM 지연 합계(s) | 저장 가격 기준 추정 USD |
|---|---:|---|---:|---:|
| dev | 67 | 401274/12288/12846 | 122.847 | 0.350468 |
| heldout | 56 | 232843/0/10252 | 86.045 | 0.220766 |

최종 답변 생성·Interpreter·Step 2/3 LLM 호출은 모두 0회다. 최초 Top-10은 저장 ranking 재생이며 Recovery만 검색한다. 지연은 이 Grader/Recovery 실험의 측정값으로 REST E2E 지연이 아니다. 온/오프는 순차 실행과 초기 판정 공유이므로 독립적인 전체 서비스 요청의 지연 비교로 해석하지 않는다.

## 검증·보존·한계

- 구현 중 신규 Grader에는 synthetic/unit/mock 입력만 사용했다. 계약·코드·테스트를 고정한 뒤의 dev 최초 실행이 판정 실행이다.
- 최종 답변을 생성하지 않았으므로 문장 품질, 답변 grounding, 전체 서비스 동작 정확도를 평가하지 않는다. answer_ready 비율을 accuracy라고 부르지 않는다.
- Gold는 실행 종료 후 scorer에서만 읽었다. 고정 chunk Gold 충족은 문헌의 의미적 충분성 전체를 대체하지 않으며 불일치 수는 Gold 일치도다.
- 이 변경은 과거 실행 trace 관찰 뒤의 post-hoc 개선이다. 기존 acceptance v1을 재실행하지 않았고 해당 결과는 `6c68ea996f967bcdda47bfed2671aae1ae583969` 기준으로 보존한다.
- corpus/벡터/기존 Grader 코드·prompt·config/기존 평가 결과와 서비스 기본값은 보존했다. 무결성 hash와 실행 SHA는 [integrity.json](integrity.json)에 기록했다.
- 단일 확률적 실행, 이미 분석된 개발 자료, 번역 쌍의 의존성, 모델/시점/캐시 영향이 있다. 일반화 성능 향상으로 주장하지 않는다.
- 테스트·CI와 최종 PR head는 PR 설명과 완료 보고에 별도로 기록한다. Merge하지 않는다.
