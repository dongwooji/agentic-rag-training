# Held-out evaluation / semantic Gold v2

이 단계는 Retrieval 변경 전에 질문·입력 조건·정답 기준을 함께 고정한다.
기존 평가와 Gold는 개발·진단 기록으로 보존한다. 실제 질문·Gold·보고서는
ignored 연구 산출물이며 공개 CI에는 가상 문헌과 가상 질문만 사용한다.

## 출처와 작성 순서

기준은 `data/literature/v2/build_freeze_v1/`과 selection freeze의
`supported_scope.json`이다. build manifest, 산출물, 원문 파일의 SHA256과
Corpus content hash를 확인한 후 다음 순서로 작성한다.

1. frozen family 범위에 맞는 새 질문과 입력 조건을 작성한다.
2. pinned JATS 원문의 해당 문단을 직접 검토한다.
3. 의미 요구·필수 여부·주장·제한·원문 문자 구간을 기록한다.
4. 이 상태의 파일 hash를 저장한다. 아직 paragraph/chunk ID를 넣지 않는다.
5. 원문 구간을 frozen paragraph와 parent chunk에 연결한다.
6. 출처·중복·누출·기존 파일 보존을 검사하고 새 version에 freeze한다.

작성 선언과 hash는 절차를 기록한다. 작성자의 모든 과거 정보 노출을
증명하는 접근 로그나 독립 검토자의 승인 기록으로 해석하지 않는다.
질문은 검색 결과를 보기 전에 작성하며 검색 결과에 맞춰 수정하지 않는다.
원문 검토로 발견한 질문의 사실 오류는 mapping 전에 이유를 기록해 수정한다.

## Schema와 평가 경계

기존 case의 `id`, `question`, `category`를 유지하고 family, scope,
task type, expected behavior, log/literature 요구, 검색 지표 대상 여부를 추가한다.
Gold는 별도 파일이다. LIMITED를 포함한 모든 case에는 지원 결론, 필수 제한,
금지 일반화, 미지원 확장, 기대 행동을 기록한다.

Hybrid에는 고정된 가상 로그와 계산/관찰 정답을 별도로 기록한다.
e1RM은 기존 Epley 정의와 session-best 집계, 주간 volume은 월요일 기준
eligible 세트·반복수·중량×반복수 합계를 사용한다. 서로 다른 운동 장비의
표기 kg를 호환 단위로 취급하지 않는다. 일반 문헌으로 개인 원인을 확정하지 않는다.
가상 로그는 DB에 적재하거나 runtime에 새 기능으로 연결하지 않는다.

각 evidence group은 단일 pinned source span을 지원 근거로 가진다.
PMID/PMCID, source path/hash, section/index, paragraph index/hash,
정확한 문자 시작/끝과 span을 기록한다. `required=false`는 보조 근거다.
여러 출처가 동시에 필요한 요구는 별도 필수 group으로 구분한다.
family의 OPTIONAL은 제품 지원 우선순위이고, group의 `required`는 해당
평가 질문을 충분히 답하기 위한 근거 여부다. 두 의미를 혼동하지 않는다.

`parent_mapping`은 현 build의 평가 표현이다. 전체 span을 포함하는 parent들이
있으면 `match=any`, 하나로 완전히 덮지 못하면 필요한 parent들의
연속 구간 합집합을 `match=all`로 기록한다. 문단 ID가 연결돼 있다는
사실만으로 잘린 chunk를 정답으로 인정하지 않는다. 차후 child representation은
별도 mapping version으로 추가하고 의미 Gold와 원문 구간은 유지한다.
현재 parent가 Dense 최대 입력 길이에 맞는다는 가정은 하지 않는다.

기존 `src/evaluation/retrieval_metrics.py`는 변경하지 않는다.
새 evaluation-only adapter로 EvidenceGroupRecall@K와 Complete@K의
필수 any/all group 의미를 재사용한다. MRR은 **첫 필수 chunk hit**이고
모든 근거 확보 순위가 아니다. 필수 문헌 근거가 없는 경계 case는
검색 지표에서 제외하고 답변 보류/부분 답변 기준으로 따로 평가한다.
이번 단계에서는 가상 순위 fixture로 호환성만 검사하며 실제 검색 점수는 계산하지 않는다.

## 재검증과 freeze

```powershell
python scripts/freeze_heldout_evaluation_v2.py --draft-dir <authored-drafts> --output data/evaluation/<new-version> --reports reports/diagnostics/<new-version> --validate-only
```

최종 발행은 `--validate-only`를 빼고 실행한다. draft directory에는
`evaluation_cases_authored.json`, `gold_semantic_authored.json`,
`authoring_questions.json`, `semantic_stage.json`, `protected_before.json`,
`review_audits.json`이 필요하다. `review_audits.json`의 누출 검사에는
검색 결과 미사용, 이전 Gold 미복사, 질문 중복의 검토 근거를 기록한다.
기계 검증이 의미적 정확성의 전문가 판정을 대체하지 않는다.

산출물은 `evaluation_cases.json`, `gold_evidence.json`, `manifest.json`,
`freeze_integrity.json`, `authoring_record.json`과 mapping 전 byte-identical
질문/Gold snapshot이다. 별도 보고서는 sampling, case review, provenance,
leakage, integrity를 기록한다. manifest와 receipt는 서로의 hash를 순환 참조하지 않는다.
기존 output/report directory가 존재하면 덮어쓰지 않고 실패한다.

무결성 재검증은 evaluation-only `verify_references(root, receipt)`와
`verify_references(root, manifest)`로 파일 hash를 확인하고,
`validate_dataset(...)`으로 schema와 span coverage를 확인한다.
이전 frozen 파일을 수정하는 대신 새 version을 만들어야 한다.

## Runtime 분리와 남은 한계

Router·Retriever·Literature Tool·Grader·Recovery·답변 생성은 이 module이나
Gold path를 참조하지 않는다. 모든 runtime source/config의 정적 검사와
기존 API import-closure 테스트로 이를 확인한다. 이는 임의 악성 파일 읽기를
막는 OS 접근 제어를 뜻하지 않는다. 향후 평가 runner가 실행 계층에 넘길
입력은 질문과 실제 입력 값뿐이며 family/scope/Gold는 평가 계층에 남긴다.

같은 corpus와 주제군 일부를 사용하는 질문 hold-out이며 paper-held-out
평가가 아니다. 외부 전문가의 blind review도 아니다. 사람 검토 여부와
agent 검토를 명확히 구분한다. 검색 결과를 본 뒤 바꾼 질문/Gold는 새 version과
개발·진단용 지위를 기록하고 독립 검증 성능으로 부르지 않는다.
