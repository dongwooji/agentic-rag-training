"""Deterministic isolation of the literature side of a routed Hybrid question.

This adapter uses only the question and the deterministic Router's existing
matched-pattern provenance.  It does not alter routing, retrieval, grading, or
recovery semantics and does not use evaluation labels.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_CLAUSE_BOUNDARY = re.compile(r"[,;，；]\s+|\n")
_SENTENCE_END = re.compile(r"[?？.!]")


def _text_matches(question: str, values: Sequence[Any]) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    for value in values:
        term = str(value).strip()
        if not term:
            continue
        match = re.search(re.escape(term), question, re.IGNORECASE)
        if match:
            output.append((match.start(), match.end()))
    return output


def _regex_matches(question: str, patterns: Sequence[Any]) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    for value in patterns:
        pattern = str(value).strip()
        if not pattern:
            continue
        try:
            match = re.search(pattern, question, re.IGNORECASE)
        except re.error:
            continue
        if match:
            output.append((match.start(), match.end()))
    return output


def _matched_patterns(route: Mapping[str, Any]) -> Mapping[str, Any]:
    rule_match = route.get("rule_match")
    if not isinstance(rule_match, Mapping):
        return {}
    patterns = rule_match.get("matched_patterns")
    return patterns if isinstance(patterns, Mapping) else {}


def derive_literature_subquestion(
    question: str,
    route: Mapping[str, Any],
) -> str:
    """Return a bounded literature-only clause for a Hybrid route.

    Scientific concept and literature-regex matches are preferred as the
    anchor.  If only an explicit word such as ``문헌`` matched, the clause
    containing that word is retained so its scientific subject is not lost.
    The initial retrieval query is intentionally unaffected by this function.
    """

    original = str(question).strip()
    if not original or str(route.get("task_type") or "") != "hybrid":
        return original

    patterns = _matched_patterns(route)
    scientific = [
        *_text_matches(original, patterns.get("literature_concepts", [])),
        *_regex_matches(original, patterns.get("literature_regex", [])),
    ]
    explicit = _text_matches(original, patterns.get("literature_explicit", []))
    anchors = scientific or explicit
    if not anchors:
        # A Hybrid route should carry one of these Router signals.  Fail closed
        # to the original wording rather than inventing a new scientific claim.
        return original

    anchor_start = min(item[0] for item in anchors)
    prior_boundaries = [
        match.end()
        for match in _CLAUSE_BOUNDARY.finditer(original, 0, anchor_start)
    ]
    start = prior_boundaries[-1] if prior_boundaries else anchor_start

    sentence_end = _SENTENCE_END.search(original, anchor_start)
    end = sentence_end.end() if sentence_end else len(original)
    isolated = original[start:end].strip(" \t\r\n,;，；")
    return isolated or original
