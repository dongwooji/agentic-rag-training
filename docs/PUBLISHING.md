# GitHub 공개 범위

## 업로드 대상

| 경로 | 용도 |
|---|---|
| `src/`, `scripts/`, `tests/` | 구현 코드, 실행 도구, 회귀 테스트 |
| `config/` | 비밀값 없는 정책·프롬프트·운동명 alias 정의 |
| `db/` | DB 스키마와 설정 절차(SQL dump 제외) |
| `requirements*.txt`, `pytest.ini` | 의존성과 테스트 설정 |
| `README.md`, `docs/` | 검토된 공개 설명과 aggregate 평가 요약 |
| `.env.example`, `.gitignore`, `.gitattributes` | 빈 설정 안내, 배포 제외 규칙, 파일 바이트 보존 |

## 로컬에 보존하며 업로드하지 않는 파일

- `data/raw/`, `data/processed/`: 원본 및 가공 운동 기록. 공개 데이터인지 여부와 무관하게 원 출처·라이선스 및 개인 기록 포함 여부를 확인하기 전 재배포하지 않습니다.
- `data/literature/`: 논문 원문과 chunks. 논문별 재배포 권한이 다를 수 있습니다.
- `data/evaluation/`: Gold 및 held-out 구성, 원문 evidence가 포함된 평가 자료.
- `data/models/`, `.build/`: 다운로드 모델과 재생성 가능한 build/cache.
- `reports/` 전체: frozen baseline, checkpoint, raw provider output, request metadata, 개별 기록, human-review evidence 전문 및 로컬 경로가 혼재합니다. 공개용 요약은 `docs/EVALUATION.md`로 별도 작성합니다.
- `PROJECT_PLAN.md`: 오래된 상태 표기와 상세 내부 연구 이력이 있는 원본 기획서. 최신 공개 상태는 README에서 설명합니다.
- Python cache, 가상환경, IDE/agent 로컬 설정, 로그 및 임시 파일.

제외는 삭제를 의미하지 않습니다. 특히 frozen baseline의 파일과 hash는 변경하지 않습니다. 공개 문서의 manifest hash는 이력을 식별하기 위한 값이며, 비공개 원본을 받지 않은 독자는 hash를 직접 재검증할 수 없습니다.

## 절대 커밋하지 않는 파일

- 실제 `.env`, DB password 파일, API token, credential JSON, 개인키·인증서 키
- DB dump/backup, 사용자 로그, 비밀값이 포함된 터미널 캡처
- 모델 다운로드 캐시, 전체 논문 PDF 등 별도 권리 확인이 필요한 자료

`.gitignore`는 이미 tracked된 파일을 보호하지 않습니다. 새 커밋마다 `git diff --cached --stat`과 실제 staged 내용을 검토하고, 비밀값이 발견되면 push 전에 제거합니다. 테스트에 쓰이는 가짜 credential 문자열은 실제 비밀값과 구분합니다.

## 재현성과 테스트 범위

코드와 테스트를 모두 보존하지만 데이터/산출물은 배포하지 않으므로 전체 suite와 실험 재현에는 로컬 자료가 필요합니다. README의 artifact-free API 테스트 명령은 clean source export에서 별도로 검증합니다. 과거 frozen 결과와 현재 post-baseline source는 같은 스냅샷이 아니며, 기존 baseline을 현재 코드로 덮어쓰지 않습니다.

문헌·데이터의 라이선스를 확인하지 않은 상태에서 포괄적인 데이터 라이선스를 부여하지 않습니다. 이 초기 공개 준비에서는 코드 재사용 라이선스도 임의로 추가하지 않았습니다.
