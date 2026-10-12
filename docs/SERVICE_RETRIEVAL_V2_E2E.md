# Service Retrieval V2: 실제 REST E2E 관찰

실행일: 2026-10-12 (Asia/Seoul). 아래 10개는 **AI가 작성한 실제 사용자 스타일의 정성적 smoke-test 질문**이다. Gold가 없으며 benchmark 또는 일반화 성능 평가셋이 아니다. 결과에 따른 코드·설정·prompt·validator·corpus·검색어 튜닝을 하지 않았다.

## 실제 서비스 구성과 실행

최신 master에서 `feature/service-retrieval-v2`를 만들었다. 실행 코드 변경은 `src/api/dependencies.py`의 서비스 기본 설정 선택뿐이다. 기존 실행 graph·Router·Interpreter/Resolver 정책·Grader·Recovery·최종 응답 계약을 유지한다. 서비스 해석 모드는 기존 기본값 `legacy`이다.

서비스가 사용하는 corpus는 **v1**, DB에는 v1 corpus/embedding run만 존재한다. corpus v2 Final 설정 hash는 그대로 보존했다. 서비스는 같은 검색 방식의 v1 결합 동결 설정 `retrieval_step3b_translation_v1.json`을 기본값으로 사용한다. 서비스 설정 hash: `02a7d398a7a1597e68a56a4e4d97bea5bb6a59fcd98ea7e7a6ae4bbf56a27330`.

문헌 첫 검색은 Step 2 생성 LLM → Step 3 영어 번역 LLM → Dense/BM25/RRF로 연결한다. Step 2 검증 실패는 원 질문, Step 3 검증 실패는 실제 Step 2 검색어를 사용한다. parent MiniLM(고정 revision, max_seq_length=128), Dense Top-10, BM25 positive_only Top-10, equal-weight RRF k=60, 최종 Top-10이다. child/reranker는 없다. Recovery 검색어는 기존 계약대로 생성/번역 단계를 다시 통과시키지 않는다.

정상 서비스 생성 함수로 만든 실제 runtime을 관찰 래퍼로 감싸고 로컬 Uvicorn `127.0.0.1:8012`에서 **실제 HTTP POST /query를 문항별 1회** 보냈다. provider/tool은 실제 OpenAI·PostgreSQL·pgvector를 사용했다. FastAPI 의존성 대체는 동일 실제 runtime의 상태 기록 래퍼만 주입했으며 합성 답변·검색·route를 주입하지 않았다. 모든 사용량은 실제 provider 반환 기록에서 수집했다. 실패 시 관찰 실행기는 재요청하지 않는다. 정상 실행 중 Recovery 최대 2회는 기존 서비스 정책이다.

단위/통합 연결 검증은 외부 호출 없는 합성 테스트로 별도 수행했다. 두 단계 성공/실패 네 조합, 검증 사유, 실제 전달 검색어, 명시적 H0·Step 2 설정 선택, legacy/phase_a 연결 계약을 검증했다.

## 문항별 요약

| 번호 | 질문 주제 | HTTP | Router | Grader 순서 | Recovery 시도/실제 검색 | terminal | LLM 호출 | input/output token | 추정 비용 USD | 전체 지연 초 |
|---:|---|---:|---|---|---|---|---:|---:|---:|---:|
| 1 | 근비대·반복수 | 200 | literature_only | completed / sufficient=True | 0/0 | answer_ready | 4 | 16515/618 | 0.01516725 | 19.589 |
| 2 | 훈련 빈도 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.020 |
| 3 | 실패 지점·여유 반복 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.020 |
| 4 | 디로드 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.020 |
| 5 | 벤치프레스 정체 | 200 | log_lookup | 미실행 | 0/0 | answer_ready | 1 | 6278/237 | 0.00577500 | 4.924 |
| 6 | 스쿼트 감소·공백 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.018 |
| 7 | 데드리프트 주간 볼륨 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.022 |
| 8 | 최장 운동 공백 | 200 | log_lookup | 미실행 | 0/0 | answer_ready | 1 | 2521/177 | 0.00268725 | 2.321 |
| 9 | 어깨 통증·진단·재활 | 200 | ambiguous | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.021 |
| 10 | 대회 식단·보충제 용량 | 200 | unsupported | 미실행 | 0/0 | abstain_ready | 0 | 0/0 | 0.00000000 | 0.022 |

