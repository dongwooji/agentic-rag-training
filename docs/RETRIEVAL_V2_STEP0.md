# Retrieval v2 — 0단계 실행 안내

0단계는 H0 재현, 버전 검색 설정 분리, 사용자 검토를 거친 dev-ko 동결과 기준값 측정이다.
BM25 점수 0 후보 처리, 질문 분리, 영문 약어·숫자 정규화, child 검색 단위와 운영 pgvector
재현 확인은 이 단계에 포함하지 않는다. 자세한 단계 구분은 [실험 명세](specs/RETRIEVAL_V2.md)를 따른다.

## 검색 설정과 실행 경계

`config/retrieval_h0_v1.json`은 H0의 모델 revision, 검색 단위, BM25·RRF 설정과 frozen 자산의
검증 해시를 선언한다. `src/retrieval/runtime_config.py`가 설정을 검증하고,
`src/retrieval/hybrid.py`가 선언된 해시·문서 수·기존 baseline metadata와 대조한다.
기존 호출은 기본 H0 설정을 사용한다. 잘못된 설정, 지원하지 않는 정책, frozen 파일 변조는 거부한다.

검색 구성요소는 질문 문자열만 받고 Gold나 case label을 받지 않는다. 별도 평가 실행기가 검색 순위를
먼저 파일로 기록한 뒤 기존 개발셋 Gold를 연결해 지표를 계산한다. FastAPI 실행 경로는
`src.evaluation`을 import하지 않는다. 과거 전용 실험 코드는 현재 tree로 복원하지 않는다.

## H0 재현

실행에는 로컬 frozen Corpus v1, 개발셋·manifest, 기존 Dense/BM25/Hybrid baseline과 고정 모델 캐시가 필요하다.
공개 CI에 이 자료를 추가하지 않는다. 고정 revision을 오프라인 CPU로 실행하고 문서 벡터는 메모리에서만 계산한다.
DB 적재·쓰기, OpenAI 호출과 별도 검증용 평가 열람은 수행하지 않는다.

```powershell
python scripts/run_retrieval_v2_preparation.py --output reports/experiments/retrieval_v2_step0_v1/h0_after --reference-run reports/experiments/retrieval_v2_step0_v1/h0_before
```

`--reference-run`은 설정 분리 전 확보한 H0 실행과 비교할 때 사용한다. 없으면 기존 frozen baseline과 비교한다.
출력 경로는 `reports/experiments/` 아래 새 디렉터리여야 한다. 재실행할 때는 새 경로를 지정한다.
설정 변경은 `--config`, 모델 캐시 위치는 `--cache-dir`로 명시할 수 있다.

판정 기준은 실행 전 고정한다. Dense·BM25·Hybrid Top-10 ID·순서와 source rank는 정확히 같아야 한다.
검색 점수 절대 오차는 `1e-6`, 평가 지표 절대 오차는 `1e-12`까지 허용한다.
메모리 exact cosine 검증은 운영 PostgreSQL/pgvector 재현 검증과 구분한다.

## dev-ko 검토와 동결

개발 문항의 영어 용어는 한국 운동 커뮤니티에서 쓰는 표현으로 바꾸고 한글 음차를 허용한다.
RIR, RPE, 1RM, e1RM은 유지한다. 날짜·숫자·개인 기록 문맥·질문 의미는 보존한다.
약어·숫자를 유지하므로 완전히 영어 표기가 없는 평가셋이라고 설명하지 않는다.

로컬 초안은 `data/evaluation/dev_ko_v1/draft_questions.json`에 둔다. 사용자에게 실제 문항을 보여주고
확인받은 뒤에만 승인 기록을 만든다. 승인 기록은 다음 필드를 갖는다.

```json
{
  "actor": "user",
  "decision": "approved",
  "draft_sha256": "사용자가 확인한 초안 파일의 SHA-256",
  "evidence_ref": "실제 사용자 확인 메시지의 식별자 또는 인용",
  "approved_at_utc": "실제 확인 시각"
}
```

초안 해시가 승인 기록과 다르면 동결을 거부한다. 이미 생성된 동결 파일은 덮어쓰지 않는다.

```powershell
python scripts/freeze_dev_ko.py --draft data/evaluation/dev_ko_v1/draft_questions.json --source data/evaluation/eval_dataset_v1.json --approval .build/retrieval-v2-step0/dev_ko_user_approval.json --output data/evaluation/dev_ko_v1
python scripts/run_retrieval_v2_preparation.py --dataset dev-ko --output reports/experiments/retrieval_v2_step0_v1/h0_dev_ko
```

동결 산출물은 질문, 원문 대응표, 변환 규칙, 승인받은 초안 원본, 사용자 승인 기록과 해시 manifest다.
Gold는 복사하거나 새로 작성하지 않고 frozen 원 개발셋의 case ID로 연결한다.
동결·승인 기록이 없거나 파일 해시가 다르면 dev-ko 검색을 시작하지 않는다.

## 검증과 보고

설정·파일 변조·사용자 승인·숫자 및 약어 보존은 합성 테스트로 검증한다.
실제 모델을 이용한 H0 재현과 dev-ko 측정은 별도의 로컬 실험으로 보고한다.
단위·새 기능 통합·공개 CI·로컬 전체 테스트 순서로 실행하고,
Windows 임시 디렉터리 권한 오류는 검색 기능 실패와 구분한다.

개발셋 결과는 독립 일반화 성능으로 설명하지 않는다. 설정 선택은 Corpus v1 dev/dev-ko에서,
최종 별도 평가는 설정 확정 후 Corpus v2에서 수행한다. 0단계 완료 후 PR을 검토받고 멈춘다.
