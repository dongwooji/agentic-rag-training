from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

from src.evaluation.reference import compute_reference


EXPECTED_DISTRIBUTION = {
    "literature_only": 10,
    "log_metric": 6,
    "hybrid": 8,
    "unanswerable": 6,
}
ALLOWED_TOOLS = {
    "search_literature",
    "query_training_log",
    "compute_metrics",
}
ALLOWED_SUPPORT_CONTRACTS = {
    "dataset_limitations_v1",
    "metric_definition_v1",
    "observational_noncausality",
    "session_semantics_v1",
    "source_unit_unknown",
}
BANNED_LEAKAGE_KEYS = {
    "retrieval_rank",
    "retrieval_score",
    "retrieved_chunks",
    "baseline_output",
    "model_answer",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def minimum_required_gold_chunks(case: dict[str, Any]) -> int:
    """Return the smallest distinct chunk set satisfying all required groups."""
    groups = [
        group
        for group in case.get("gold", {}).get("literature_evidence_groups", [])
        if group.get("required") is True
    ]
    if not groups:
        return 0
    candidates = sorted({chunk_id for group in groups for chunk_id in group["chunk_ids"]})

    def covers(selected: set[str]) -> bool:
        for group in groups:
            gold = set(group["chunk_ids"])
            if group["match"] == "any" and not selected.intersection(gold):
                return False
            if group["match"] == "all" and not gold.issubset(selected):
                return False
        return True

    for size in range(1, len(candidates) + 1):
        if any(covers(set(selection)) for selection in combinations(candidates, size)):
            return size
    raise ValueError(f"No chunk combination covers all required groups for {case.get('id')}")


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_all_keys(nested))
    return keys


def _compare_expected(
    expected: Any,
    actual: Any,
    *,
    tolerance: float,
    path: str = "expected",
) -> list[str]:
    errors: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        for key, value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing from computed result")
            else:
                errors.extend(
                    _compare_expected(
                        value,
                        actual[key],
                        tolerance=tolerance,
                        path=f"{path}.{key}",
                    )
                )
        return errors
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [
                f"{path}: expected list length {len(expected)}, "
                f"got {len(actual) if isinstance(actual, list) else 'non-list'}"
            ]
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            errors.extend(
                _compare_expected(
                    expected_item,
                    actual_item,
                    tolerance=tolerance,
                    path=f"{path}[{index}]",
                )
            )
        return errors
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return [f"{path}: expected numeric {expected}, got {actual!r}"]
        if abs(float(expected) - float(actual)) > tolerance:
            return [
                f"{path}: expected {expected}, computed {actual}, tolerance {tolerance}"
            ]
        return []
    if expected != actual:
        errors.append(f"{path}: expected {expected!r}, computed {actual!r}")
    return errors


