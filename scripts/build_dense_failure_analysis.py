"""Create the Phase 6 close-out analysis without rerunning retrieval."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = PROJECT_ROOT / "reports/baselines/dense_baseline_v1"
OUTPUT_PATH = BASELINE_DIR / "FAILURE_ANALYSIS.md"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def group_covered(group: dict[str, Any], retrieved: list[str]) -> bool:
    found = set(retrieved)
    gold = set(group["chunk_ids"])
    if group["match"] == "any":
        return bool(found & gold)
    if group["match"] == "all":
        return gold.issubset(found)
    raise ValueError(f"Unknown match mode: {group['match']}")


def paper_label(paper_id: str) -> str:
    return paper_id.removeprefix("paper_").upper()


def compact_ranked(items: list[dict[str, Any]], limit: int = 5) -> str:
    if not items:
        return "없음"
    shown = [f"r{item['rank']} `{item['chunk_id']}`" for item in items[:limit]]
    if len(items) > limit:
        shown.append(f"외 {len(items) - limit}개")
    return "; ".join(shown)


def verify_frozen_artifacts() -> dict[str, Any]:
    manifest_path = BASELINE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "frozen":
        raise RuntimeError("dense_baseline_v1 manifest is not frozen")
    for artifact in manifest["artifacts"]:
        path = BASELINE_DIR / artifact["path"]
        actual = sha256_file(path)
        if actual != artifact["sha256"]:
            raise RuntimeError(f"Frozen artifact hash mismatch: {path.name}")
    return manifest


def analyze_cases() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    verify_frozen_artifacts()
    dataset = json.loads(
        (PROJECT_ROOT / "data/evaluation/eval_dataset_v1.json").read_text(
            encoding="utf-8"
        )
    )
    chunks = read_jsonl(PROJECT_ROOT / "data/literature/processed/chunks.jsonl")
    results = read_jsonl(BASELINE_DIR / "retrieval_results.jsonl")
    metrics = json.loads((BASELINE_DIR / "metrics.json").read_text(encoding="utf-8"))
    reproducibility = json.loads(
        (BASELINE_DIR / "reproducibility.json").read_text(encoding="utf-8")
    )

    if sha256_file(PROJECT_ROOT / reproducibility["inputs"]["eval_dataset_path"]) != reproducibility["inputs"]["eval_dataset_sha256"]:
        raise RuntimeError("Frozen eval dataset hash differs from baseline provenance")
    if sha256_file(PROJECT_ROOT / reproducibility["inputs"]["corpus_chunks_path"]) != reproducibility["inputs"]["corpus_chunks_sha256"]:
        raise RuntimeError("Frozen corpus hash differs from baseline provenance")

    cases = {
        case["id"]: case
        for case in dataset["cases"]
        if case["category"] in {"literature_only", "hybrid"}
    }
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    if len(cases) != 18 or len(results) != 18:
        raise RuntimeError("Expected exactly 18 literature-bearing cases/results")

    analyses: list[dict[str, Any]] = []
    for stored in results:
        case_id = stored["case_id"]
        case = cases[case_id]
        groups = [
            group
            for group in case["gold"]["literature_evidence_groups"]
            if group["required"]
        ]
        ranked = [item["chunk_id"] for item in stored["retrieved"]]
        rank_by_chunk = {
            item["chunk_id"]: int(item["rank"]) for item in stored["retrieved"]
        }
        all_gold = list(
            dict.fromkeys(
                chunk_id for group in groups for chunk_id in group["chunk_ids"]
            )
        )
        covered5 = [group_covered(group, ranked[:5]) for group in groups]
        covered10 = [group_covered(group, ranked[:10]) for group in groups]
        complete5 = all(covered5)
        complete10 = all(covered10)
        first_gold_rank = min(
            (rank_by_chunk[chunk_id] for chunk_id in all_gold if chunk_id in rank_by_chunk),
            default=None,
        )
        stored_metrics = stored["metrics"]
        if complete5 != bool(stored_metrics["complete_evidence@5"]):
            raise RuntimeError(f"Stored @5 metric mismatch for {case_id}")
        if complete10 != bool(stored_metrics["complete_evidence@10"]):
            raise RuntimeError(f"Stored @10 metric mismatch for {case_id}")
        expected_rr = 0.0 if first_gold_rank is None else 1.0 / first_gold_rank
        if abs(expected_rr - float(stored_metrics["reciprocal_rank"])) > 1e-12:
            raise RuntimeError(f"Stored reciprocal rank mismatch for {case_id}")

        uncovered10 = [
            group for group, covered in zip(groups, covered10, strict=True) if not covered
        ]
        late_groups = [
            group["id"]
            for group, at5, at10 in zip(groups, covered5, covered10, strict=True)
            if not at5 and at10
        ]
        same_paper: list[dict[str, Any]] = []
        related_topic: list[dict[str, Any]] = []
        for group in uncovered10:
            group_chunks = [chunks_by_id[chunk_id] for chunk_id in group["chunk_ids"]]
            gold_papers = {chunk["paper_id"] for chunk in group_chunks}
            gold_topics = {
                topic for chunk in group_chunks for topic in chunk.get("topics", [])
            }
            for hit in stored["retrieved"]:
                chunk = chunks_by_id[hit["chunk_id"]]
                if hit["chunk_id"] in all_gold:
                    continue
                detail = {
                    "rank": hit["rank"],
                    "chunk_id": hit["chunk_id"],
                    "evidence_group_id": group["id"],
                }
                if chunk["paper_id"] in gold_papers:
                    same_paper.append(detail)
                elif set(chunk.get("topics", [])) & gold_topics:
                    related_topic.append(
                        {
                            **detail,
                            "topic_overlap": sorted(
                                set(chunk.get("topics", [])) & gold_topics
                            ),
                        }
                    )

        def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            unique: dict[tuple[Any, ...], dict[str, Any]] = {}
            for item in items:
                key = (item["rank"], item["chunk_id"], item["evidence_group_id"])
                unique[key] = item
            return sorted(unique.values(), key=lambda item: (item["rank"], item["chunk_id"]))

        same_paper = deduplicate(same_paper)
        related_topic = deduplicate(related_topic)
        labels: list[str] = []
        if complete5:
            labels.append("SUCCESS_TOP5")
        if late_groups:
            labels.append("GOLD_RANK_6_10")
        if same_paper:
            labels.append("SAME_PAPER_WRONG_CHUNK")
        if related_topic:
            labels.append("RELATED_TOPIC_WRONG_EVIDENCE")
        if first_gold_rank is None:
            labels.append("GOLD_NOT_IN_TOP10")
        multi_required = len(groups) > 1 or any(
            group["match"] == "all" and len(group["chunk_ids"]) > 1
            for group in groups
        )
        if not complete10 and multi_required:
            labels.append("MULTI_EVIDENCE_INCOMPLETE")
        if not labels:
            raise RuntimeError(f"No diagnostic label assigned to {case_id}")

        analyses.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "question": stored["question"],
                "groups": groups,
                "retrieved": stored["retrieved"],
                "rank_by_chunk": rank_by_chunk,
                "all_gold": all_gold,
                "covered5": sum(covered5),
                "covered10": sum(covered10),
                "group_count": len(groups),
                "complete5": complete5,
                "complete10": complete10,
                "first_gold_rank": first_gold_rank,
                "late_groups": late_groups,
                "same_paper": same_paper,
                "related_topic": related_topic,
                "labels": labels,
            }
        )
    return analyses, {
        "metrics": metrics,
        "reproducibility": reproducibility,
        "dataset": dataset,
    }


def technical_term_rows(analyses: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    patterns = (
        ("APRE", re.compile(r"(?<![A-Za-z0-9])APRE(?![A-Za-z0-9])", re.IGNORECASE)),
        ("VBT", re.compile(r"(?<![A-Za-z0-9])VBT(?![A-Za-z0-9])", re.IGNORECASE)),
        ("RPE", re.compile(r"(?<![A-Za-z0-9])RPE(?![A-Za-z0-9])", re.IGNORECASE)),
        (
            "momentary muscular failure",
            re.compile(r"momentary muscular failure", re.IGNORECASE),
        ),
        (
            "periodization / periodized",
            re.compile(r"(?<![A-Za-z0-9])periodi[sz](?:ation|ed)(?![A-Za-z0-9])", re.IGNORECASE),
        ),
        (
            "progressive overload",
            re.compile(r"(?<![A-Za-z0-9])progressive overload(?![A-Za-z0-9])", re.IGNORECASE),
        ),
    )
    rows: list[tuple[str, str, str]] = []
    for label, pattern in patterns:
        matched = [item for item in analyses if pattern.search(item["question"])]
        if not matched:
            rows.append((label, "없음", "frozen query에서 literal term 동작을 판정할 수 없음"))
            continue
        observations = []
        for item in matched:
            first = item["first_gold_rank"] if item["first_gold_rank"] else "—"
            observations.append(
                f"{item['case_id']}: Complete@5={'Y' if item['complete5'] else 'N'}, "
                f"Complete@10={'Y' if item['complete10'] else 'N'}, first Gold={first}"
            )
        rows.append((label, ", ".join(item["case_id"] for item in matched), "; ".join(observations)))
    return rows


def render_report(analyses: list[dict[str, Any]], context: dict[str, Any]) -> str:
    metrics = context["metrics"]
    reproducibility = context["reproducibility"]
    counts = Counter(label for item in analyses for label in item["labels"])
    by_id = {item["case_id"]: item for item in analyses}
    lines = [
        "# Phase 6 Dense Retrieval Failure Analysis",
        "",
        "> Close-out diagnostic only. Retrieval was not rerun, and no baseline setting, query, Gold label, corpus, evaluation dataset, score, or rank was changed.",
        "",
        "## Analysis scope and semantics",
        "",
        f"- Baseline: `{reproducibility['baseline_version']}` (`{reproducibility['status']}`)",
        f"- Frozen retrieval results SHA-256: `{next(item['sha256'] for item in verify_frozen_artifacts()['artifacts'] if item['path'] == 'retrieval_results.jsonl')}`",
        f"- Corpus: `{reproducibility['inputs']['corpus_version']}` / `{reproducibility['inputs']['corpus_chunks_sha256']}`",
        f"- Evaluation: `{reproducibility['inputs']['eval_dataset_version']}` / `{reproducibility['inputs']['eval_dataset_sha256']}`",
        "- `Required complete @K`는 frozen evidence-group contract에 따라 모든 `required=true` group이 Top-K에서 `any`/`all` semantics를 충족했음을 뜻한다.",
        "- 첫 Gold rank는 required group에 속한 Gold chunk 중 최초 순위이며, MRR 정의와 동일하다.",
        "- `SAME_PAPER_WRONG_CHUNK`와 `RELATED_TOPIC_WRONG_EVIDENCE`는 Top-10에서도 미충족인 group에 대해서만 부여했다. Topic overlap은 frozen chunk의 `topics` 메타데이터 교집합이다.",
        "- 실패 유형은 상호 배타적이지 않다. 이 파일은 frozen manifest의 네 핵심 결과물을 수정하지 않는 사후 분석 산출물이다.",
        "",
        "## Baseline snapshot",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| EvidenceGroupRecall@5 | {metrics['macro']['evidence_group_recall@5']:.6f} |",
        f"| EvidenceGroupRecall@10 | {metrics['macro']['evidence_group_recall@10']:.6f} |",
        f"| CompleteEvidence@5 | {metrics['macro']['complete_evidence@5']:.6f} ({sum(item['complete5'] for item in analyses)}/18 cases) |",
        f"| CompleteEvidence@10 | {metrics['macro']['complete_evidence@10']:.6f} ({sum(item['complete10'] for item in analyses)}/18 cases) |",
        f"| ChunkRecall@5 | {metrics['macro']['chunk_recall@5']:.6f} |",
        f"| ChunkRecall@10 | {metrics['macro']['chunk_recall@10']:.6f} |",
        f"| MRR | {metrics['macro']['mrr']:.6f} |",
        "",
        "## Failure-type distribution",
        "",
        "Counts overlap because one case may receive multiple diagnostic labels.",
        "",
        "| Type | Cases | Count |",
        "|---|---|---:|",
    ]
    label_order = (
        "SUCCESS_TOP5",
        "GOLD_RANK_6_10",
        "SAME_PAPER_WRONG_CHUNK",
        "RELATED_TOPIC_WRONG_EVIDENCE",
        "GOLD_NOT_IN_TOP10",
        "MULTI_EVIDENCE_INCOMPLETE",
    )
    for label in label_order:
        case_ids = [item["case_id"] for item in analyses if label in item["labels"]]
        lines.append(f"| `{label}` | {', '.join(case_ids) or '—'} | {counts[label]} |")

    lines.extend(
        [
            "",
            "## All 18 cases at a glance",
            "",
            "`Y (x/y)`는 required group 전체 충족 여부와 충족 group 수를 함께 표시한다.",
            "",
            "| Case | Required complete @5 | Required complete @10 | First Gold rank | Failure types |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for item in analyses:
        first = item["first_gold_rank"] if item["first_gold_rank"] is not None else "—"
        lines.append(
            f"| `{item['case_id']}` | {'Y' if item['complete5'] else 'N'} "
            f"({item['covered5']}/{item['group_count']}) | "
            f"{'Y' if item['complete10'] else 'N'} ({item['covered10']}/{item['group_count']}) | "
            f"{first} | {', '.join(f'`{label}`' for label in item['labels'])} |"
        )

    lines.extend(["", "## Representative successes", ""])
    success_notes = {
        "LIT-003": "첫 Gold가 rank 2이고 네 required group 모두 Top-5에서 충족됐다.",
        "LIT-006": "autoregulation 질문에서 첫 Gold가 rank 1, 네 required group이 Top-3에서 모두 충족됐다.",
        "HYB-008": "literal `periodization`이 포함된 hybrid query에서 첫 Gold 및 complete evidence가 rank 1에 도달했다.",
    }
    for case_id, note in success_notes.items():
        lines.append(f"- **{case_id}:** {note}")

    lines.extend(["", "## Representative failures", ""])
    failure_notes = {
        "LIT-001": "Gold는 Top-10에 없지만 Gold paper `PMC6081873`의 다른 chunk가 ranks 3, 7, 10에 있어 paper 선택보다 intra-paper chunk 선택 실패가 두드러진다.",
        "LIT-004": "첫 Gold가 rank 8이고 Top-10에서 2/3 group만 충족했다. 미충족 limitation Gold와 같은 `PMC9935748`의 다른 chunk가 다수 검색됐다.",
        "LIT-008": "첫 Gold는 rank 1이지만 5개 group 중 1개가 Top-10에서 누락됐다. 누락 Gold paper `PMC7593778`의 다른 chunk는 rank 6에 있다.",
        "HYB-006": "Top-10 전체가 Gold가 아니지만 ranks 1-9의 9개가 모두 Gold paper `PMC9935748`의 다른 chunk다. exact evidence selection 실패가 명확한 사례다.",
        "HYB-007": "literal `progressive overload`가 있어도 Gold는 Top-10에 없고 Gold paper도 검색되지 않았다. 관련 topic overlap 문헌만 상위에 나타났다.",
    }
    for case_id, note in failure_notes.items():
        lines.append(f"- **{case_id}:** {note}")

    lines.extend(
        [
            "",
            "## Exact technical-term diagnostic",
            "",
            "이 표는 frozen question에 literal term이 실제로 포함된 경우만 탐지한다. 결과만으로 sparse retrieval의 우위를 가정하지 않는다.",
            "",
            "| Term/family | Literal query cases | Stored-result observation |",
            "|---|---|---|",
        ]
    )
    for term, cases, observation in technical_term_rows(analyses):
        lines.append(f"| `{term}` | {cases} | {observation} |")
    lines.extend(
        [
            "",
            "Additional concept-level observations:",
            "",
            "- `APRE`, `VBT`, `RPE`는 frozen question에 literal acronym으로 존재하지 않는다. `LIT-006`은 broader `autoregulation` 표현을 사용하며 Complete@5에 성공했으므로 acronym 자체의 효과는 이 baseline만으로 판정할 수 없다.",
            "- `momentary muscular failure`도 frozen question의 literal phrase는 아니다. 다만 해당 Gold claim을 가진 `HYB-006`은 Gold paper의 다른 chunk를 ranks 1-9에 검색했지만 Gold chunk 자체는 놓쳤다.",
            "- Periodization family는 `LIT-009`가 Top-10에서만 complete, `HYB-008`은 rank 1에서 complete였다.",
            "- `progressive overload`는 `LIT-010`이 Top-10에서 complete했지만 `HYB-007`은 Gold가 Top-10에 없었다. 동일 term의 상반된 결과이므로 lexical presence만으로 성공을 설명할 수 없다.",
            "",
            "## Case details",
            "",
        ]
    )
    for item in analyses:
        first = item["first_gold_rank"] if item["first_gold_rank"] is not None else "없음"
        lines.extend(
            [
                f"### {item['case_id']}",
                "",
                f"- Query: {item['question']}",
                f"- Required Gold @5: {'COMPLETE' if item['complete5'] else 'INCOMPLETE'} ({item['covered5']}/{item['group_count']} groups)",
                f"- Required Gold @10: {'COMPLETE' if item['complete10'] else 'INCOMPLETE'} ({item['covered10']}/{item['group_count']} groups)",
                f"- First Gold rank: {first}",
                f"- Failure types: {', '.join(f'`{label}`' for label in item['labels'])}",
                f"- Same-paper wrong chunks for still-missed groups: {compact_ranked(item['same_paper'])}",
                f"- Related-topic, other-paper chunks for still-missed groups: {compact_ranked(item['related_topic'])}",
                "- Required Gold chunks:",
            ]
        )
        for group in item["groups"]:
            lines.append(
                f"  - `{group['id']}` (`{group['match']}`): {group['claim']}"
            )
            for chunk_id in group["chunk_ids"]:
                rank = item["rank_by_chunk"].get(chunk_id)
                lines.append(
                    f"    - `{chunk_id}` — Top-10 rank: {rank if rank is not None else '—'}"
                )
        lines.append("- Retrieved Top-10:")
        for hit in item["retrieved"]:
            lines.append(
                f"  {hit['rank']}. `{hit['chunk_id']}` — cosine score `{hit['score']:.6f}`"
            )
        lines.append("")

    lines.extend(
        [
            "## Hypotheses to test in Phase 7",
            "",
            "These are comparison hypotheses, not conclusions about BM25 or hybrid superiority.",
            "",
            "1. **Intra-paper chunk discrimination:** Cases such as LIT-001, LIT-004, LIT-008, HYB-004, and HYB-006 suggest testing whether another retrieval signal changes the ordering of chunks inside an already relevant paper.",
            "2. **Technical-term sensitivity:** Compare the two retrievers separately on periodization/progressive-overload cases and report per-case ranks; do not infer behavior for APRE/VBT/RPE because those acronyms are absent from the frozen questions.",
            "3. **Hybrid-query dilution:** Six of eight HYB cases have no Gold chunk in Top-10. Test whether dates, exercise names, and log-specific wording dominate literature retrieval, while keeping every frozen query unchanged.",
            "4. **Multi-evidence completeness:** Compare group-level coverage, especially limitations and `all` groups, rather than judging improvement from first-Gold MRR alone.",
            "5. **Depth effects:** CompleteEvidence rises from 4/18 at Top-5 to 8/18 at Top-10. Compare rank movement and fusion effects at the same fixed K values.",
            "6. **Representation-length diagnostic:** The frozen Dense encoder records `max_sequence_length=128`. Treat chunk length/truncation as a documented baseline characteristic and stratify failures if useful; do not alter the frozen Dense run.",
            "",
            "## Close-out decision",
            "",
            "Phase 6 is complete. `dense_baseline_v1` remains the immutable, untuned comparison point. Phase 7 has not been implemented or run as part of this analysis.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite close-out report: {OUTPUT_PATH}")
    analyses, context = analyze_cases()
    report = render_report(analyses, context)
    with OUTPUT_PATH.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(report)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"SHA-256: {sha256_file(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
