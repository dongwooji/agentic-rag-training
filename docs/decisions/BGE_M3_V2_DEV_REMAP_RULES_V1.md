# corpus v2 dev Gold remap 규칙 v1

결정일: 2026-10-14 (Asia/Seoul). 사용자 승인 범위는 검색 전 준비다. 이 규칙을 원문 매핑 검토표 작성 전에 SHA-256으로 고정한다. 규칙 수정이 필요하면 새 version을 작성한다. 검토표와 remapped Gold는 사용자 검토 전 동결하지 않는다.

## 보존과 적용 범위

- 기존 dev/dev-ko 질문, 기존 Gold, claim, required, group ID, 원래 match 의미를 유지한다. dev 18문항과 대응 dev-ko 18문항은 36입력이며 18개 의미 질문이다.
- 동결 corpus v1/v2, 기존 MiniLM 벡터, 기존 평가/보고서, 운영 서비스, prompt, Router, retrieval 기본 설정을 수정하지 않는다.
- 새로운 매핑은 별도 version으로 저장한다. Gold·case label은 embedding 입력이나 검색 구성요소에 제공하지 않는다.
- 이번 단계에서는 corpus parent embedding과 dev 질문 CPU embedding 시간만 측정한다. Dense/BM25/Hybrid 검색, 순위 생성, Grader, Recovery, LLM, REST, DB 적재/조회는 실행하지 않는다.

## 원문 근거와 후보 생성

1. v1 Gold가 참조한 chunk 원문·논문 ID(PMCID/PMID/DOI)·section·text hash를 출발점으로 한다.
2. v2 후보는 동일 논문 안에서 원문·section·문단 출처·문장 대조로 만든다. ID가 바뀌었다는 이유만으로 의미 동등성을 가정하지 않는다.
3. 같은 ID는 원문 문자열과 계산한 SHA-256을 모두 비교한다. 같은 ID라도 본문이 다르면 변경 후보로 별도 표시한다.
4. 완전 동일 원문, 원문 문장 포함, whitespace만 다른 동일 span은 객관적 대조로 구분한다. 논문 주제나 단어가 비슷하다는 이유만으로 승인하지 않는다.
5. 같은 논문에서 문장이 재구성돼 동일 claim인지 해석이 필요한 경우 claim_equivalent_pending_review로 표시하고 양쪽 원문·해당 claim·인구집단/개입/결과/제약을 함께 보여준다. 사용자 승인 전 확정하지 않는다.
6. 다른 논문의 유사 주장으로 대체하지 않는다. 논문이 없거나 원문 근거가 보존되지 않으면 부재를 기록하며 corpus에 논문을 추가하지 않는다.
7. 검토 후보는 문자/출처 대조 결과다. 검색 query·embedding similarity·BM25·검색 순위를 사용하지 않는다.

## 분할 시 any/all 의미

v1 chunk 하나가 v2 여러 chunk와 연결된다고 해서 자동으로 any 또는 all로 바꾸지 않는다. 원래 Gold claim을 직접 지지하는 데 필요한 범위를 기준으로 판단한다.

- 각각의 v2 chunk가 필요한 claim 전체와 제약을 독립적으로 지지하면 대안 관계다: any.
- claim의 필수 구성 요소가 여러 v2 chunk에 나뉘어 어느 하나로는 불완전하고 함께 있어야 지지하면 결합 관계다: all.
- 원래 chunk의 배경·무관한 문장을 담은 분할 chunk까지 all에 넣지 않는다. 반대로 제약/대상/결과가 분산된 근거를 any로 약화하지 않는다.
- 판단이 불명확하면 ambiguous_pending_review다. 검색 결과를 보고 유리한 쪽을 선택하지 않는다.

원래 group의 any/all과 분할 chunk의 관계는 별도 계층으로 유지한다. 각 v1 chunk의 매핑 M(old)을 대안 bundle의 OR로 표현하며, 각 bundle 안은 필수 v2 chunk의 AND다.

```text
M(old_A) = OR(AND(v2_A1, v2_A2), AND(v2_A3))
원래 group any(old_A, old_B) = M(old_A) OR M(old_B)
원래 group all(old_A, old_B) = M(old_A) AND M(old_B)
```

예를 들어 old_A의 근거가 A1/A2로 분할되고 old_B는 B1로 그대로 보존되었다면, 원래 any는 (A1 AND A2) OR B1, 원래 all은 (A1 AND A2) AND B1이다. 하나의 평평한 chunk_ids+match로 표현할 수 없으면 새 remap schema에 이 계층을 보존한다. 기존 평가기나 Gold를 이번 준비 작업에서 변경하지 않는다. 최종 평가 실행 전 새 표현을 검증하는 별도 계약이 필요하다.

## 부재·미확정 상태

