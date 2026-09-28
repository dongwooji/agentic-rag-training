from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CATEGORY_LABELS = {
    "literature_only": "Literature-only",
    "log_metric": "Log / Metric",
    "hybrid": "Hybrid",
    "unanswerable": "Unanswerable",
}


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_review(dataset_path: Path, chunks_path: Path) -> str:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    review_path = dataset_path.parent / "human_review_v1.json"
    review = (
        json.loads(review_path.read_text(encoding="utf-8"))
        if review_path.exists()
        else {}
    )
    review_by_case = {item["id"]: item for item in review.get("cases", [])}
    human_approved = (
        review.get("status") == "approved"
        and review.get("dataset_sha256") == dataset_hash
        and len(review_by_case) == len(dataset["cases"])
    )
    chunks = {
        row["chunk_id"]: row
        for row in (
            json.loads(line)
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    lines = [
        "# Phase 5 — Evaluation set v1 human review",
        "",
        (
            "> Status: **human review approved; eval_dataset_v1 frozen**."
            if human_approved
            else "> Status: **draft; human review pending**. This file is a review surface, not a frozen benchmark."
        ),
        "",
        "## Review rules",
        "",
        "For every case, confirm that the question is natural, the gold evidence really supports the answer criteria, and the limitations prevent overclaiming. No retrieval baseline or model answer was used to create these labels.",
        "",
        "A case should be rejected if its wording leaks an answer, requires unavailable data without being marked unanswerable, or lists a chunk that is merely topically related rather than relevant.",
        "",
        "Evidence semantics: chunks inside a `match=any` group are alternatives; a `match=all` group requires every listed chunk. Every group marked `required=true` must be covered. Optional groups provide context but do not affect primary retrieval metrics.",
        "",
        "## Distribution",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for category, label in CATEGORY_LABELS.items():
        count = sum(case["category"] == category for case in dataset["cases"])
        lines.append(f"| {label} | {count} |")
    lines.extend(["| **Total** | **30** |", ""])

    for case in dataset["cases"]:
        case_review = review_by_case.get(case["id"], {})
        case_approved = (
            human_approved
            and case_review.get("status") == "approved"
            and case_review.get("question_and_category_correct") is True
            and case_review.get("evidence_correct") is True
            and case_review.get("limitations_sufficient") is True
        )
        lines.extend(
            [
                f"## {case['id']} — {CATEGORY_LABELS[case['category']]}",
                "",
                f"**Question:** {case['question']}",
                "",
                f"**Required tools:** {', '.join(case['required_tools']) or 'none'}",
                "",
                "**Answer criteria:**",
                "",
            ]
        )
        for criterion in case["gold"]["answer_criteria"]:
            lines.append(f"- `{criterion['id']}` {criterion['text']}")
            mappings = [
                *(f"literature:{value}" for value in criterion["literature_evidence_group_ids"]),
                *(f"structured:{value}" for value in criterion["structured_evidence_ids"]),
                *(f"contract:{value}" for value in criterion["supporting_contracts"]),
            ]
            lines.append(f"  - Evidence mapping: {', '.join(mappings)}")
        limitations = case["gold"].get("limitations", [])
        if limitations:
            lines.extend(["", "**Required limitations:**", ""])
            for item in limitations:
                lines.append(f"- `{item['id']}` {item['text']}")
                mappings = [
                    *(f"literature:{value}" for value in item["literature_evidence_group_ids"]),
                    *(f"structured:{value}" for value in item["structured_evidence_ids"]),
                    *(f"contract:{value}" for value in item["supporting_contracts"]),
                ]
                lines.append(f"  - Evidence mapping: {', '.join(mappings)}")

        structured = case["gold"].get("structured_evidence", [])
        if structured:
            lines.extend(["", "**Structured gold:**", ""])
            for evidence in structured:
                lines.append(
                    f"- `{evidence['id']}` — `{evidence['operation']}` with `{_compact_json(evidence['parameters'])}`"
                )
                lines.append(f"  - Expected: `{_compact_json(evidence['expected'])}`")

        literature_groups = case["gold"].get("literature_evidence_groups", [])
        if literature_groups:
            lines.extend(["", "**Claim-level literature evidence groups:**", ""])
            for group in literature_groups:
                lines.append(
                    f"- `{group['id']}` — required=`{str(group['required']).lower()}`, "
                    f"match=`{group['match']}`"
                )
                lines.append(f"  - Claim: {group['claim']}")
                for chunk_id in group["chunk_ids"]:
                    chunk = chunks[chunk_id]
                    preview = " ".join(chunk["text"].split())[:360]
                    lines.append(
                        f"  - `{chunk_id}` — {chunk['pmcid']}, {chunk['section']}"
                    )
                    lines.append(f"    - Preview: {preview}…")

        missing = case["gold"].get("missing_fields", [])
        if missing:
            lines.extend(
                [
                    "",
                    "**Missing fields that require abstention:** " + ", ".join(missing),
                ]
            )

        lines.extend(
            [
                "",
                "Review checklist:",
                "",
                f"- [{'x' if case_approved else ' '}] Question and category are appropriate",
                f"- [{'x' if case_approved else ' '}] Gold evidence and numeric labels are correct",
                f"- [{'x' if case_approved else ' '}] Answer criteria and limitations are sufficient",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_review(dataset_path: Path, chunks_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_review(dataset_path, chunks_path), encoding="utf-8"
    )