## 전체 LLM 사용량

```json
{
  "calls": 6,
  "by_stage": {
    "step2": 1,
    "step3": 1,
    "grader": 1,
    "answer": 3
  },
  "input_tokens": 25314,
  "cached_input_tokens": 0,
  "output_tokens": 1032,
  "total_tokens": 26346,
  "estimated_cost_usd": 0.0236295,
  "usage_unavailable_calls": 0
}
```

추정 비용은 기존 동결 provider config의 pricing snapshot을 사용한 추정치이며 청구서 금액이 아니다. cached input token도 별도 집계한다. 응답/usage를 받지 못한 실패 호출은 소비 token·비용을 확정할 수 없다. 단계별 호출 수는 실제 provider invoke 횟수이며 graph 종료 상태의 개수로 추정하지 않았다.

## 관찰된 제한과 요청 충족 범위

- Q1에서 실제 Step 2 → Step 3 → Literature retrieval → Grader → 최종 답변 연결을 확인했다. Step 2는 `has_personal_context=false`로 원 질문을 사용했고, Step 3는 영어 번역 검증을 통과했다. 실제 보완 경로는 발생하지 않았다. 개인 문맥 분리·검증 실패 보완 경로는 합성 REST 네 조합으로 검증했고, 실제 hybrid 사용자 질문에서 확인된 것은 아니다.
- Q2·3·4·6·9는 `R-AMBIGUOUS`에서 structured/metric/literature 신호가 모두 false였다. Q7은 metric 신호만 true이고 structured/literature 신호가 false였다. 이 종료는 Retrieval miss 또는 Grader 실패가 아니다. 문헌 검색 자체가 실행되지 않았다.
- Q5는 `log_lookup/exercise_records`, Q8은 `log_lookup/list_sessions`만 실행했다. 두 문항 모두 Metric와 문헌 Tool이 실행되지 않아 요청한 정체 판정·변경 제안·공백 계산·근력 관련 문헌 요구가 충족됐다고 볼 수 없다.
- Q8에서 최종 답변은 답변용 세션 목록 생략 경계를 실제 장기간 운동 공백처럼 설명했다. 원 Tool 결과는 583개 세션이고, 답변 생성기는 기존 `first_10_and_last_10` 정책으로 563개 중간 세션을 생략한다. 답변이 공백 경계로 사용한 두 시점 사이에 실제 반환된 중간 세션이 존재함을 확인했다. 따라서 해당 공백 주장은 Tool 결과의 연속 세션 간격으로 뒷받침되지 않는다. `answer_ready`는 의미적 정확성을 보증하지 않는다. 결과에 따른 수정은 하지 않았다.
- Q9는 진단/재활 처방의 명시적 미지원 규칙으로 분류된 것이 아니라 일반 `ambiguous` 보류였다. Q10은 `R-UNSUPPORTED-MISSING-DATA`의 `식단` 신호로 보류됐고, 보충제 용량 처방 신호는 잡히지 않았다. 현재 건강 관련 범위 제한의 포괄성을 검증했다고 주장하지 않는다.
- 모든 문항의 Recovery 시도·검색 횟수는 0이다. 실제 Recovery/재검사 경로는 이번 10문항에서 관찰되지 않았다. 기존 합성 회귀 테스트에서 재검색 시 Step 2/3를 다시 호출하지 않는 계약은 확인했다.
- HTTP 요청 지연 합계는 26.977초이며 서비스 초기화·모델 로딩은 제외한다. API 오류·provider 실패는 관찰되지 않았고 모든 호출의 usage를 확보했다. 종료 상태는 answer_ready 3개, abstain_ready 7개이며 accuracy로 해석하지 않는다.

