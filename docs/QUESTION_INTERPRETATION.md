# 선택형 질문 해석: Phase A / A.1

기존 frozen 평가를 보존한 post-baseline runtime 변경이다. 새로운 baseline 성능 수치가 아니다.

## 흐름

질문 → 좁은 규칙 해석 → 필요한 경우 LLM Interpreter 1회 → 결정론적 검증
→ interpretation 기반 Router → typed Argument Binder → 기존 workflow.

- 완전히 해석 가능한 질문만 규칙 경로로 처리한다.
- fallback은 동일 LLM 요청에서 질문 해석과 실제 DB 운동 후보 선택을 함께 한다.
- 기존 LLM Tool Input Resolver를 다시 호출하지 않는다.
- 날짜/N은 질문에서 확인되어야 하며 하나의 운동과 하나의 Metric만 실행한다.
- 불명확한 조건은 내부 clarification_required로 구분하고 API에서는 abstain_ready로 반환한다.

## 실행 모드

CMD에서 서버 실행 전에:

```bat
set AGENTIC_RAG_QUESTION_INTERPRETATION=phase_a
```

PowerShell에서 서버 실행 전에:

```powershell
$env:AGENTIC_RAG_QUESTION_INTERPRETATION = "phase_a"
```

환경변수 미설정 또는 legacy 값은 기존 runtime 경로를 사용한다.
이미 실행 중인 서버는 재시작해야 한다. DB/API 비밀값은 기존 안전한 로딩 절차를 사용한다.
이 문서는 key/password를 직접 명령행에 넣도록 권하지 않는다.

## 운동 후보 검증

DB의 training.exercises에서 실제 canonical 목록을 read-only로 조회한다.
LLM 출력 schema는 그 목록의 정확한 이름 또는 null만 허용하며 validator가 다시 확인한다.
원문 운동 표현, resolved/ambiguous/not_found, 기존 확정 alias 충돌도 검증한다.
규칙 경로 LLM 0회, fallback 최대 1회, automatic retry=0이다.

membership은 없는 이름 생성을 막지만 목록 안의 잘못된 의미 선택까지 보장하지는 않는다.
기존 전처리에서 통합한 이름과 runtime의 장비/그립 생략 해석은 구분한다.
Phase A에서는 승인한 해머컬 두 이름을 함께 조회한다. 서로 다른 set ID와 원래 이름은
보존하며, 값이 같은 이름 간 기록은 삭제하지 않고 해당 조회 범위의 Metric 실행을 차단한다.
일반 인클라인의 덤벨 추정과 로우의 close-grip 강제 변환은 금지한다.
이미 통합된 frozen 기록의 물리적 분리 조회는 아직 구현하지 않았다.
대소문자/공백 정규화와 서로 다른 운동 기록의 병합은 구분해야 한다.

## 예시와 범위

- 내 데드리프트 처음 3세션과 최근 3세션 median e1RM 비교해줘.
- 내 덤벨 해머컬 기록 좀 확인해줘.
- 내가 예전에 데드리프트할 때보다 요즘 얼마나 세졌는지 기록으로 한번 봐줘.
  (기간이 불명확하므로 확인 질문으로 종료해야 한다.)

복수 Metric, 새로운 계산법, 자동 운동 그룹 병합은 지원하지 않는다.
Retrieval/Grader/Recovery/Fusion/Top-10/MAX_RETRY 정책과 frozen artifact는 변경하지 않는다.

## 검증 및 공개 범위

Phase A.1 이후 승인 그룹/생략명 제한 테스트 29개를 추가했다. 기존 해머컬 모호성
테스트는 새 승인 그룹 정책으로 대체했고, 로컬 전체 suite는 445 passed였다.
실제 LLM 의미 정확도에 대한 독립 평가를 뜻하지 않는다.
원자료/논문 원문/frozen 결과/개별 live trace는 공개 저장소에 포함하지 않는다.
전체 suite는 로컬 연구 자료가 필요하며 공개 소스만으로 실행할 API 테스트는 README를 따른다.

## Phase A.2 운동 후보 선택

현재 실행 경로에서는 해머컬 자동 공동 조회 대신 후보 선택을 요청한다.
명시한 운동만 조회하고, 승인된 그룹의 공동 조회는 사용자가 선택했을 때만 수행한다.
앞서 설명한 자동 공동 조회는 이전 단계의 동작이다.
후속 요청 규격과 제한은 [운동 후보 선택](EXERCISE_CLARIFICATION.md)을 참조한다.
Phase A.2 신규 테스트는 23개, 전체 suite는 468 passed다.
