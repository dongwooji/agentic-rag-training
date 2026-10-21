# 설치와 실행

Python **3.13**(공개 CI), Node.js **22.12 이상**, PostgreSQL/pgvector를 사용한다. 개발·테스트 기준 환경은 Windows이며 공개 Python CI는 Ubuntu에서 실행한다. 프론트엔드 CI의 Node.js는 24다.

## 설치

저장소 루트에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-api.txt
```

`requirements-api.txt`가 `requirements.txt`를 포함한다. 일부 전이 의존성은 고정되어 있지 않으므로 설치 시점에 따라 조합이 달라질 수 있다.

## 환경변수

[`.env.example`](../.env.example)은 빈 설정 안내이며 자동 로드되지 않는다. 비밀값은 서버 프로세스 환경으로 전달한다. 명령행 인자·Git·공개 로그에 값을 넣지 않는다.

| 변수 | 용도 / 기본값 |
|---|---|
| `PGHOST`, `PGPORT` | DB 주소 / localhost, 5432 |
| `PGDATABASE`, `PGUSER` | DB와 사용자 / agentic_rag_training, agentic_rag_app |
| `PGPASSWORD` | 실제 `/query`용 DB 비밀번호 |
| `OPENAI_API_KEY` | 실제 `/query`용 OpenAI 키 |
| `AGENTIC_RAG_MODEL_CACHE` | 모델 캐시 / data/models/huggingface |
| `AGENTIC_RAG_RETRIEVAL_CONFIG` | 서비스 검색 config / config/retrieval_step3b_translation_v1.json |
| `AGENTIC_RAG_QUESTION_INTERPRETATION` | legacy(기본), phase_a |
| `AGENTIC_RAG_PHASE_A_EXTRACTION_VERSION` | Phase A의 original(기본), personal_metric_v1 |

Grader는 기존 legacy를 사용한다. 제거된 fixed-requirements 선택값은 지원하지 않는다. 실행 중인 서버에 환경 변경을 적용하려면 재시작한다.

## 데이터와 DB 준비

**공개 clone만으로 실제 `/query` 또는 과거 평가를 재현할 수 없다.** 원본 운동 기록, 논문 원문·corpus, frozen manifest·검색 자산과 모델은 배포하지 않는다. 보존 tag도 ignored 파일을 포함하지 않는다. 전체 재현에는 별도로 보관한 로컬 자산과 원 출처·라이선스 확인이 필요하다.

1. [`config/preprocessing_v1.json`](../config/preprocessing_v1.json)의 입력 형식에 맞춰 `data/raw/weightlifting_721_workouts.csv`를 준비한다. 공개 장기 운동 기록을 가상의 개인 기록으로 사용한다.
2. 신규 환경에서 `python scripts/preprocess_workouts.py`로 정규화하고 `python scripts/setup_postgres.py`, `python scripts/load_postgres.py`로 스키마·기록을 적재한다. 기존 frozen 자료를 다시 생성하거나 `--replace`로 덮어쓰지 않는다.
3. 문헌 준비는 [`config/literature_selection_v1.json`](../config/literature_selection_v1.json)과 [`config/literature_corpus_v1.json`](../config/literature_corpus_v1.json)을 따른다. `scripts/search_literature.py`는 PubMed 후보 검색, `scripts/build_literature_corpus.py`는 기존 후보 metadata·선정 목록·PMC XML을 이용한 신규 corpus 구축 도구다. 검색 후보를 다시 만들었다고 기존 frozen corpus와 같아지는 것은 아니다.
4. 운영 자산의 `data/literature/processed/{papers,chunks}.jsonl`, `data/literature/manifests/corpus_v1.json`을 준비한 뒤 `python scripts/load_literature_postgres.py`로 문헌을 적재한다.
5. `python scripts/setup_dense_retrieval.py`로 pgvector 스키마를 설치한다. 이 명령은 벡터를 생성·적재하지 않는다. 현재 서비스는 기존 embedding run과 corpus membership을 검증한다. 신규 준비 시 `MiniLMEncoder`와 `PgVectorStore.ensure_embedding_run` / `store_embeddings`를 이용하는 적재 API는 [`src/retrieval/dense.py`](../src/retrieval/dense.py), [`src/retrieval/postgres.py`](../src/retrieval/postgres.py)에 있다. 역사적 Dense 생성 실행기는 [보존 이력](HISTORY.md)에 안내한다. 완전 자동 bootstrap CLI는 없다.
6. `reports/baselines/hybrid_baseline_v1/`의 `manifest.json`, `reproducibility.json`, `bm25_index_metadata.json` 및 manifest가 가리키는 자산을 원래 바이트로 준비한다. 현재 runtime이 SHA를 검증하므로 빈 파일·다른 corpus로 대체할 수 없다. [`config/retrieval_step3b_translation_v1.json`](../config/retrieval_step3b_translation_v1.json)의 해시가 기준이다.

DB 세부 절차는 [db/README.md](../db/README.md), 현재 책임 분리는 [ARCHITECTURE.md](ARCHITECTURE.md)를 참고한다. corpus v2 빌더는 별도 selection/build manifest가 필요한 준비 도구이며 서비스 corpus 전환 명령이 아니다. 이 정리에서는 스키마·기존 데이터·frozen 자산을 변경하지 않았다.

## API 실행

```powershell
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Phase A와 개인 Metric 추출 버전을 명시적으로 선택하려면 서버 실행 전에 설정한다.

