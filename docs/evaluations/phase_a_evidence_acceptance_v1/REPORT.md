# Phase A evidence acceptance v1 — 최종 보고

## 1. 평가셋 SHA

질문·Gold가 지정 SHA와 일치했고, 5개 원본 파일 모두 변경하지 않았다. 최초 hash 기록 → 질문/manifest만 읽음 → baseline 20회 동결 → changed 20회 동결 → 두 manifest receipt → 정답 파일 첫 열람 순서를 지켰다.

| 파일 | SHA-256 |
|---|---|
| questions.jsonl | `e8f7609df2ec70e92c413914bca0ccf179436026859b692b5cda3c452f3fc5fc` |
| gold.json | `ba4ae2af655bb401344d3058af7243d94ef0f3bc082ec43a8a14f40fda25b7ae` |
| REVIEW.md | `03bcaebe0d1f7489473b5664dc15db0274a8a58644d25cad19f3a5bd7ed109de` |
| build.py | `3268c4e718f7a5cd1ac271e55e5c9646c1351c8d593f56ef489c17f05d8c57e1` |
| manifest.json | `0b0a038c67de03d75b5733e6059bbf8c2d115eeca6b49cf48cdb123894cb81d7` |

## 2. 실행 SHA와 환경

- Baseline: `685e2b9194419fbf757df2df2156299b77e4afe3`.
- Changed: `6c68ea996f967bcdda47bfed2671aae1ae583969`.
- 별도 clean worktree, 모두 phase_a. 동일 DB fingerprint, corpus v1, 기존 pgvector 벡터, MiniLM 128, Final V2 retrieval, catalog 및 LLM 모델/인자. 16개 환경 비교 항목 모두 일치.
- Interpreter prompt/typed semantics는 두 버전의 의도된 차이. 두 preflight에 prompt/config 및 corpus/manifest SHA, 모델 설정을 기록했다.
- 실제 `/query` 요청 각 20회, debug=true, 순서 유지, 재시도·clarification 후속 호출 없음. 운영 서비스 기본값 legacy 유지.

## 3. 먼저 동결한 필수 기준

| 기준 | baseline | changed | 판정 |
|---|---:|---:|---|
| required-channel omission | 6 | 0 | PASS |
| expected-answer 부당한 clarification/abstain | 12 | 9 | PASS |
| final behavior accuracy | 5/20 | 10/20 | PASS |
| frozen graph observer의 execution_failure | 0 | 0 | PASS |

[판정 파일](decision.json)을 먼저 동결하고 상세 분석을 수행했다. 원본 Gold의 최상위 schema_version 키를 기존 scorer가 받는 version으로 연결한 입력 사본만 만들었다. 원본 hash와 모든 case/라벨은 동일하며 채점 코드·규칙은 변경하지 않았다.

## 4. Acceptance 결론

**고정 scorer 기준 PASS. 서비스 REST 안정성 통과로 볼 수는 없다.** Changed의 EN-03A/B, EN-10B는 실제 HTTP 500이다. graph 내부 clarification을 관찰하는 기존 scorer는 API 응답 변환 실패를 execution_failure로 집계하지 않는 한계가 있다. API 경계 실패는 0→3으로 별도 보고하며 판정을 결과에 맞춰 바꾸지 않았다.

## 5. 문항별 비교

20문항 비교표와 contrast pair는 [ACCEPTANCE.md](ACCEPTANCE.md), 각 need/operation/Grader/Recovery/되묻기 사유·지연·token·답변 전문은 [DETAILS.md](DETAILS.md)에 있다. 원시 HTTP body/JSON과 실제 Tool 실행은 baseline/changed 디렉터리에 각각 보존했다. HTTP 500에는 없는 공개 답변이나 trace를 만들어 넣지 않았다.

## 6. 보조 지표와 검증

