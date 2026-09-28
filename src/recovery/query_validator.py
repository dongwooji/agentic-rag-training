"""Deterministic validation for model-proposed literature recovery queries."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .contracts import (
    EvidenceRecoveryInput,
    RecoveryErrorCode,
    RecoveryQueryDraft,
)


def normalize_query(value: str) -> str:
    """Normalize Unicode, case, and whitespace without erasing punctuation."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _deduplicate(values: list[str], *, normalized: bool = False) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_query(value) if normalized else value
        if key in seen:
            continue
        seen.add(key)
        result.append(value.strip())
    return result


class RecoveryQueryValidationError(ValueError):
    def __init__(self, code: RecoveryErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ValidatedRecoveryQuery:
    target_component_ids: list[str]
    recovery_query: str
    normalized_recovery_query: str
    preserved_terms: list[str]


def validate_recovery_query(
    recovery_input: EvidenceRecoveryInput,
    draft: RecoveryQueryDraft,
) -> ValidatedRecoveryQuery:
    target_ids = _deduplicate(draft.target_component_ids)
    available_ids = {
        item.component_id for item in recovery_input.missing_components
    }
    invalid_ids = [item for item in target_ids if item not in available_ids]
    if not target_ids or invalid_ids:
        invalid_text = ", ".join(invalid_ids) if invalid_ids else "none supplied"
        raise RecoveryQueryValidationError(
            RecoveryErrorCode.INVALID_TARGET_COMPONENT,
            f"target component IDs must be current missing components: {invalid_text}",
        )

    query = draft.recovery_query.strip()
    normalized_query = normalize_query(query)
    if not normalized_query:
        raise RecoveryQueryValidationError(
            RecoveryErrorCode.EMPTY_QUERY,
            "recovery query must be non-empty",
        )

    prior_queries = [recovery_input.previous_query, *recovery_input.query_history]
    prior_normalized = {
        normalized
        for item in prior_queries
        if (normalized := normalize_query(item))
    }
    if normalized_query in prior_normalized:
        raise RecoveryQueryValidationError(
            RecoveryErrorCode.DUPLICATE_QUERY,
            "recovery query duplicates the previous query or query history",
        )

    preserved_terms = _deduplicate(draft.preserved_terms, normalized=True)
    preserved_terms = [item for item in preserved_terms if normalize_query(item)]
    absent_terms = [
        item
        for item in preserved_terms
        if normalize_query(item) not in normalized_query
    ]
    if absent_terms:
        raise RecoveryQueryValidationError(
            RecoveryErrorCode.PRESERVED_TERM_MISSING,
            "reported preserved terms are absent from the recovery query: "
            + ", ".join(absent_terms),
        )

    return ValidatedRecoveryQuery(
        target_component_ids=target_ids,
        recovery_query=query,
        normalized_recovery_query=normalized_query,
        preserved_terms=preserved_terms,
    )
