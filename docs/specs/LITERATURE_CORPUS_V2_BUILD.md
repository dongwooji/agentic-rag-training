# Corpus v2 build freeze v1

이 단계는 frozen MAIN selection 26편을 실제 corpus로 만드는 작업이다.
Retrieval, embedding/index, 평가셋/Gold, Router/Grader/Recovery 변경은 포함하지 않는다.

## 입력과 실행

- Selection: `data/literature/v2/manifests/selection_freeze_v1/selection_manifest.json`
- 고정 SHA256: `b8aac64d65222093db9370d2cbb954dd0615d36a074cedc3915f3dff8f9b526c`
- 설정: `config/literature_corpus_v2_build_v1.json`
- 실행: `scripts/build_literature_corpus_v2.py`
- 구현: `src/literature/build_v2.py`
- 테스트: `tests/test_corpus_v2_build.py` (합성 fixture만 사용)

기존 `extract_sections`, `paper_metadata`, `build_paper_and_chunks`, `_chunk_section`,
`write_jsonl`, `sha256_file`을 재사용한다. parser/chunker 및 기존 설정을 수정하지 않았다.
목표 350단어, 최소 80단어, 최대 550단어, overlap 60단어이며 기존 짧은 tail 병합은
최대 610단어를 허용한다. selection의 supporting question ID나 Gold를 parser/chunker에
전달하지 않는다. evidence preservation 설정은 build 이후의 검사에만 사용한다.

실행 전에 모든 기존 data/reports, v1 설정, selection, source, baseline, 사용자
미커밋 파일의 상대 경로와 SHA256을 별도 JSON map에 기록한다. 실제 작업에서는
880개 파일을 기록하고 실행 전후 전부 대조했다.

```powershell
python scripts/build_literature_corpus_v2.py `
  --protected-snapshot .build/corpus-v2-validation/protected_before.json
