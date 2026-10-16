# Phase A evidence acceptance v1 — 실행 전 고정 절차

- Baseline: `685e2b9194419fbf757df2df2156299b77e4afe3`.
- Changed: `6c68ea996f967bcdda47bfed2671aae1ae583969` (PR #20, merge하지 않음).
- 두 별도 clean worktree에서 `AGENTIC_RAG_QUESTION_INTERPRETATION=phase_a`.
- 서비스 기본값 legacy, runtime code/prompt/config/Gold/질문/기준을 변경하지 않음.
- 같은 명시적 `--secrets-json`, DB, corpus v1, pgvector 벡터, retrieval 설정, Python 환경 사용.
- 실제 Uvicorn/FastAPI `/query`에 `debug: true`, 파일 순서 각 20회, 한 턴.
- 외부 runner가 provider의 Responses 호출을 위임하며 usage만 관찰. 실제 Tool 실행은 반환 state의 실행 기록으로 확인. 기준선에 코드를 이식하지 않음.
- 요청 전 exclusive intent 파일을 작성. 실패/중단/불명확한 transport도 재시도하지 않음. 원시 HTTP body도 그대로 보존.
- SDK max_retries=0을 확인. 기존 bounded Recovery는 서비스 동작이므로 유지하고 회차별 trace에 기록.
- 응답 usage가 없으면 null. 알려진 소계는 별도. 가격표 기반 추정 비용과 실제 청구 비용을 구분; 실제 비용 미제공은 null.

## Blind 경계

1. 평가 파일 5개 SHA만 확인. `input_freeze.json`에 기록.
2. 질문과 manifest만 읽음. Gold, REVIEW, build는 hash-only.
3. baseline 20회 → raw/run manifest hash와 FREEZE 파일.
4. changed 20회 → raw/run manifest hash와 FREEZE 파일.
5. 두 실행 동결을 묶는 receipt 저장.
6. 처음으로 Gold/REVIEW/build를 읽고 고정 scorer로 오프라인 채점.
7. 아래 4개 기준 판정과 입력 hash를 먼저 동결. 이후 상세·contrast pair 분석 동결.
8. 그 뒤에만 interpretation_eval_v1 test 20문항을 changed 설정에서 1회 회귀 진단. historical 결과와 비교하되 acceptance 판정은 변경하지 않음.

## 고정 판정

`src/evaluation/phase_a_semantics.py`와 `scripts/score_phase_a_semantics.py`의 변경 SHA 버전을 그대로 사용.

1. required-channel omission 수: changed < baseline.
2. expected answer의 부당한 clarification/abstain 수: changed < baseline.
3. final behavior accuracy: changed ≥ baseline.
4. execution_failure 수: changed ≤ baseline.

네 기준이 모두 참이면 acceptance PASS. 채널 판정 누락과 실제 실행 누락은 구분. answer_ready만으로 정답 처리하지 않음. 기준선 literature_requested=true는 required Literature, answer_type은 N/A. optional의 누락은 허용하되 Gold의 optional을 required로 승격한 경우 별도 집계.

## 해석 제한

optional Literature도 실제 선택되면 현재 graph의 blocking Grader/Recovery/종료 의미를 유지한다. 단일 probabilistic run이므로 분산·통계적 유의성을 평가하지 않는다. 이 제한된 frozen 20문항은 일반화 증거가 아니다. weighted dips 같은 운동명 해석은 LLM과 catalog 후보 연결 품질의 영향을 받을 수 있다. variant 고정 실행 순서와 shared cache가 latency 및 cached token에 영향을 줄 수 있다.

평가 결과는 별도 branch/PR로 저장하고 PR #20과 평가 PR을 merge하지 않는다. 질문·Gold 등 원본 5개는 사용자의 명시적 commit 요청에 따라 원래 경로에서만 추가한다. 다른 ignored 연구 자료나 비밀값은 추가하지 않는다.