```powershell
$env:AGENTIC_RAG_QUESTION_INTERPRETATION = 'phase_a'
$env:AGENTIC_RAG_PHASE_A_EXTRACTION_VERSION = 'personal_metric_v1'
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Swagger는 `http://127.0.0.1:8000/docs`, ReDoc은 `/redoc`이다. `GET /health`, `GET /version`, 문서 확인에는 DB/API 자산이 필요하지 않다. `/health`는 liveness이며 DB 준비 완료 검사가 아니다.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$body = @{ question = '내 데드리프트의 처음 3세션과 최근 3세션 median e1RM을 비교해줘.' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/query -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

입력 부족 응답은 `response_metadata`를 확인한다. 운동 후보 선택은 반환된 `clarification_id`, 선택지의 `id`를 각각 `/query/clarify`의 `clarification_id`, `selected_option`에 보내 재개한다. 같은 서버 프로세스가 살아 있어야 한다. 응답의 `final_status`, `limitations`, 근거 ID를 함께 확인한다.

## 테스트와 프론트엔드

```powershell
python -m compileall src
python -m pytest -m "not requires_local_artifacts" -q
python -m pytest -q
cd frontend
npm ci
npm run lint
npm test
npm run build
npm run dev
```

`requires_local_artifacts`는 실제 로컬 산출물이 필요한 테스트만 분류한다. 첫 pytest 명령은 공개 CI와 같고, 전체 실행은 marker 테스트도 포함한다. CI는 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`로 실행하며 실제 OpenAI·DB·모델 다운로드를 검증하지 않는다. [Python workflow](../.github/workflows/ci.yml), [프론트엔드 workflow](../.github/workflows/frontend.yml)는 그대로 유지한다. 프론트엔드 workflow는 경로 필터가 있어 frontend 변경이 없는 PR에서는 실행되지 않을 수 있다.

실제 서비스 연결 확인 도구 `scripts/run_api_integration_smoke.py`, `scripts/run_end_to_end_smoke.py`, `scripts/observe_service_retrieval_v2.py`는 로컬 자산과 비밀값을 요구하며 결과를 `reports/`에 쓸 수 있다. 기존 보고서를 보존해야 하는 환경에서는 출력 경로와 덮어쓰기 동작을 먼저 확인한다. 연결 확인은 성능 평가와 구분한다. 프론트엔드의 실제 API·녹화 재생 실행법은 [frontend/README.md](../frontend/README.md)에 있다.

## 공개 범위

`data/`, `reports/`, `eval_frozen/`, 모델·embedding, 평가 상세 출력·임시 파일은 Git에서 제외한다. 실제 `.env`, credential, DB dump, 논문 전문과 provider 원본을 추가하지 않는다. 코드·데이터의 포괄적 라이선스를 임의로 부여하지 않았으며 자료 재배포 권한 확인이 필요하다.
