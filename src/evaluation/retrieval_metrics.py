from __future__ import annotations

from statistics import mean
from typing import Any, Iterable


def literature_evidence_groups(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the literature groups used by retrieval evaluation."""
    return list(case.get("gold", {}).get("literature_evidence_groups", []))


def required_literature_groups(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [group for group in literature_evidence_groups(case) if group["required"]]


def group_is_covered(group: dict[str, Any], retrieved_chunk_ids: Iterable[str]) -> bool:
    retrieved = set(retrieved_chunk_ids)
    gold = set(group["chunk_ids"])
    if group["match"] == "any":
        return bool(retrieved & gold)
    if group["match"] == "all":
        return gold.issubset(retrieved)
    raise ValueError(f"Unsupported evidence-group match mode: {group['match']}")


def evidence_group_recall_at_k(
    case: dict[str, Any], ranked_chunk_ids: list[str], k: int
) -> float:
    """Fraction of required claim groups covered by the top-k results."""
    groups = required_literature_groups(case)
    if not groups:
        raise ValueError(f"Case {case.get('id')} has no required literature groups")
    top_k = ranked_chunk_ids[:k]
    return sum(group_is_covered(group, top_k) for group in groups) / len(groups)


def complete_evidence_at_k(
    case: dict[str, Any], ranked_chunk_ids: list[str], k: int
) -> float:
    """One only when every required claim group is covered in the top-k."""
    groups = required_literature_groups(case)
    if not groups:
        raise ValueError(f"Case {case.get('id')} has no required literature groups")
    top_k = ranked_chunk_ids[:k]
    return float(all(group_is_covered(group, top_k) for group in groups))


def required_gold_chunk_ids(case: dict[str, Any]) -> set[str]:
    return {
        chunk_id
        for group in required_literature_groups(case)
        for chunk_id in group["chunk_ids"]
    }


def chunk_recall_at_k(
    case: dict[str, Any], ranked_chunk_ids: list[str], k: int
) -> float:
    """Traditional set-based chunk recall, retained as a diagnostic metric."""
    gold = required_gold_chunk_ids(case)
    if not gold:
        raise ValueError(f"Case {case.get('id')} has no required gold chunks")
    return len(gold & set(ranked_chunk_ids[:k])) / len(gold)


def reciprocal_rank(case: dict[str, Any], ranked_chunk_ids: list[str]) -> float:
    """Reciprocal rank of the first chunk in any required evidence group."""
    gold = required_gold_chunk_ids(case)
    if not gold:
        raise ValueError(f"Case {case.get('id')} has no required gold chunks")
    for rank, chunk_id in enumerate(ranked_chunk_ids, start=1):
        if chunk_id in gold:
            return 1.0 / rank
    return 0.0


def evaluate_retrieval(
    cases: list[dict[str, Any]],
    ranked_results: dict[str, list[str]],
    *,
    ks: tuple[int, ...] = (5, 10),
) -> dict[str, Any]:
    """Compute per-case and macro metrics for literature-bearing cases."""
    per_case: dict[str, dict[str, float]] = {}
    for case in cases:
        case_id = case["id"]
        if case_id not in ranked_results:
            raise ValueError(f"Missing ranked results for {case_id}")
        ranked = ranked_results[case_id]
        metrics: dict[str, float] = {"reciprocal_rank": reciprocal_rank(case, ranked)}
        for k in ks:
            metrics[f"evidence_group_recall@{k}"] = evidence_group_recall_at_k(
                case, ranked, k
            )
            metrics[f"complete_evidence@{k}"] = complete_evidence_at_k(case, ranked, k)
            metrics[f"chunk_recall@{k}"] = chunk_recall_at_k(case, ranked, k)
        per_case[case_id] = metrics

    metric_names = list(next(iter(per_case.values())).keys()) if per_case else []
    macro = {
        ("mrr" if name == "reciprocal_rank" else name): mean(
            metrics[name] for metrics in per_case.values()
        )
        for name in metric_names
    }
    return {
        "case_count": len(per_case),
        "ks": list(ks),
        "macro": macro,
        "per_case": per_case,
    }

