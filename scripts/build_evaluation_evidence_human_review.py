"""Build a chunk-only human-review packet for Phase 5 literature Gold evidence.

This script is deliberately read-only with respect to the evaluation dataset and
the frozen literature corpus. Reviewer annotations live under reports/ and are
never written back into Gold labels.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "evaluation" / "eval_dataset_v1_draft.json"
HUMAN_REVIEW_PATH = ROOT / "data" / "evaluation" / "human_review_v1.json"
FROZEN_DATASET_PATH = ROOT / "data" / "evaluation" / "eval_dataset_v1.json"
FREEZE_MANIFEST_PATH = ROOT / "data" / "evaluation" / "eval_dataset_v1.manifest.json"
CHUNKS_PATH = ROOT / "data" / "literature" / "processed" / "chunks.jsonl"
BASE_ANNOTATIONS_PATH = ROOT / "reports" / "evaluation_evidence_human_review_annotations.json"
OVERRIDES_PATH = ROOT / "reports" / "evaluation_evidence_human_review_v2_overrides.json"
OUTPUT_PATH = ROOT / "reports" / "EVALUATION_EVIDENCE_HUMAN_REVIEW_V2.md"

EXPECTED_DATASET_SHA256 = "89f35c8bdadbb543aa89eae00fcd8694d84607e34512dd51095bb830ec79bf5d"
EXPECTED_CHUNKS_SHA256 = "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
EXPECTED_CORPUS_VERSION = "literature_corpus_v1"
EXPECTED_BASE_ANNOTATIONS_SHA256 = "5a5120fd35c895714a8575bee8099b59b382bcb1e0a49aca7ca6cb3d72cf9c37"

CASE_IDS = [*(f"LIT-{index:03d}" for index in range(1, 11)), *(f"HYB-{index:03d}" for index in range(1, 9))]
DECISIONS = {"APPROVE", "MODIFY", "REJECT"}
RATINGS = {"SUPPORTED", "PARTIAL", "NOT SUPPORTED"}
ISSUES = {
    "none",
    "overly broad claim",
    "weak evidence",
    "redundant group",
    "unnecessary match=all",
    "missing limitation",
    "other",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_chunks(path: Path) -> dict[str, dict]:
    chunks: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            chunk_id = chunk["chunk_id"]
            if chunk_id in chunks:
                raise ValueError(f"Duplicate chunk_id at line {line_number}: {chunk_id}")
            chunks[chunk_id] = chunk
    return chunks


def load_v2_annotations() -> tuple[dict, dict]:
    base_hash = sha256(BASE_ANNOTATIONS_PATH)
    if base_hash != EXPECTED_BASE_ANNOTATIONS_SHA256:
        raise ValueError(
            f"V1 annotation base changed: expected {EXPECTED_BASE_ANNOTATIONS_SHA256}, got {base_hash}"
        )
    base = load_json(BASE_ANNOTATIONS_PATH)
    overrides = load_json(OVERRIDES_PATH)
    if overrides.get("base_annotations_sha256") != base_hash:
        raise ValueError("V2 overrides do not reference the current V1 annotation base")
    annotations = json.loads(json.dumps(base, ensure_ascii=False))
    for group_id in overrides["removed_group_ids"]:
        annotations["groups"].pop(group_id)
    annotations["groups"].update(overrides["group_overrides"])
    annotations["case_notes"].update(overrides["case_note_overrides"])
    annotations["review_version"] = overrides["review_version"]
    return annotations, overrides


def minimum_required_gold_chunks(case: dict) -> int:
    groups = [
        group
        for group in case["gold"].get("literature_evidence_groups", [])
        if group["required"]
    ]
    if not groups:
        return 0
    candidates = sorted({chunk_id for group in groups for chunk_id in group["chunk_ids"]})

    def covers(selected: set[str]) -> bool:
        for group in groups:
            gold = set(group["chunk_ids"])
            if group["match"] == "any" and not (selected & gold):
                return False
            if group["match"] == "all" and not gold.issubset(selected):
                return False
        return True

    for size in range(1, len(candidates) + 1):
        if any(covers(set(selection)) for selection in combinations(candidates, size)):
            return size
    raise ValueError(f"No Gold set can cover all required groups for {case['id']}")


def reverse_mappings(gold: dict) -> dict[str, list[tuple[str, str, str]]]:
    mappings: dict[str, list[tuple[str, str, str]]] = {}
    for kind, key in (("Answer criterion", "answer_criteria"), ("Limitation", "limitations")):
        for item in gold.get(key, []):
            for group_id in item.get("literature_evidence_group_ids", []):
                mappings.setdefault(group_id, []).append((kind, item["id"], item["text"]))
    return mappings


def validate(
    cases: list[dict], chunks: dict[str, dict], annotations: dict, dataset_hash: str, chunks_hash: str
) -> tuple[int, int]:
    if dataset_hash != EXPECTED_DATASET_SHA256:
        raise ValueError(
            f"Evaluation draft changed: expected {EXPECTED_DATASET_SHA256}, got {dataset_hash}. "
            "Do not regenerate this review until the scope is re-audited."
        )
    if chunks_hash != EXPECTED_CHUNKS_SHA256:
        raise ValueError(
            f"Frozen chunks changed: expected {EXPECTED_CHUNKS_SHA256}, got {chunks_hash}. "
            "Do not regenerate this review against a different corpus."
        )
    if [case["id"] for case in cases] != CASE_IDS:
        raise ValueError("Selected case order or coverage is not LIT-001..010 followed by HYB-001..008")

    groups_by_id: dict[str, dict] = {}
    used_chunk_ids: set[str] = set()
    pair_count = 0
    for case in cases:
        mappings = reverse_mappings(case["gold"])
        valid_group_ids = {
            group["id"] for group in case["gold"].get("literature_evidence_groups", [])
        }
        valid_structured_ids = {
            item["id"] for item in case["gold"].get("structured_evidence", [])
        }
        for key in ("answer_criteria", "limitations"):
            for item in case["gold"].get(key, []):
                literature_refs = set(item.get("literature_evidence_group_ids", []))
                structured_refs = set(item.get("structured_evidence_ids", []))
                contracts = item.get("supporting_contracts", [])
                if not literature_refs.issubset(valid_group_ids):
                    raise ValueError(f"Unknown group mapping on {item['id']}")
                if not structured_refs.issubset(valid_structured_ids):
                    raise ValueError(f"Unknown structured mapping on {item['id']}")
                if not literature_refs and not structured_refs and not contracts:
                    raise ValueError(f"Criterion/limitation has no evidence mapping: {item['id']}")
        for group in case["gold"].get("literature_evidence_groups", []):
            group_id = group["id"]
            if group_id in groups_by_id:
                raise ValueError(f"Duplicate evidence group: {group_id}")
            groups_by_id[group_id] = group
            if group_id not in mappings:
                raise ValueError(f"Evidence group has no criterion/limitation mapping: {group_id}")
            for chunk_id in group["chunk_ids"]:
                if chunk_id not in chunks:
                    raise ValueError(f"Missing Gold chunk: {chunk_id}")
                if chunks[chunk_id].get("corpus_version") != EXPECTED_CORPUS_VERSION:
                    raise ValueError(f"Wrong corpus version for {chunk_id}")
                used_chunk_ids.add(chunk_id)
                pair_count += 1

    review_groups = annotations.get("groups", {})
    if set(review_groups) != set(groups_by_id):
        missing = sorted(set(groups_by_id) - set(review_groups))
        extra = sorted(set(review_groups) - set(groups_by_id))
        raise ValueError(f"Annotation coverage mismatch; missing={missing}, extra={extra}")

    for group_id, group in groups_by_id.items():
        review = review_groups[group_id]
        if review.get("suggested_decision") not in DECISIONS:
            raise ValueError(f"Invalid decision for {group_id}")
        if review.get("potential_issue") not in ISSUES:
            raise ValueError(f"Invalid potential issue for {group_id}")
        if set(review.get("chunks", {})) != set(group["chunk_ids"]):
            raise ValueError(f"Chunk annotation coverage mismatch for {group_id}")
        for chunk_id, chunk_review in review["chunks"].items():
            if chunk_review.get("rating") not in RATINGS:
                raise ValueError(f"Invalid rating for {group_id}/{chunk_id}")
            if not chunk_review.get("reason"):
                raise ValueError(f"Missing rationale for {group_id}/{chunk_id}")
            if group["match"] == "all" and not chunk_review.get("role"):
                raise ValueError(f"match=all chunk role missing for {group_id}/{chunk_id}")
        if group["match"] == "any" and any(
            item["rating"] != "SUPPORTED" for item in review["chunks"].values()
        ):
            raise ValueError(f"match=any contains a non-SUPPORTED Gold assignment: {group_id}")
        if group["match"] == "all":
            analysis = review.get("match_all_analysis", {})
            if not analysis.get("why_all") or not analysis.get("single_chunk_sufficiency"):
                raise ValueError(f"match=all analysis missing for {group_id}")

    if len(cases) != 18 or len(groups_by_id) != 44 or len(used_chunk_ids) != 24:
        raise ValueError(
            f"Unexpected scope counts: cases={len(cases)}, groups={len(groups_by_id)}, "
            f"unique_chunks={len(used_chunk_ids)}"
        )
    return pair_count, len(used_chunk_ids)


def md_escape_inline(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_report() -> str:
    dataset_hash = sha256(DATASET_PATH)
    chunks_hash = sha256(CHUNKS_PATH)
    base_annotations_hash = sha256(BASE_ANNOTATIONS_PATH)
    overrides_hash = sha256(OVERRIDES_PATH)
    dataset = load_json(DATASET_PATH)
    human_review = load_json(HUMAN_REVIEW_PATH)
    annotations, overrides = load_v2_annotations()
    chunks = load_chunks(CHUNKS_PATH)
    case_index = {case["id"]: case for case in dataset["cases"]}
    cases = [case_index[case_id] for case_id in CASE_IDS]
    pair_count, unique_chunk_count = validate(cases, chunks, annotations, dataset_hash, chunks_hash)

    decision_counts = Counter(
        review["suggested_decision"] for review in annotations["groups"].values()
    )
    rating_counts = Counter(
        chunk_review["rating"]
        for review in annotations["groups"].values()
        for chunk_review in review["chunks"].values()
    )
    selected_groups = [
        group
        for case in cases
        for group in case["gold"].get("literature_evidence_groups", [])
    ]
    required_count = sum(group["required"] for group in selected_groups)
    optional_count = len(selected_groups) - required_count
    match_counts = Counter(group["match"] for group in selected_groups)
    complete_evidence_minimums = {
        case["id"]: minimum_required_gold_chunks(case) for case in cases
    }
    cases_over_five = {
        case_id: count
        for case_id, count in complete_evidence_minimums.items()
        if count > 5
    }
    human_approved = (
        human_review.get("status") == "approved"
        and human_review.get("dataset_sha256") == dataset_hash
        and len(human_review.get("cases", [])) == 30
        and all(
            item.get("status") == "approved"
            and item.get("question_and_category_correct") is True
            and item.get("evidence_correct") is True
            and item.get("limitations_sufficient") is True
            for item in human_review.get("cases", [])
        )
    )
    freeze_manifest = load_json(FREEZE_MANIFEST_PATH) if FREEZE_MANIFEST_PATH.exists() else None
    if human_approved and not freeze_manifest:
        raise ValueError("Human review is approved but the freeze manifest is missing")
    if freeze_manifest:
        if not FROZEN_DATASET_PATH.exists():
            raise ValueError("Freeze manifest exists but frozen dataset is missing")
        if freeze_manifest.get("dataset_sha256") != sha256(FROZEN_DATASET_PATH):
            raise ValueError("Frozen dataset hash does not match its manifest")
        if freeze_manifest.get("draft_sha256") != dataset_hash:
            raise ValueError("Freeze manifest does not reference the reviewed draft")

    lines: list[str] = [
        "# Phase 5 — Evaluation Evidence Human Review V2",
        "",
        (
            "> **Status:** Final human review approved and `eval_dataset_v1` frozen."
            if human_approved
            else "> **Status:** Human-review packet only; final approval is pending."
        ),
        "",
        "## Review boundary",
        "",
        "This report audits every literature evidence group in `LIT-001`–`LIT-010` and `HYB-001`–`HYB-008`. "
        "Judgments use only each group's full Gold chunk text from frozen `literature_corpus_v1`.",
        "",
        "The review did **not** inspect retrieval baseline output, model-generated answers, or external knowledge. "
        "The evaluation draft was read only to reproduce case questions, claims, semantics, and criterion/limitation mappings.",
        "",
        "- Cases: 18",
        f"- Evidence groups: {len(selected_groups)}",
        f"- Required / optional groups: {required_count} / {optional_count}",
        f"- `match=any` / `match=all`: {match_counts['any']} / {match_counts['all']}",
        f"- Gold group–chunk assignments reviewed: {pair_count}",
        f"- Unique Gold chunks: {unique_chunk_count}",
        f"- Suggested group dispositions: APPROVE {decision_counts['APPROVE']}, MODIFY {decision_counts['MODIFY']}, REJECT {decision_counts['REJECT']}",
        f"- Independent chunk judgments: SUPPORTED {rating_counts['SUPPORTED']}, PARTIAL {rating_counts['PARTIAL']}, NOT SUPPORTED {rating_counts['NOT SUPPORTED']}",
        f"- Evaluation draft SHA-256: `{dataset_hash}`",
        f"- Frozen chunks SHA-256: `{chunks_hash}`",
        f"- Evaluation draft before SHA-256: `{overrides['dataset_before_sha256']}`",
        f"- V1 annotation base SHA-256: `{base_annotations_hash}`",
        f"- V2 overrides SHA-256: `{overrides_hash}`",
        f"- CompleteEvidence@5 structurally feasible: **{'yes' if not cases_over_five else 'no'}**",
        f"- Maximum minimum required distinct Gold chunks: {max(complete_evidence_minimums.values())}",
        f"- Cases requiring more than five distinct Gold chunks: {', '.join(cases_over_five) or 'none'}",
        f"- Human review status: **{human_review.get('status', 'unknown')}**",
        f"- Human reviewer: `{human_review.get('reviewer') or 'pending'}`",
        f"- Reviewed at: `{human_review.get('reviewed_at') or 'pending'}`",
        *(
            [
                f"- Frozen dataset SHA-256: `{freeze_manifest['dataset_sha256']}`",
                f"- Freeze manifest status: **{freeze_manifest['status']}**",
            ]
            if freeze_manifest
            else []
        ),
        "",
        "### Rating rule",
        "",
        "- **SUPPORTED:** the chunk alone supports the whole group claim at the stated scope.",
        "- **PARTIAL:** the chunk supports only part of the claim or requires an unstated inferential step.",
        "- **NOT SUPPORTED:** the chunk does not support the claim's material proposition.",
        "- For `match=any`, every chunk is judged as if it were the only retrieved Gold chunk.",
        "- For `match=all`, each chunk's role and the sufficiency of the combined set are reviewed separately.",
        "",
        "## Applied review revisions",
        "",
        "| Evidence group | V2 result |",
        "|---|---|",
    ]

    removed = set(overrides["removed_group_ids"])
    for group_id in overrides["modified_group_ids"]:
        result = "REMOVED" if group_id in removed else "APPROVE"
        lines.append(f"| `{group_id}` | {result} |")
    lines.extend(
        [
            "",
            (
                "> V2 incorporates the nine requested revisions. Final human approval is recorded for all 30 cases."
                if human_approved
                else "> V2 incorporates the nine requested revisions. Final human approval is still pending."
            ),
            "",
            "## CompleteEvidence@5 feasibility",
            "",
            "The minimum number below is the smallest number of distinct Gold chunks that can satisfy every `required=true` group in the case, respecting `any` and `all` semantics.",
            "",
            "| Case | Minimum distinct required Gold chunks | Feasible at K=5 |",
            "|---|---:|---|",
            *(
                f"| `{case_id}` | {count} | {'yes' if count <= 5 else 'no'} |"
                for case_id, count in complete_evidence_minimums.items()
            ),
            "",
            "## Case-by-case review",
            "",
        ]
    )

    for case in cases:
        case_id = case["id"]
        gold = case["gold"]
        mappings = reverse_mappings(gold)
        lines.extend(
            [
                f"## {case_id}",
                "",
                f"**Question:** {case['question']}",
                "",
            ]
        )
        case_note = annotations.get("case_notes", {}).get(case_id)
        if case_note:
            lines.extend([f"**Special review note:** {case_note}", ""])

        for group in gold["literature_evidence_groups"]:
            group_id = group["id"]
            review = annotations["groups"][group_id]
            lines.extend(
                [
                    f"### {group_id}",
                    "",
                    f"- **Case ID:** `{case_id}`",
                    f"- **Question:** {case['question']}",
                    f"- **Claim:** {group['claim']}",
                    f"- **required:** `{str(group['required']).lower()}`",
                    f"- **match:** `{group['match']}`",
                    "- **Mapped answer criterion / limitation:**",
                ]
            )
            for kind, item_id, text in mappings[group_id]:
                lines.append(f"  - `{item_id}` ({kind}) — {text}")
            lines.extend(["", f"**Group assessment:** {review['group_note']}", ""])

            for ordinal, chunk_id in enumerate(group["chunk_ids"], start=1):
                chunk = chunks[chunk_id]
                chunk_review = review["chunks"][chunk_id]
                lines.extend(
                    [
                        f"#### Gold chunk {ordinal}: `{chunk_id}`",
                        "",
                        f"- **Source:** {chunk['title']} ({chunk.get('year', 'n/a')}); section `{chunk['section']}`; PMCID `{chunk.get('pmcid', 'n/a')}`",
                        f"- **Corpus version:** `{chunk['corpus_version']}`",
                        f"- **Independent support judgment:** **{chunk_review['rating']}**",
                        f"- **Reason:** {chunk_review['reason']}",
                    ]
                )
                if group["match"] == "all":
                    lines.append(f"- **Role in all-of claim:** {chunk_review['role']}")
                lines.extend(
                    [
                        "",
                        "**Full Gold chunk text:**",
                        "",
                        "```text",
                        chunk["text"],
                        "```",
                        "",
                    ]
                )

            if group["match"] == "all":
                analysis = review["match_all_analysis"]
                lines.extend(
                    [
                        "#### `match=all` analysis",
                        "",
                        f"- **Why all chunks are or are not necessary:** {analysis['why_all']}",
                        f"- **Could one chunk be sufficient?:** {analysis['single_chunk_sufficiency']}",
                        "",
                    ]
                )

            selected = review["suggested_decision"]
            lines.extend(["#### Human Review", "", "**Suggested human decision:**", ""])
            for decision in ("APPROVE", "MODIFY", "REJECT"):
                mark = "x" if decision == selected else " "
                lines.append(f"- [{mark}] {decision}")
            lines.extend(["", f"**Potential issue:** `{review['potential_issue']}`", ""])
            if review.get("issue_detail"):
                lines.extend([f"**Issue detail:** {review['issue_detail']}", ""])
            lines.extend(
                [
                    "**Human reviewer final decision:**",
                    "",
                    f"- [{'x' if human_approved else ' '}] APPROVE",
                    "- [ ] MODIFY",
                    "- [ ] REJECT",
                    "",
                    "**Human reviewer notes:**",
                    "",
                    "---",
                    "",
                ]
            )

    lines.extend(
        [
            "## Integrity statement",
            "",
            "This report is generated from immutable-hash-checked V2 inputs. The generator aborts if the revised evaluation draft, frozen chunks file, or V1 annotation base differs from the hashes recorded above. It writes only this V2 report and does not freeze the dataset, update PostgreSQL, or mutate Gold labels.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.write_text(build_report(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
