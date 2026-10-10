# Reranker 후보 진단·CPU 평가 실행

사전 기준과 입력 계약은 [결정 기록](decisions/RETRIEVAL_V2_RERANKER.md)에 있다.
서비스 기본값은 legacy/H0다. 이번 실행기는 평가 전용이며 FastAPI에 reranker를 자동 연결하지 않는다.

## 준비

- 승인된 Step 2 생성/Step 3 번역 v2 동결본과 승인 기록이 필요하다. 새 LLM 호출은 없다.
- Step 3b 및 Step 5 parent 깊이 50의 동결 후보를 재사용한다. 입력 manifest와 파일 해시,
  검색어, corpus, 설정, 깊이 10/50 순위 prefix를 검증한다. 새 Dense/BM25 검색·embedding 계산은 없다.
- `requirements.txt`의 기존 transformers/torch 의존성을 사용한다. 새 framework는 추가하지 않는다.
- 아래 모델의 해당 revision만 Hugging Face 캐시 `data/models/huggingface`에 준비한다.
  네트워크 다운로드는 준비 단계이며 실제 평가는 offline이다. OpenAI 키·PGPASSWORD는 필요 없다.

```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="cross-encoder/ms-marco-MiniLM-L6-v2",
    revision="233902d25c440f23af6f7d6e94d2946bac0bee0a",
    cache_dir="data/models/huggingface",
    allow_patterns=["config.json", "model.safetensors", "tokenizer.json",
                    "tokenizer_config.json", "special_tokens_map.json", "vocab.txt"],
)
```

## 실행

```powershell
python scripts/run_retrieval_reranker.py --output reports/experiments/retrieval_v2_reranker_evaluation_v1
```

이미 존재하는 출력 경로는 거부한다. 반복이 필요하면 새 version을 쓰고 반복 이유를 기록한다.
조건은 A/B 두 개, 모델은 하나다. 모델·batch·thread·길이 조정 실험은 포함하지 않는다.

처리 순서:

1. 승인·동결 입력 검증 → 새 protocol 및 소스 snapshot 고정.
2. pinned CPU 모델 적재 → synthetic 1쌍 준비 실행.
3. 영어 검색어와 parent 원문 전체로 문항마다 A/B 평가. 두 조건의 순위·전체 점수·지연·토큰 기록 저장.
4. 이때 처음 dev Gold를 읽어 기존 `any`/`all` 규칙으로 후보 진단·지표를 계산.
5. 같은 후보 RRF 대비 변화 및 Step 3b 대비 채택 기준 자동 적용 → manifest 동결.

## 산출물과 해석

- `protocol.json`, `sources/`: 결과 확인 전 고정한 기준·입력 해시·실행 소스.
- `A/`, `B/`: 후보 ID 전체, 상위 10개, 모든 점수, 문항별 CPU 지연, 모델 tokenizer 길이/잘림 기록.
- `result.json`: a/b-only/c 진단, 후보 EGR/CE 상한, 언어·문항 종류별 지표, 문항별 변화,
  같은 후보 RRF 비교, 기준선 대비 채택 여부, 지연·잘림 집계, 모델 파일 해시와 실행 환경.
- `manifest.json`: 위 산출물의 SHA-256. 기존 corpus·Gold·query·embedding·baseline은 보존한다.

후보 상한은 후보 전체에 근거가 있는지를 뜻한다. 최종 10개 제약과 모델 오판 때문에 달성 보장은 없다.
지연은 문항별 입력 준비부터 정렬까지다. 모델 다운로드/적재와 synthetic 준비 실행은 따로 보고한다.
입력 잘림은 reranker tokenizer로 질문+parent+특수 토큰을 측정한다. MiniLM embedding 토큰 수와 다르다.
높은 reranker 점수는 Runtime Grader의 근거 충분성 판정을 대체하지 않는다.

held-out 질문·Gold는 열람하지 않는다. 이번 결과는 반복 사용한 개발셋 진단이며 독립 일반화 성능이 아니다.
