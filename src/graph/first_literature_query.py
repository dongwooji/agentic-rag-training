"""First-query selection only; no case labels, Gold, model calls or retrieval."""

from dataclasses import asdict, dataclass
import re
from typing import Any, Literal, Mapping

from .literature_scope import derive_literature_subquestion


QUERY_POLICY_VERSION = 'first_literature_query_step2_v1'
# Safety checks do not rewrite the scientific question or introduce new anchors.
PERSONAL_CONTEXT = re.compile(
    r'\d{4}-\d{2}-\d{2}|(?:내|나의|저의|제)\s*(?:기록|운동|훈련)|'
    r'(?:median\s*e1rm|\d+[- ]?session|\d+\s*회\s*세션)|'
    r'e1rm.{0,20}(?:계산|비교|변화)|(?:training\s+gap|plateau\s+candidate|정체\s*후보\s*구간)',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FirstLiteratureQuery:
    query: str
    query_source: Literal['phase_a', 'deterministic', 'original']
    isolation_status: Literal['isolated', 'original_already_suitable', 'safe_fallback', 'validation_failed']
    fallback: bool = False
    fallback_reason: str | None = None
    policy_version: str = QUERY_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _original(question: str, status: str, reason: str | None = None) -> FirstLiteratureQuery:
    return FirstLiteratureQuery(question, 'original', status, reason is not None, reason)


def select_phase_a_query(question: str, route: Mapping[str, Any], *, validation_status: str, literature_subquestion: str | None) -> FirstLiteratureQuery:
    """Use validated Phase A text. Evaluation retains failed cases on H1 wording."""
    if validation_status != 'ready':
        return _original(question, 'validation_failed', 'phase_a_' + validation_status)
    task = route.get('task_type')
    if task == 'literature_only':
        return _original(question, 'original_already_suitable')
    if task != 'hybrid':
        return _original(question, 'safe_fallback', 'phase_a_no_hybrid_literature_route')
    if not isinstance(literature_subquestion, str) or not literature_subquestion.strip():
        return _original(question, 'validation_failed', 'phase_a_missing_literature_subquestion')
    if literature_subquestion == question:
        return _original(question, 'safe_fallback', 'phase_a_subquestion_equals_original')
    return FirstLiteratureQuery(literature_subquestion, 'phase_a', 'isolated')


def select_deterministic_query(question: str, route: Mapping[str, Any]) -> FirstLiteratureQuery:
    """Retain the existing derive result only when a distinct, safe clause exists."""
    if route.get('task_type') == 'literature_only':
        return _original(question, 'original_already_suitable')
    if route.get('task_type') != 'hybrid':
        return _original(question, 'safe_fallback', 'deterministic_no_hybrid_literature_route')
    derived = derive_literature_subquestion(question, route)
    if not derived.strip() or derived.strip() == question.strip():
        return _original(question, 'safe_fallback', 'deterministic_no_separable_clause')
    if PERSONAL_CONTEXT.search(derived):
        return _original(question, 'safe_fallback', 'deterministic_personal_context_remains')
    return FirstLiteratureQuery(derived, 'deterministic', 'isolated')