```

아직 materialize되지 않은 두 source를 최초 취득할 때만 `--allow-download`를 추가한다.
manifest에 고정된 공식 URL만 읽는다. 원격 wrapper에서 정확히 한 article을 추출하고
preflight의 Windows `ElementTree.write` UTF-8/declaration/CRLF 직렬화를 명시적으로
재현한다. 최초 LF 직렬화는 고정 해시와 달라 차단됐으며, CRLF 재현 후 두 article과
transport 해시 및 paragraph signature가 모두 고정값과 일치했다. 해시 검사를 완화하거나
다른 source를 선택하지 않았다. 새 parser/adapter를 추가한 것이 아니다.

기존 출력/보고서 디렉터리가 있으면 즉시 거부한다. 재현 실행은 원본 freeze를
덮어쓰지 않고 `--output data/literature/v2/<new-version>` 및
`--reports reports/diagnostics/<new-version>`을 사용한다.

## 구조와 검증

기존 paper/chunk schema를 유지하고 source path/type, section index 및 explicit
source paragraph IDs를 추가한다. paragraph ID는 paper ID + section ordinal +
1-based paragraph index이다. 기존 chunk ID, text, paragraph start/end는 그대로다.
반복된 section 이름은 section index로 구분한다. paragraph artifact는 parser 출력
전체를 보존하고, 기존 section 제외 정책에 해당하면 `included_in_chunks=false`로 표시한다.
선정 manifest에 구조화된 study type/population이 없으므로 `not_reported`로 남긴다.

검사에는 source/selection/parser pins, source identity, frozen section/paragraph/text hash
signature의 완전 일치, supported body block multiplicity, 예상 밖 wrapper의 ordinary
paragraph 손실, paper/paragraph/chunk membership, metadata, section path, ID/hash,
explicit span ownership 및 포함 section의 순서 있는 전체 token 보존이 포함된다.
동일한 단어 반복을 overlap으로 오인하지 않도록 원문과 가능한 overlap 경로를 비교한다.
각 paper를 두 번 독립적으로 parse/build하고 paper set, paragraph, chunk ID/text/hash 및
전체 content hash를 비교한다. 시간은 manifest에만 기록해 corpus 내용에서 제외한다.

## 실제 결과 (2026-10-08, Asia/Seoul)

| 항목 | 결과 |
|---|---:|
| selected / built | 26 / 26 |
| missing / unexpected papers | 0 / 0 |
| unique PMID / PMCID | 26 / 26 |
| section 출력 단위 | 574 |
| normalized paragraphs / chunk 포함 paragraphs | 1,047 / 995 |
| chunks / paper 평균 | 683 / 26.2692 |
| PARSE_OK / WARNING / FAILED | 15 / 11 / 0 |
| empty chunks / duplicate chunk IDs / orphan chunks | 0 / 0 / 0 |
| critical provenance 누락 / PMID·PMCID 누락 | 0 / 0 |
| exact duplicate paragraph 중복분 | 20 |
| exact duplicate chunk text 중복분 | 11 |
| determinism | PASS |
| 보호된 기존 파일 변경 | 0 / 880 |

section은 제목 문자열의 종류 수가 아니라 paper/section index별 parser 출력 단위다.
논문 내 같은 경로가 여러 번 flush되면 별개 단위로 계산한다.

chunk 본문의 반복은 `Not applicable.` 9개와 전자 보충자료 링크 안내문 4개로,
각 그룹 첫 항목을 제외한 중복분이 8+3=11이다. 각 source/section과 ID는 별개이며
그대로 보존했다. 과학 결과의 중복이라고 계산하지 않는다. 현재 제외 정책의 문자열
매칭에서 남는 행정/안내문이며, retrieval의 잡음이 될 가능성은 있으나 이 단계에서
정책을 바꾸거나 성능 영향도를 주장하지 않는다.

두 신규 MAIN paper의 11개 요청 항목은 Introduction/Abstract에만 의존하지 않고
Participants/Methods/Results의 paragraph와 관련 chunk에서 확인했다.
PMID 42784046의 훈련 경험·8주·FAIL/1–3 RIR·초음파 thickness·Results와,
PMID 36228016의 RTEV/RTUV 조건 차이·weekly TTV matching·1RM·MRI/CSA·Results가
보존된다. 이 판정은 출처 보존 검사이며 과학적 결론 재판정이나 새 Gold가 아니다.

## 산출물과 한계

신규 합성 회귀 27개를 포함한 관련 테스트 144개 통과. 연구 자료와 `.env`가 없는 별도
공개 checkout에서 Public CI-safe pytest는 498개 통과/로컬 artifact 의존 81개 선택 해제다.
원래 작업 폴더의 전체 pytest는 사용자 미추적 audit 테스트도 포함해 598개 통과했다.
Python compile 검사도 통과했다. Windows sandbox에서는 asyncio 내부 loopback socketpair
생성에서 정지해 stack trace로 환경 문제를 확인했다. 재실행은 비밀 환경값 제거 및 실제
외부 연결 차단을 유지하면서 내부 loopback만 허용했으며 marker/assertion을 변경하지 않았다.
기존 Starlette/anyio deprecation warning 1개가 있다. 실제 DB/OpenAI/REST 서버 호출은
없으며, 실제 네트워크 사용은 고정 source 취득과 Git/PR/CI 확인으로 구분한다.

- Build: `data/literature/v2/build_freeze_v1/`
- Manifest: `data/literature/v2/build_freeze_v1/build_manifest.json`
- Content SHA256: `ecdbef3098c28b72c20b9c4962b728b2294f6116b6fd4e808099efb424102e2f`
- 검증 보고서: `reports/diagnostics/literature_corpus_v2_build_validation/`
- 완료/회귀/중복문 검토 기록: `reports/diagnostics/literature_corpus_v2_build_validation_completion/`

`papers.jsonl`, `paragraphs.jsonl`, `chunks.jsonl`, build manifest 및 freeze integrity는
별도 v2 경로에 보존한다. 로컬 연구 data/reports는 기존 Git 제외 정책을 유지하며 PR에
강제로 추가하지 않는다. manifest는 선택과 build를 구분하고 source hashes,
parser/chunker/config/code references, 출력 파일 hashes, deterministic content hash,
환경 및 Asia/Seoul build timestamp를 기록한다.

known warning은 mixed abstract의 direct p 우선 처리와 일부 body fn-group footnote
미추출이다. 표/그림/media/외부 보충자료는 계속 제외하고 DTD validation·malformed XML
복구·모든 XML dialect 지원을 주장하지 않는다. 표에만 있는 세부 수치와 모든 질문
family의 의미적 근거 완전성을 새로 인증한 것이 아니다. 이전 short section/special
block/namespace 손실은 이미 별도 수정된 역사적 항목이며 현재 회귀로 재해석하지 않는다.

향후 직접 확인할 동작은 freeze integrity 및 JSONL hash 재검사, 특정 paragraph ID에서
chunk ID까지의 추적이다. 별도 검증용 평가 설계로 넘어갈 준비는 됐지만 이번 작업에서는
그 단계나 Retrieval v2를 시작하지 않는다. PR은 사람이 확인하고 merge한다.
