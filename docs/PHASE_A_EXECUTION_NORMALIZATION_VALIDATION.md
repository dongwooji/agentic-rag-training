# PR 1 검증 기록

기준 master: `cbb92852b7ec8de3bce319b3323bcab2e51a1221`.
계약: [PHASE_A_EXECUTION_NORMALIZATION.md](PHASE_A_EXECUTION_NORMALIZATION.md).

acceptance v2 결과 분석 후의 post-hoc correctness 수정이다. 기존 acceptance 질문·Gold·raw·decision을 재실행하거나 변경하지 않았다. 기존 acceptance 수치를 수정 후 head의 성능으로 해석하지 않는다.

## 수정 및 회귀

동일 실행 Metric 중복 정규화, e1RM 기반 plateau 검증, 기간 생략/세션 순서 범위 정규화, 정확한 되묻기 문구를 수정했다. operation이 불확실한 요청, 실제 복수 Metric, 서로 다른 N, 미확정 비교 범위, 명시 날짜/미지원 상대 기간, 운동 후보 모호성과 grounding 실패는 계속 차단한다.

초기 공개 테스트에서 `기간별 비교`의 미확정 time이 잘못 해소되는 회귀 1건을 발견했다. 비교 기간 제약을 보존하도록 코드에서 수정했고 기존 테스트를 변경하지 않은 채 최종 공개/전체 테스트를 통과했다. 기존 목적 보존 테스트를 확인하며 기간 정규화도 Metric이 정해진 요청으로 제한했다.

## 결과

| 검증 | 결과 |
|---|---|
| 새 테스트 + 기존 해석/인자 단위·통합 | 183 passed |
| 최종 기간/전역 공백/semantic 회귀 | 126 passed |
| 공개: `python -m pytest -m "not requires_local_artifacts" -q` | 1,236 passed, 81 deselected |
| 로컬 전체: `python -m pytest -q` | 1,317 passed |
| 기존 보호 파일 SHA-256 | 2,081개 변경 없음 |
| 실제 LLM/REST/DB | 이 PR 검증에서는 호출하지 않음. PR 2 동작 확인에서 별도로 실행 |

로컬 테스트에는 사용자가 보유한 미추적 `test_jats_parser_audit.py`의 19개 테스트가 포함된다. 이 파일은 commit하지 않으므로 GitHub CI의 테스트 수는 19개 적다.

보호 hash 목록과 pytest XML은 ignored `.build/`에 보존했다. 기존 corpus·Gold·벡터·prompt/config·평가 보고서와 서비스 기본값은 변경하지 않았다. PR head와 GitHub Actions 결과는 PR 설명에 기록한다.

## PR 2에서 사용할 frozen synthetic

- 파일: `tests/fixtures/interpreter_extraction_smoke_v1.json`
- SHA-256: `6d07cb74db25fc88b7e7577845fede8fe5f1f8ba6735b80c2d779aedc1c76c99`
- 8문항, 설정별 1회, 자동 재시도 없음. 동작 확인이며 성능 평가가 아님.
- 계약 SHA-256: `c813a1d5938f1ab9fb0c5a547db0b98ae28874d8e2a189656170c2a85365797f`
- 로컬 freeze: `reports/interpreter_extraction_smoke_v1_20261019/FREEZE.json`
