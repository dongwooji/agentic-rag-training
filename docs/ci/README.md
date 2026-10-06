# 공개 코드 CI

GitHub 공개 checkout에서 실행 가능한 코드 회귀와 기본 계약을 자동 확인한다. 연구 데이터, frozen baseline 및 실행 정책을 변경하지 않는다. 테스트 허용 목록은 사용하지 않으며, marker가 없는 새 테스트는 자동으로 CI에 포함된다.

## 실행 설정

- Workflow: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
- 트리거: `master` push, `master` 대상 pull request, 수동 `workflow_dispatch`
- Runner: `ubuntu-latest`, Python `3.13` 단일 버전
- 권한: `contents: read`
- 공식 [actions/checkout v6](https://github.com/actions/checkout)와 [actions/setup-python v6](https://github.com/actions/setup-python)을 사용한다. setup-python의 pip cache key에 두 requirements 파일을 포함한다.
- `requirements-api.txt`는 `requirements.txt`를 포함하므로 한 번의 설치로 기존 일반/API 의존성을 모두 설치한다.
- 사용자 GitHub Secrets, PostgreSQL service container 및 연구 DB는 사용하지 않는다.
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 설정한다. 테스트에서 실제 OpenAI, PubMed/PMC, 원격 논문 또는 embedding 모델을 요청하지 않는다. checkout/action/Python 준비와 pip 설치 자체에는 네트워크가 필요하다.

```sh
python -m pip install -r requirements-api.txt
python -m compileall src
python -m pytest -m "not requires_local_artifacts" -q
```

프로젝트에 Python 버전 선언, pyproject, .python-version 또는 Docker 기준이 없고 README도 버전을 지정하지 않는다. 실제 기존 개발 가상환경은 Python 3.13.7이다. 같은 버전으로 새 가상환경을 생성하고 기존 requirements 설치, `pip check`, 전체 테스트 관찰 및 공개 테스트 재검증을 통과했으므로 첫 CI는 3.13을 선택했다. setup-python은 3.13 계열의 patch 버전을 사용하며 matrix는 구성하지 않는다.

## marker와 테스트 추가

`pytest.ini`에 등록한 `requires_local_artifacts`는 공개 저장소에 없는 로컬/비공개 연구 산출물이 필요한 **개별 테스트 함수 또는 메서드**를 분류한다. skip, skipif, xfail 또는 파일 전체 marker를 추가하지 않았다.

```python
@pytest.mark.requires_local_artifacts
def test_frozen_artifact_integrity():
    ...
```

로컬 자료를 읽는 테스트에만 이 marker를 붙인다. 같은 파일 안의 mock, 합성 입력 및 공개 설정 기반 테스트는 계속 CI에서 실행한다. 예를 들어 `test_eda_workouts.py`는 실제 원본 CSV 계약 검사만 제외하고 나머지 3개를 실행한다. `test_exercise_candidate_resolution.py`는 실제 catalog CSV fixture를 사용하는 22개 node만 제외하고 2개를 실행한다. `test_phase8_tools.py`는 frozen literature asset을 요구하는 4개만 제외하고 Training Log / Metric fake 기반 10개를 실행한다.

현재 일부 `unittest.TestCase`의 `setUpClass`는 모든 메서드 실행 전에 로컬 자료를 읽는다. `DatabasePhase3Test`의 SQL helper, `EvaluationSetPhase5Test`의 freeze 명령 거부, `LiteratureCorpusTest`의 SQL/config 검사처럼 본문이 단순한 테스트도 이 초기화 때문에 로컬 의존성이 있다. 조사 JSON의 해당 node에 이 이유를 명시했다. 이를 더 분리하기 위한 fixture/클래스 변경은 이번 작업 범위에 포함하지 않았다.

일반 `python -m pytest -q`는 marker가 있는 테스트도 실행한다. CI만 `-m "not requires_local_artifacts"`로 해당 node를 선택 해제한다. 새 일반 테스트에는 marker나 JSON 항목을 추가해야 실행된다는 조건이 없다. [test_classification.json](test_classification.json)은 조사 시점의 기록이며 workflow 실행 목록이 아니다.

향후 실제 DB, API, 외부 서비스 또는 모델 다운로드를 요구하는 테스트를 추가할 때는 이 marker로 임의 분류하지 않는다. 먼저 의존성을 조사하고 별도 실행 범위를 설계해야 한다. 첫 CI에는 marker 하나만 등록했다.

## 2026-10-06 조사 결과

아래 실행 결과는 **Windows의 tracked-only checkout + 새 Python 3.13.7 가상환경**에서 측정했다. GitHub Ubuntu runner에서는 아직 실행하지 않았으므로 GitHub Actions 성공으로 표현하지 않는다. 첫 push/PR 또는 수동 실행의 Actions 결과로 Ubuntu 동작을 확인해야 한다.

| 항목 | 개수/결과 |
| --- | ---: |
| 공개 checkout 수집 node | 552 |
| parametrization 전 고유 함수/메서드 | 343 |
| CI_SAFE | 471 node (274 함수) |
| REQUIRES_LOCAL_ARTIFACTS | 81 node (69 함수) |
| NEEDS_REVIEW | 0 |
| marker 추가 | 69 함수, 81 node, 18 파일 |
| marker 전 전체 공개 실행 | 471 passed, 13 failed, 68 setup errors |
| marker 후 CI 실행 | 471 passed, 0 failed, 81 deselected |
| 전체 collect-only | 552 collected |
| CI collect-only | 471/552 collected, 81 deselected |
| README의 API 테스트 3개 별도 실행 | 15 passed |
| 원래 작업 폴더의 전체 실행 | 571 passed, 제외/skip 없음 |
| compileall / pip check | 모두 성공 |

원래 작업 폴더의 571개에는 작업 전부터 미추적 상태였던 `tests/test_jats_parser_audit.py`의 19개 node가 포함된다. 이 파일과 관련 미추적 Corpus v2 설계/감사 작업을 공개 checkout에 복사하거나 이번 변경에 포함하지 않았다. README의 과거 전체 suite 수치는 이 시점의 측정값으로 덮어쓰지 않는다. README가 공개 실행 가능하다고 안내한 `test_api_health.py`, `test_api_query.py`, `test_api_smoke_v2_harness.py`의 모든 node는 CI_SAFE이며 주장이 실제 동작과 일치했다.

최초 공개 전체 실행의 81개 실패는 모두 로컬 파일 누락이었다. 오류 경로가 `/data/`, `/reports/` Git 제외 정책에 해당함을 확인하고, fixture/공통 초기화/호출 경로를 검토했다. 해당 자료의 원본 또는 frozen 과거 산출물이 공개 저장소에 없어 공개 checkout만으로 재생성할 수 없다. 같은 node는 로컬 자료가 있는 전체 실행에서 모두 통과했다. 단순 경로 오타나 코드 회귀를 로컬 의존성으로 숨긴 사례는 발견하지 않았다.

관찰에 사용한 네트워크 차단 장치는 초기에 Windows asyncio의 내부 loopback socket pair까지 막아 로컬 실행에서 26개 실패를 유발했다. 장치를 수정해 내부 loopback만 허용하고 외부 연결은 차단한 후, 위 공개/로컬 검증을 수행했다. 이 초기 관찰 장치 오류는 marker 분류 근거로 사용하지 않았다. 테스트 실행 중 실제 DB/API/외부 네트워크 연결과 모델 다운로드는 없었다.

실행에는 Starlette의 `anyio.abc.BlockingPortal` deprecation warning 1개가 있었다. warning을 숨기거나 의존성/application code를 변경하지 않았다. 새 환경에서 확인한 주요 버전은 pytest 9.1.1, numpy 2.5.3, pandas 3.0.6, Pydantic 2.8.2, FastAPI 0.115.14, OpenAI SDK 3.3.1, LangGraph 1.2.11, sentence-transformers 5.7.0이다. 전체 설치 버전은 조사 JSON에 남겼으며 CI 설치용 lockfile로 사용하지 않는다. 기존 requirements의 일부 직접/전이 의존성은 고정되지 않아 이후 설치 조합이 달라질 수 있다.

## 조사 방법과 재확인

1. 작업 전 `git status`와 추적 목록을 기록했다. 추적 파일 192개만 `.build/`의 별도 checkout으로 복사하고 새 venv를 만들었다. `.env`, data, reports, 로컬 corpus/모델 및 미추적 연구 파일을 복사하지 않았다.
2. 기존 requirements를 변경하지 않고 설치했다. 첫 설치 시 도구의 네트워크 제한(WinError 10013)이 발생했으나 허용된 네트워크 실행으로 재설치가 성공했다. 패키지 부재/호환성 실패로 분류하지 않았다.
3. API key, PostgreSQL 설정/비밀번호, 질문 해석 실행 모드 설정을 제거한 자식 프로세스에서 compileall과 전체 pytest를 실행했다. 별도 관찰 plugin으로 collection과 각 node의 setup/call 결과를 기록했다. 외부 socket/DNS 연결을 차단하고 빈 HF cache와 offline 설정을 사용했다. 기존 fixture/mock/assertion은 변경하지 않았다.
4. 오류 경로, `.gitignore`, Git 추적 상태, 원래 로컬 파일 존재 및 source를 대조했다. 실패 node의 함수에만 marker/import를 추가했다. 18개 테스트 파일의 AST에서 해당 import/marker만 제거해 원본과 비교했으며 assertion, 예상값, fixture와 테스트 본문이 동일했다.
5. 별도의 새 tracked-only checkout에 marker 변경을 반영해 compileall, CI 실행, 전체/CI collection을 검증했다. 수집 차집합은 정확히 81개 로컬 node였다. 원래 폴더의 전체 pytest에서도 그 81개를 포함해 모두 통과했다.
6. 작업 전후 `data/`와 `reports/`의 673개 파일 해시 및 파일 집합을 비교해 동일함을 확인했다. src, runtime/config, frozen 자산과 기존 연구 결과를 변경하지 않았다.

커밋된 공개 내용으로 직접 재확인하려면 새 위치에 checkout하거나 `git archive HEAD`를 사용한다. 기존 data/reports가 있는 작업 폴더를 검증용으로 재사용하지 않는다. secret을 설정하지 않은 셸에서 새 venv를 만들고 위 설치/CI 명령을 실행한다. 수집 확인은 다음과 같다.

```sh
python -m pytest --collect-only -q
python -m pytest -m "not requires_local_artifacts" --collect-only -q
python -m pytest tests/test_api_health.py tests/test_api_query.py tests/test_api_smoke_v2_harness.py -q
```

조사 JSON은 실제 수집된 node ID, 분류, 의존성 flag, 최초 공개 실행 결과, 최종 CI 실행 결과, 로컬 전체 결과를 담는다. `clean_checkout_result=FAIL`은 최초 전체 실행의 setup error도 포함한다. 제외된 node의 `clean_checkout_ci_result=NOT_RUN`은 최종 CI 선택 해제를 뜻한다. `requires_openai`는 SDK import가 아니라 실제 API/key 의존성, `requires_network`는 외부 연결, `requires_private_data`는 Git에서 제외한 data/corpus/evaluation 입력 의존성이다. reports만 필요한 node는 별도 flag로 표시한다. flag는 mock의 이름이 아니라 실제 실행 의존성을 나타낸다.

## 검증 범위와 한계

CI 성공은 공개 코드의 기본 회귀 확인이다. RAG/Retrieval 성능, corpus 품질, frozen 평가 결과, 실제 PostgreSQL의 운동 기록 및 pgvector 검색, OpenAI 응답, 개인 기록에 기반한 실제 API E2E를 검증했다는 뜻이 아니다. CI에 포함된 평가 지표의 합성 단위 테스트도 연구 성능 실험과 구분한다.

실제 DB, 비공개 원본 데이터, corpus/embedding artifact, Gold/평가셋, 과거 baseline/report의 무결성 검증은 필요한 로컬 자료가 있는 전체 suite에서 수행한다. 실제 외부 네트워크/모델 다운로드 CI, DB service/synthetic DB fixture, Docker, 배포/CD, repository settings 및 Corpus/Retrieval 후속 실험은 이번 작업 범위 밖이다.

## 변경 파일

새 파일은 workflow와 이 README, 조사 JSON이다. 기존 파일은 `pytest.ini`와 아래 테스트 18개이며 import/marker만 추가했다. README 본문과 application code는 수정하지 않았다.

| 테스트 파일 | marker 함수 | 해당 node |
| --- | ---: | ---: |
| [tests/test_answer_integration.py](../../tests/test_answer_integration.py) | 1 | 1 |
| [tests/test_database.py](../../tests/test_database.py) | 5 | 5 |
| [tests/test_e2e_smoke_harness.py](../../tests/test_e2e_smoke_harness.py) | 2 | 2 |
| [tests/test_eda_workouts.py](../../tests/test_eda_workouts.py) | 1 | 1 |
| [tests/test_evaluation.py](../../tests/test_evaluation.py) | 8 | 8 |
| [tests/test_evidence_human_review_v2.py](../../tests/test_evidence_human_review_v2.py) | 8 | 8 |
| [tests/test_exercise_candidate_resolution.py](../../tests/test_exercise_candidate_resolution.py) | 11 | 22 |
| [tests/test_graph_recovery_loop.py](../../tests/test_graph_recovery_loop.py) | 1 | 1 |
| [tests/test_literature.py](../../tests/test_literature.py) | 7 | 7 |
| [tests/test_phase8_tools.py](../../tests/test_phase8_tools.py) | 4 | 4 |
| [tests/test_post_baseline_v1_remediation.py](../../tests/test_post_baseline_v1_remediation.py) | 2 | 3 |
| [tests/test_preprocessing.py](../../tests/test_preprocessing.py) | 8 | 8 |
| [tests/test_question_interpretation.py](../../tests/test_question_interpretation.py) | 1 | 1 |
| [tests/test_recovery_agent.py](../../tests/test_recovery_agent.py) | 1 | 1 |
| [tests/test_recovery_evidence_fusion.py](../../tests/test_recovery_evidence_fusion.py) | 1 | 1 |
| [tests/test_retrieval_metrics.py](../../tests/test_retrieval_metrics.py) | 6 | 6 |
| [tests/test_runtime_evidence_grader_integration.py](../../tests/test_runtime_evidence_grader_integration.py) | 1 | 1 |
| [tests/test_tool_input_resolver.py](../../tests/test_tool_input_resolver.py) | 1 | 1 |