| 상태 | 의미 | v2 IDs/bundles |
|---|---|---|
| unchanged_verified | 동일 ID·원문·hash 일치 | 검증한 기존 ID |
| exact_text_pending_review | 다른 ID지만 동일 원문 | 후보, 승인 전 미확정 |
| same_span_pending_review | 문장/span이 동일하며 필요한 범위 포함 | 후보, 승인 전 미확정 |
| claim_equivalent_pending_review | 동일 claim인지 사람 확인 필요 | 후보, 승인 전 미확정 |
| source_absent | 동일 논문이 v2에 없음 | 빈 목록 |
| evidence_absent | 논문은 있으나 필수 근거가 없음 | 빈 목록 |
| ambiguous_pending_review | 여러 후보·분할 의미가 불명확 | 후보만 기록 |

부재를 다른 chunk로 채우거나 required를 false로 바꾸지 않는다. 검색 가능한 근거가 없다는 이유로 문항·group을 분모에서 조용히 제외하지 않는다. 최종 매핑은 사용자 검토 후 freeze하며, 부재 상태도 그 version의 명시적 결과로 보존한다.

매핑 확정 후 원래 any group에 승인된 대안이 하나라도 있으면 그 대안으로 평가할 수 있다. 원래 all group의 필수 매핑이 하나라도 부재면 전체 지지는 불가능하다. 완전히 부재인 required group은 충족되지 않은 것으로 계산한다. 원래 required/group match를 유지한 dev 36입력 전체를 채택 기준의 분모로 사용하며, 근거 부재 수·달성 가능한 CE 상한을 별도 보고한다. coverage 부족과 검색 실패를 원인 보고에서 구분한다. 매핑 미확정 상태가 남아 있으면 평가를 실행하지 않는다.

## 검토와 동결 순서

1. 이 규칙 문서와 hash 고정.
2. 바뀐 ID 14개의 v1/v2 원문을 나란히 보여주는 검토표 작성. 같은 ID 10개는 원문·hash 검증.
3. 사용자에게 문장/claim 대조 근거, 부재, any/all 후보를 제시. 검토 전 검색하지 않는다.
4. 사용자 검토 후에만 새 remapped Gold version과 승인 기록/hash를 freeze.
5. 그 후 별도 승인된 비교에서 검색을 실행한다. 결과를 보고 규칙·라벨·Gold를 수정하지 않는다.

## 채택 기준과 진단 범위

채택 기준: remapped dev 36입력 기준 CE@10 감소 없음 AND 문항별 EGR@10 개선 수 > 악화 수 AND 질문 embedding CPU 평균 1초 이하. 대응 질문을 독립적인 36개 의미 질문으로 표현하지 않는다.

기존 held-out v2의 근거 있는 20문항은 채택 결정 후 진단용으로 1회만 실행한다. 이번 준비에서는 held-out을 열거나 실행하지 않는다. 독립 일반화 성능 평가라고 표현하지 않는다. 운영 corpus/model 전환은 이번 범위 밖이다.

## CPU 질문 embedding 측정 사전 조건

- 모델은 tokenizer 측정에 사용한 BAAI/bge-m3 revision 5617a9f61b028005a4858fdac845db406aefb181.
- CPU, float32, normalize_embeddings=True, max_seq_length=8192, 질문 batch_size=1.
- dev 원 질문 18개 및 동결 dev-ko 질문 18개를 그대로 사용한다. 검색어 생성/번역이나 prompt를 붙이지 않는다. 실제 Final V2 영어 검색어의 latency와 구분한다.
- CPU intra-op thread 4, inter-op thread 1을 측정 전에 고정한다. 결과에 따라 thread·precision·batch·입력 길이를 바꾸고 재측정하지 않는다.
- 새 synthetic 문장 3개를 각각 1회 warm-up한 뒤 36입력을 고정 순서(dev 다음 dev-ko)로 각각 1회 측정한다. warm-up과 모델 다운로드/로드는 질문 시간에서 제외하고 별도 기록한다.
- perf_counter로 호출 전후 tokenizer·forward·pooling·정규화·numpy 반환을 포함해 측정한다. 질문마다 평균/개별 시간과 p95를 기록한다. p95는 (n-1)*0.95 위치 선형 보간이다.
- process RSS peak와 Windows private commit peak, 시스템 가용 RAM을 기록한다. warm-up 전 첫 encode 시간도 별도 기록한다. 가용 메모리 부족·다른 프로세스 영향은 실행 환경의 제한으로 명시한다. 기존 프로세스를 종료하지 않는다.
- 실제 검색 CE/EGR은 이번 단계에서 측정하지 않는다. CPU 시간 기준 통과 여부만 따로 보고하며 모델 채택으로 표현하지 않는다.