## 검증과 보존

| 검증 | 결과 | 실제 외부 호출 |
|---|---|---|
| 관련 단위 테스트 | 79 passed | 없음, 합성/mock |
| REST 계약·import 경계 통합 | 39 passed | 없음, 합성/mock |
| 기본값 변경 후 연결 계약 | 34 passed | 없음, 합성/mock |
| 공개 테스트 전체 | 830 passed, 81 deselected | 없음 |
| 로컬 전체 테스트 | 911 passed | 로컬 산출물 읽기, 실제 provider/DB 호출 없음 |
| pgvector 재현성 | 36/36 순서·ID 일치 | 실제 PostgreSQL SELECT, 로컬 encoder |
| 정성 REST 관찰 | 10문항 각 1회, HTTP 200 | 실제 OpenAI·PostgreSQL·pgvector |

workspace 테스트 수에는 작업 전부터 미추적이었던 `test_jats_parser_audit.py`가 포함된다. 이 파일은 commit하지 않았다. 첫 공개 테스트 실행의 2개 실패는 이전 H0 기본값을 고정한 연결 계약이었다. 명시적 H0 선택 검증을 유지하면서 요청된 서비스 기본값 계약과 provider 생성 검증을 추가한 뒤 전체 범위를 재실행했다. assertion을 제거하거나 marker/skip으로 검증 범위를 줄이지 않았다.

Windows sandbox의 asyncio socketpair 차단과 기존 pytest 임시 폴더 ACL 오류는 환경 문제로 분리했다. 정상 권한 및 새 작업 내 임시 폴더에서 동일 테스트 범위를 통과했다. 첫 실제 REST 실행은 자동 승인 검토가 외부 전송 승인 불명확으로 시작 전에 거절했다. DB의 모든 source hash가 문서에 명시된 공개 실험 CSV 하나와 같고, 입력은 사용자 지정 AI 작성 질문이며 SDK 목적지가 `https://api.openai.com/v1/`임을 확인한 후 재검토가 승인됐다. 거절된 요청은 전송 0회이며 승인된 관찰만 각 1회 실행했다.

기존 config·prompt, corpus v1/v2·selection/build, 평가/Gold, baseline, human decision, 기존 연구 보고서와 운동 alias를 포함한 1,267개 보호 파일의 작업 전/후 SHA-256이 모두 같았다. 새 결과 문서만 생성했고 기존 동결 결과를 덮어쓰지 않았다. 코드 diff의 실행 변경은 서비스 설정 선택 한 곳이며 validator·Router·Grader·Recovery·답변 생성 코드는 변경하지 않았다.

원본 결과 SHA-256: `rest/rest.json` = `6c91298fb10c54fd257d9cbf03ff64fd5f4cbf414971ba09b4732c68a719cebf`, `compare/comparison.json` = `ba38f43447584f2c33836df5178a51e9544a83bd048d0ee2bffe0bb81ea08da8`. 로컬 보호 hash 목록은 `reports/service_retrieval_v2/protected_{before,after}.json`에 보존한다.

## 상세 문서 보존

문항별 모든 실행 기록, 검색어, 논문 제목·section·parent/chunk ID, Log/Metric 주요 결과, Grader·Recovery, 최종 답변 전문은 로컬 `reports/service_retrieval_v2/E2E.md`와 `rest/rest.json`에 보존했다. 최종 답변은 그대로 저장했고 교정하지 않았다.

docs/PUBLISHING.md에 따라 개인 기록에서 계산한 상세 결과·raw provider 응답은 GitHub에 공개하지 않고 코드·테스트·관찰 요약만 commit한다.

corpus v2 Final 설정을 그대로 운영하려면 별도 승인된 corpus/embedding DB 이전이 필요하다. 이번 작업에서는 기존 corpus v1을 보존했다. AI 작성 10문항에서 관찰한 terminal 비율은 accuracy가 아니다. 테스트 성공도 연구 성능 향상을 뜻하지 않는다.