def validate_dataset(dataset_path: Path, workspace: Path) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset_hash = sha256_file(dataset_path)
    cases = dataset.get("cases", [])
    chunks_path = workspace / "data/literature/processed/chunks.jsonl"
    chunk_rows = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chunks_by_id = {row["chunk_id"]: row for row in chunk_rows}

    errors: list[str] = []
    ids = [case.get("id") for case in cases]
    questions = [case.get("question") for case in cases]
    distribution = Counter(case.get("category") for case in cases)
    required_tool_counts: Counter[str] = Counter()
    gold_chunk_ids: list[str] = []
    required_gold_chunk_ids: list[str] = []
    evidence_group_count = 0
    required_evidence_group_count = 0
    optional_evidence_group_count = 0
    group_match_counts: Counter[str] = Counter()
    mapped_criterion_count = 0
    mapped_limitation_count = 0
    reference_check_count = 0
    reference_errors: dict[str, list[str]] = {}
    group_errors: list[str] = []
    mapping_errors: list[str] = []

    if dataset.get("dataset_version") != "eval_dataset_v1":
        errors.append("dataset_version must be eval_dataset_v1")
    if dataset.get("status") != "draft_human_review_pending":
        errors.append("draft status must be draft_human_review_pending")
    if dict(distribution) != EXPECTED_DISTRIBUTION:
        errors.append(
            f"category distribution {dict(distribution)} != {EXPECTED_DISTRIBUTION}"
        )
    if len(cases) != 30:
        errors.append(f"expected 30 cases, got {len(cases)}")
    if len(ids) != len(set(ids)):
        errors.append("case IDs are not unique")
    if len(questions) != len(set(questions)):
        errors.append("questions are not unique")
    if BANNED_LEAKAGE_KEYS & _all_keys(dataset):
        errors.append(
            "retrieval-output leakage keys found: "
            + ", ".join(sorted(BANNED_LEAKAGE_KEYS & _all_keys(dataset)))
        )
    metric_policy = dataset.get("protocol", {}).get("retrieval_metric_policy", {})
    required_metric_policy_keys = {
        "evidence_group_recall_at_k",
        "complete_evidence_at_k",
        "chunk_recall_at_k",
        "reciprocal_rank",
        "aggregation",
        "default_k",
    }
    metric_policy_valid = (
        set(metric_policy) == required_metric_policy_keys
        and metric_policy.get("default_k") == [5, 10]
    )
    if not metric_policy_valid:
        errors.append("retrieval metric policy is incomplete or invalid")

    for source in dataset.get("source_artifacts", []):
        source_path = workspace / source["path"]
        if not source_path.exists():
            errors.append(f"source artifact missing: {source['path']}")
            continue
        actual_hash = sha256_file(source_path)
        if actual_hash != source["sha256"]:
            errors.append(
                f"source hash mismatch for {source['path']}: "
                f"{source['sha256']} != {actual_hash}"
            )

    for case in cases:
        case_id = str(case.get("id"))
        category = case.get("category")
        behavior = case.get("expected_behavior")
        tools = case.get("required_tools", [])
        gold = case.get("gold", {})
        literature_groups = gold.get("literature_evidence_groups", [])
        structured = gold.get("structured_evidence", [])
        answer_criteria = gold.get("answer_criteria", [])
        limitations = gold.get("limitations", [])
        human_status = case.get("review", {}).get("human_status")

        if not case.get("question") or not case.get("topic_tags"):
            errors.append(f"{case_id}: question and topic_tags are required")
        if not answer_criteria:
            errors.append(f"{case_id}: answer_criteria must not be empty")
        if human_status != "pending":
            errors.append(f"{case_id}: human_status must remain pending in draft")
        if not set(tools).issubset(ALLOWED_TOOLS):
            errors.append(f"{case_id}: unknown required tool")
        required_tool_counts.update(tools)

        if "literature_chunk_ids" in gold:
            group_errors.append(
                f"{case_id}: legacy literature_chunk_ids field is not allowed"
            )
        if category in {"literature_only", "hybrid"} and not literature_groups:
            group_errors.append(
                f"{case_id}: literature-bearing case has no evidence groups"
            )
        if category in {"log_metric", "unanswerable"} and literature_groups:
            group_errors.append(
                f"{case_id}: category must not have literature evidence groups"
            )
        if literature_groups and gold.get("required_group_semantics") != "all_required":
            group_errors.append(
                f"{case_id}: required_group_semantics must be all_required"
            )
        if category in {"log_metric", "hybrid"} and not structured:
            errors.append(f"{case_id}: structured case has no structured evidence")
        if category in {"literature_only", "unanswerable"} and structured:
            errors.append(f"{case_id}: category must not have structured evidence")
        if category == "unanswerable":
            if behavior != "abstain" or tools:
                errors.append(
                    f"{case_id}: unanswerable cases must abstain without tool calls"
                )
            if not case.get("gold", {}).get("missing_fields"):
                errors.append(f"{case_id}: missing_fields are required")
        elif behavior != "answer":
            errors.append(f"{case_id}: answerable case must use expected_behavior=answer")

        structured_ids = [evidence.get("id") for evidence in structured]
        if len(structured_ids) != len(set(structured_ids)) or any(
            not value for value in structured_ids
        ):
            mapping_errors.append(f"{case_id}: structured evidence IDs are invalid")

        group_ids = [group.get("id") for group in literature_groups]
        if len(group_ids) != len(set(group_ids)) or any(not value for value in group_ids):
            group_errors.append(f"{case_id}: evidence group IDs are invalid")
        evidence_group_count += len(literature_groups)
        mapped_group_ids: set[str] = set()
        for group in literature_groups:
            group_id = group.get("id")
            chunk_ids = group.get("chunk_ids", [])
            match = group.get("match")
            required = group.get("required")
            if not group.get("claim"):
                group_errors.append(f"{case_id}/{group_id}: claim is required")
            if match not in {"any", "all"}:
                group_errors.append(
                    f"{case_id}/{group_id}: match must be any or all"
                )
            else:
                group_match_counts[match] += 1
            if not isinstance(required, bool):
                group_errors.append(
                    f"{case_id}/{group_id}: required must be boolean"
                )
            elif required:
                required_evidence_group_count += 1
            else:
                optional_evidence_group_count += 1
            if not chunk_ids or len(chunk_ids) != len(set(chunk_ids)):
                group_errors.append(
                    f"{case_id}/{group_id}: chunk_ids must be non-empty and unique"
                )
            for chunk_id in chunk_ids:
                gold_chunk_ids.append(chunk_id)
                if required:
                    required_gold_chunk_ids.append(chunk_id)
                if chunk_id not in chunks_by_id:
                    group_errors.append(
                        f"{case_id}/{group_id}: unknown chunk_id {chunk_id}"
                    )

        item_ids: list[str] = []
        for item_kind, items in (
            ("answer criterion", answer_criteria),
            ("limitation", limitations),
        ):
            for item in items:
                if not isinstance(item, dict) or not item.get("id") or not item.get("text"):
                    mapping_errors.append(
                        f"{case_id}: {item_kind} must have stable id and text"
                    )
                    continue
                item_ids.append(item["id"])
                literature_refs = item.get("literature_evidence_group_ids", [])
                structured_refs = item.get("structured_evidence_ids", [])
                contracts = item.get("supporting_contracts", [])
                mapped_group_ids.update(literature_refs)
                if not set(literature_refs).issubset(set(group_ids)):
                    mapping_errors.append(
                        f"{case_id}/{item['id']}: unknown literature evidence group"
                    )
                if not set(structured_refs).issubset(set(structured_ids)):
                    mapping_errors.append(
                        f"{case_id}/{item['id']}: unknown structured evidence ID"
                    )
                if not set(contracts).issubset(ALLOWED_SUPPORT_CONTRACTS):
                    mapping_errors.append(
                        f"{case_id}/{item['id']}: unknown supporting contract"
                    )
                if not literature_refs and not structured_refs and not contracts:
                    mapping_errors.append(
                        f"{case_id}/{item['id']}: item has no evidence mapping"
                    )
                if item_kind == "answer criterion":
                    mapped_criterion_count += 1
                else:
                    mapped_limitation_count += 1
        if len(item_ids) != len(set(item_ids)):
            mapping_errors.append(f"{case_id}: criterion/limitation IDs are not unique")
        unmapped_groups = set(group_ids) - mapped_group_ids
        if unmapped_groups:
            mapping_errors.append(
                f"{case_id}: evidence groups not mapped to criteria/limitations: "
                + ", ".join(sorted(unmapped_groups))
            )

        for evidence in structured:
            reference_check_count += 1
            operation = evidence.get("operation")
            try:
                actual = compute_reference(
                    operation,
                    evidence.get("parameters", {}),
                    sets_path=workspace / "data/processed/workout_sets.csv",
                    lineage_path=workspace / "data/processed/row_lineage.csv",
                )
                comparison_errors = _compare_expected(
                    evidence.get("expected", {}),
                    actual,
                    tolerance=float(evidence.get("numeric_tolerance", 1e-6)),
                )
                if comparison_errors:
                    reference_errors.setdefault(case_id, []).extend(comparison_errors)
            except Exception as exc:  # validation must report the bad label
                reference_errors.setdefault(case_id, []).append(str(exc))

    if reference_errors:
        errors.append("one or more structured gold labels failed recomputation")
    errors.extend(group_errors)
    errors.extend(mapping_errors)

    complete_evidence_minimums = {
        case["id"]: minimum_required_gold_chunks(case)
        for case in cases
        if case.get("gold", {}).get("literature_evidence_groups")
    }
    complete_evidence_cases_over_5 = {
        case_id: count
        for case_id, count in complete_evidence_minimums.items()
        if count > 5
    }
    if complete_evidence_cases_over_5:
        errors.append(
            "CompleteEvidence@5 is structurally impossible for: "
            + ", ".join(
                f"{case_id} ({count})"
                for case_id, count in complete_evidence_cases_over_5.items()
            )
        )

    external_review_path = workspace / "data/evaluation/human_review_v1.json"
    external_review: dict[str, Any] | None = None
    if external_review_path.exists():
        candidate = json.loads(external_review_path.read_text(encoding="utf-8"))
        if candidate.get("dataset_sha256") == dataset_hash:
            external_review = candidate
    if external_review is not None:
        review_items = external_review.get("cases", [])
        human_review_approved = sum(
            item.get("status") == "approved"
            and item.get("question_and_category_correct") is True
            and item.get("evidence_correct") is True
            and item.get("limitations_sufficient") is True
            for item in review_items
        )
        human_review_pending = len(cases) - human_review_approved
        human_review_fully_approved = (
            external_review.get("status") == "approved"
            and len(review_items) == len(cases)
            and human_review_approved == len(cases)
        )
    else:
        human_review_pending = sum(
            case.get("review", {}).get("human_status") == "pending" for case in cases
        )
        human_review_approved = len(cases) - human_review_pending
        human_review_fully_approved = human_review_pending == 0
    checks = {
        "dataset_version_is_v1": dataset.get("dataset_version") == "eval_dataset_v1",
        "draft_status_is_explicit": dataset.get("status")
        == "draft_human_review_pending",
        "case_count_is_30": len(cases) == 30,
        "category_distribution_matches_plan": dict(distribution)
        == EXPECTED_DISTRIBUTION,
        "case_ids_unique": len(ids) == len(set(ids)),
        "questions_unique": len(questions) == len(set(questions)),
        "source_hashes_match": not any("source hash mismatch" in error for error in errors),
        "gold_chunk_ids_exist": not any("unknown chunk_id" in error for error in errors),
        "claim_evidence_groups_valid": not group_errors,
        "criteria_and_limitations_mapped": not mapping_errors,
        "complete_evidence_at_5_feasible": not complete_evidence_cases_over_5,
        "any_and_all_semantics_present": group_match_counts["any"] > 0
        and group_match_counts["all"] > 0,
        "retrieval_metric_policy_frozen": metric_policy_valid,
        "structured_labels_recompute": not reference_errors,
        "no_retrieval_output_leakage": not bool(BANNED_LEAKAGE_KEYS & _all_keys(dataset)),
        "human_review_still_pending": human_review_pending == 30,
        "human_review_approved": human_review_fully_approved,
    }
    automated_checks = {
        key: value
        for key, value in checks.items()
        if key not in {"human_review_still_pending", "human_review_approved"}
    }
    try:
        dataset_label = dataset_path.relative_to(workspace).as_posix()
    except ValueError:
        dataset_label = str(dataset_path)
    return {
        "dataset_version": dataset.get("dataset_version"),
        "dataset_path": dataset_label,
        "dataset_sha256": dataset_hash,
        "all_automated_checks_passed": all(automated_checks.values()) and not errors,
        "ready_to_freeze": all(automated_checks.values()) and not errors and human_review_fully_approved,
        "checks": checks,
        "case_count": len(cases),
        "category_counts": dict(distribution),
        "required_tool_counts": dict(required_tool_counts),
        "literature_query_count": sum(
            case.get("category") in {"literature_only", "hybrid"} for case in cases
        ),
        "unique_gold_chunk_count": len(set(gold_chunk_ids)),
        "unique_required_gold_chunk_count": len(set(required_gold_chunk_ids)),
        "evidence_group_count": evidence_group_count,
        "required_evidence_group_count": required_evidence_group_count,
        "optional_evidence_group_count": optional_evidence_group_count,
        "group_match_counts": dict(group_match_counts),
        "complete_evidence_at_5": {
            "feasible": not complete_evidence_cases_over_5,
            "minimum_required_distinct_chunks_by_case": complete_evidence_minimums,
            "maximum_minimum_required_distinct_chunks": max(
                complete_evidence_minimums.values(), default=0
            ),
            "cases_over_5": complete_evidence_cases_over_5,
        },
        "mapped_answer_criterion_count": mapped_criterion_count,
        "mapped_limitation_count": mapped_limitation_count,
        "structured_reference_check_count": reference_check_count,
        "human_review": {
            "pending": human_review_pending,
            "approved": human_review_approved,
            "status": external_review.get("status") if external_review else "embedded_draft_status",
        },
        "reference_errors": reference_errors,
        "errors": errors,
    }


def write_validation_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