- Record 정답 9→12/20, Metric 정답 10→13/20. changed answer_type 14/20. 최근 기간 내부 적용 판정 16→20/20이나 HTTP 오류 3건에서는 사용자 응답에 전달되지 않았다.
- 불필요 채널 추가 0→1, optional→required 승격 1→1.
- Acceptance LLM 32→52회. input 136769→246150, output 6464→10615. 추정 비용 USD 0.10868235→0.1687896 (합계 0.27747195). 실제 청구 비용은 미제공(null).
- 평균 지연 5.347→7.734초, p95 13.006→18.739초. stage별 호출과 Tool 조합은 [auxiliary_metrics.json](auxiliary_metrics.json).
- 관련 synthetic/채점 회귀 38 passed. 공개 1056 passed / 81 deselected. 로컬 전체 1137 passed.
- 기존 보호 파일 1461개 및 평가 원본 5개 hash 불변. 사용자의 기존 미추적 파일 7개 보존. 비밀값 scan 검출 0.
- runner/test/원본의 diff 검사 통과. 전체 diff에는 동결한 REGRESSION.md 끝의 빈 줄 1건이 남아 있으며, hash 보존을 위해 수정하지 않았다.

## 7. 기존 interpretation test 회귀 진단

Acceptance의 판정·상세 동결 후 changed에서 test 20문항을 각각 1회 실행했다. [REGRESSION.md](REGRESSION.md) 참조.

| 지표 | historical phase_a_fixed | 이번 changed |
|---|---:|---:|
| 경로 정답 | 2/20 | 4/20 |
| 동작 정답 | 9/20 | 7/20 |
| graph execution_failure | 1 | 3 |
| HTTP/전송 실패 | 0 | 3 |
| 부당한 보류·되묻기 | 8 | 6 |
| 채널 일치(보조) | 11/20 | 16/20 |

역사적 결과는 PR #14 직후 기록으로 지정 baseline SHA의 이번 실행과 다르다. 단일 probabilistic run이므로 변경 효과와 LLM 변동을 분리할 수 없다. acceptance 판정에는 사용하지 않았다. 추가 LLM 33회이며 1회 usage 미제공으로 token/비용 총합은 null. 관측된 소계는 input 126685 / output 7531 / 추정 USD 0.09935445이다. 전체 작업은 REST 60회, LLM 117회이며 모든 호출의 실제 청구 비용은 미제공이다.

## 8. 한계와 실패

- HTTP 500 원인은 최근 8주 정보 default_time_window와 공개 ClarificationMetadata의 extra=forbid 충돌. 저장 metadata만으로 동일 validation error를 오프라인 재현했다. 코드는 수정하지 않았다.
- optional Literature는 선택되면 기존 blocking semantics를 따른다. EN-06B는 optional 표현과 literature_needed_for_answer=true가 함께 나와 required로 승격됐고, Grader/Recovery 후 전체 보류됐다.
- 전반적 training_gap에 운동을 되묻는 문제, plateau 관련 추가 clarification, 수치 비교의 “왜”에 문헌을 추가하는 문제 등이 남았다.
- Weighted dips 같은 운동명은 LLM/catalog 연결 품질에 영향을 받을 수 있다. 이번 HTTP 500의 딥스 두 건에서는 canonical Weighted dips 확정은 성공했으므로 운동 후보 실패로 설명하지 않는다.
- 제한된 frozen acceptance 20문항이며 일반화 증거가 아니다. 분산·통계적 유의성과 답변 문장 전체의 독립 사람 평가는 수행하지 않았다. 고정 순서/shared cache가 지연과 cached token에 영향을 줄 수 있다.

## Git 및 변경 범위

결과 branch `eval/phase-a-evidence-acceptance-v1`, PR base `feature/phase-a-evidence-needs`. 이번 diff는 원본 평가 파일 5개, 외부 관찰 runner와 synthetic 테스트, 새 평가 결과/보고서뿐이다. runtime code/prompt/config, 기존 Gold·벡터·corpus·서비스·Router·Grader·Recovery는 변경하지 않았다. PR #20과 평가 PR은 merge하지 않는다.

GitHub Actions의 기존 public workflow는 master 대상 PR만 자동 실행한다. 요청한 구현 branch base에는 자동 CI가 발생하지 않는 정책이며 workflow는 수정하지 않는다. 실제 PR 상태/CI 확인은 최종 응답에 기록한다. CI 성공 여부를 의미 성능 개선으로 해석하지 않는다.
