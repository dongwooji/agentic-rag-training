"""Validate schema-grounded extraction without selecting Tools or calculating values."""

from datetime import date
import re
import unicodedata

from src.graph.tool_input_resolver import ExerciseCanonicalizer
from src.routing.runtime_language import InputLanguageError, RuntimeQuestionNormalizer
from .contracts import InterpretationDraft, QuestionInterpretation, UnresolvedField
from .exercise_selection import ExerciseSelectionPolicy


class GroundingError(ValueError):
    pass


LITERATURE_REQUEST = re.compile(r"문헌|논문|메타분석|과학적 근거|학술 근거|연구 결과|literature|research evidence", re.I)
PERSONAL_REQUEST = re.compile(r"(?:내|나의|제|저의)\s*(?:운동|훈련|기록|데드|벤치|스쿼트|가장|최근)|실제 기록", re.I)
_SESSION_NUMBER = re.compile(r"(\d+|하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:[- ]?session|세션)", re.I)
_NUMBERS = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4,
            "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_ANALYSIS_SIGNALS = {
    "weekly_volume": r"weekly\s+volume|주간\s*(?:훈련량|운동량|볼륨)",
    "weekly_frequency": r"weekly\s+frequency|주간\s*빈도|주당\s*운동\s*횟수",
    "training_gap": r"training\s+gap|운동\s*공백|훈련\s*공백",
    "plateau_candidates": r"plateau\s+candidate|정체",
}


def mentioned_dates(question: str) -> list[str]:
    output = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", question)
    output += [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in re.findall(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", question)]
    return list(dict.fromkeys(output))


def validate_interpretation(question, raw, *, source, canonicalizer: ExerciseCanonicalizer, normalizer=None, canonical_exercises=None, exercise_relations=None):
    draft = InterpretationDraft.model_validate(raw)
    normalizer = normalizer or RuntimeQuestionNormalizer()
    original_nfkc = unicodedata.normalize("NFKC", question)
    issues = list(draft.unresolved_fields)
    policies = []

    def unresolved(field, reason):
        if not any(x.field == field and x.reason == reason for x in issues):
            issues.append(UnresolvedField(field=field, reason=reason))

    try:
        normalized = normalizer.normalize(question).question
    except InputLanguageError:
        normalized = original_nfkc
        if draft.personal_record_requested:
            unresolved("canonical_exercise_name", "운동의 변형 또는 복수 운동을 명확히 지정해주세요.")

    # Original substring and approved normalization together ground exercise names.
    if draft.exercise_mention:
        mention = unicodedata.normalize("NFKC", draft.exercise_mention)
        in_original = mention.casefold() in original_nfkc.casefold()
        if not in_original and (source == "llm" or mention.casefold() not in normalized.casefold()):
            raise GroundingError("exercise mention is absent from the question")
    canonical = None
    restriction = exercise_relations.restriction(question) if exercise_relations and draft.personal_record_requested else None
    if restriction:
        unresolved("canonical_exercise_name", restriction["message"])
        policies.append("runtime_restriction:" + restriction["id"])
    supplied_candidates = list(dict.fromkeys(draft.candidate_exercises))
    if supplied_candidates and (not draft.exercise_mention or not draft.personal_record_requested):
        raise GroundingError("candidates need an original personal exercise mention")
    if any((canonical_exercises is not None and name not in canonical_exercises) or
           canonicalizer.repository.resolve_canonical_exercise(name) != name for name in supplied_candidates):
        raise GroundingError("exercise candidate is not in the stored catalog")
    trusted = ExerciseSelectionPolicy().candidates(draft.exercise_mention, question) if draft.personal_record_requested else []
    if supplied_candidates and not trusted and draft.exercise_mention:
        try:
            original_candidate = canonicalizer.canonicalize(normalizer.normalize(draft.exercise_mention).question)
        except InputLanguageError:
            original_candidate = None
        if original_candidate and any(name != original_candidate for name in supplied_candidates):
            raise GroundingError("candidates conflict with a known explicit exercise")
    if trusted and supplied_candidates and not set(supplied_candidates).issubset(trusted):
        raise GroundingError("candidate conflicts with explicit exercise selection policy")
    candidates = trusted or supplied_candidates
    if candidates and not restriction:
        if any(canonicalizer.repository.resolve_canonical_exercise(name) != name for name in candidates):
            candidates = []
            unresolved("canonical_exercise_name", "운동 후보의 저장 이름을 모두 확인할 수 없습니다.")
        else:
            if draft.canonical_exercise_name not in {None, *candidates}:
                raise GroundingError("canonical selection conflicts with candidates")
            # Explicit qualifiers take priority over a model's generic ambiguity.
            issues = [x for x in issues if x.field not in {"canonical_exercise_name", "exercise_resolution_status", "candidate_exercises"}]
            if len(candidates) > 1:
                unresolved("canonical_exercise_name", "여러 저장 운동이 있습니다. 조회할 운동을 선택해주세요.")
            elif not trusted and source == "llm" and draft.exercise_resolution_status != "resolved":
                unresolved("canonical_exercise_name", "후보의 운동 종류 또는 변형을 확인해주세요.")
            else:
                canonical = candidates[0]
            policies.append("exercise_selection_v1")
    elif source == "llm" and canonical_exercises is not None:
        candidate = draft.canonical_exercise_name
        status = draft.exercise_resolution_status
        if draft.personal_record_requested:
            if status in {"ambiguous", "not_found"}:
                unresolved("canonical_exercise_name", "조회할 운동의 종류 또는 변형을 명확히 지정해주세요.")
            elif not draft.exercise_mention:
                unresolved("canonical_exercise_name", "원문의 운동 표현을 확인해주세요.")
            elif candidate not in canonical_exercises:
                unresolved("canonical_exercise_name", "제공된 운동 후보 목록에서 운동을 확인할 수 없습니다.")
            else:
                # Reuse certain existing aliases on the extracted original
                # mention, not on a whole sentence with Korean verb suffixes.
                try:
                    mention_normalized = normalizer.normalize(draft.exercise_mention).question
                except InputLanguageError:
                    mention_normalized = draft.exercise_mention
                known = canonicalizer.canonicalize(mention_normalized)
                if known is not None and known != candidate:
                    raise GroundingError("candidate conflicts with the original exercise mention")
                if status != "resolved":
                    unresolved("canonical_exercise_name", "운동 후보 선택 상태를 확인해주세요.")
                else:
                    canonical = canonicalizer.canonicalize(candidate)
                    if canonical != candidate:
                        unresolved("canonical_exercise_name", "현재 저장된 운동 목록에서 후보를 확인할 수 없습니다.")
                    else:
                        policies.append("runtime_canonical_catalog_membership_v1")
    elif draft.canonical_exercise_name:
        if not canonicalizer.is_grounded(normalized, draft.canonical_exercise_name):
            raise GroundingError("canonical exercise is not grounded in the question")
        if draft.personal_record_requested:
            canonical = canonicalizer.canonicalize(draft.canonical_exercise_name)
            if canonical is None:
                unresolved("canonical_exercise_name", "해당 운동을 저장된 canonical 운동명으로 확인할 수 없습니다.")
    elif draft.personal_record_requested:
        unresolved("canonical_exercise_name", "조회할 운동을 명확히 지정해주세요.")

    time = draft.time_condition
    found_dates = mentioned_dates(original_nfkc)
    for value in (time.start_date, time.end_date):
        if value is not None:
            date.fromisoformat(value)
            if value not in found_dates:
                raise GroundingError("date is absent from the question")
    if time.start_date and time.end_date and time.start_date > time.end_date:
        raise GroundingError("date range is reversed")
    if time.scope == "explicit" and not (time.start_date and time.end_date):
        unresolved("time_condition", "분석 기간의 시작일과 종료일을 확인해주세요.")
    if time.scope == "explicit" and set(found_dates) != {time.start_date, time.end_date}:
        unresolved("time_condition", "원문의 날짜 조건을 모두 보존해야 합니다.")
    if time.scope == "all_records" and (found_dates or time.start_date or time.end_date):
        unresolved("time_condition", "명시된 날짜 조건을 전체 기록으로 대체할 수 없습니다.")
    if time.scope == "unresolved":
        unresolved("time_condition", "분석 기간을 지정해주세요.")
    if time.scope == "all_records":
        if re.search(r"최근|요즘|지난|이번|정체|전보다", question) and not re.search(r"처음|첫|초반|first", question, re.I):
            unresolved("time_condition", "최근 또는 비교 분석의 기간을 지정해주세요.")
        else:
            policies.append("omitted_period_means_all_stored_records_v1")

    n_values = {int(x) if x.isdigit() else _NUMBERS[x] for x in _SESSION_NUMBER.findall(original_nfkc)}
    operations = {x.operation.value for x in draft.requested_analyses}
    for analysis in draft.requested_analyses:
        if analysis.source_text and analysis.source_text.casefold() not in original_nfkc.casefold():
            raise GroundingError("analysis source is absent from the question")
        if source == "llm" and not analysis.source_text:
            raise GroundingError("LLM analysis needs an original source span")
        if analysis.n_sessions is not None:
            if analysis.n_sessions not in n_values:
                raise GroundingError("N is absent from the question's session conditions")
            if analysis.operation.value != "first_last_n_session_median_e1rm":
                raise GroundingError("N is not applicable to this operation")
        if analysis.operation.value == "first_last_n_session_median_e1rm":
            if analysis.n_sessions is None:
                unresolved("n_sessions", "처음과 최근 비교에 사용할 세션 수를 지정해주세요.")
            if len(n_values) > 1:
                unresolved("n_sessions", "처음과 최근의 동일한 세션 수를 지정해주세요.")
            if not re.search(r"처음|최초|첫|초반|first", question, re.I) or not re.search(r"최근|마지막|last|latest", question, re.I):
                unresolved("comparison", "처음과 최근 세션의 비교 조건을 확인해주세요.")
    for operation, pattern in _ANALYSIS_SIGNALS.items():
        if re.search(pattern, question, re.I) and operation not in operations:
            unresolved("requested_analyses", f"질문에 명시된 {operation} 분석이 해석에서 누락되었습니다.")
    if re.search(r"e1rm|추정\s*1rm", question, re.I) and not operations.intersection({"estimated_1rm", "first_last_n_session_median_e1rm"}):
        unresolved("requested_analyses", "질문의 e1RM 분석 요청을 확인해주세요.")
    if len(draft.requested_analyses) > 1:
        unresolved("requested_analyses", "Phase A는 한 요청에서 하나의 Metric만 지원합니다. 지표별로 질문해주세요.")
    if draft.personal_record_requested and re.search(r"훈련량.{0,30}근력.{0,30}각각", question):
        unresolved("requested_analyses", "훈련량과 근력 지표의 복수 분석은 Phase A에서 함께 실행할 수 없습니다.")
    if draft.requested_analyses and not draft.personal_record_requested:
        unresolved("personal_record_requested", "개인 기록을 사용하는 Metric 요청의 범위를 확인해주세요.")
    if PERSONAL_REQUEST.search(question) and not draft.personal_record_requested:
        unresolved("personal_record_requested", "질문의 개인 기록 요청이 누락되었습니다.")
    if LITERATURE_REQUEST.search(question) and not draft.literature_requested:
        unresolved("literature_requested", "질문의 문헌 요청이 누락되었습니다.")
    if draft.literature_requested and not (draft.literature_subquestion or "").strip():
        unresolved("literature_subquestion", "문헌에서 확인할 질문을 지정해주세요.")
    if draft.personal_record_requested and draft.literature_subquestion:
        if re.search(r"내\s*(?:기록|운동|훈련)|median\s*e1rm|e1rm.{0,15}(?:계산|비교)|\d{4}-\d{2}-\d{2}", draft.literature_subquestion, re.I):
            unresolved("literature_subquestion", "문헌 하위 질문에서 개인 기록 계산 요구를 분리해주세요.")
    if not draft.literature_requested and draft.literature_subquestion:
        unresolved("literature_requested", "문헌 질문과 문헌 요청 여부가 일치하지 않습니다.")
    if draft.personal_record_requested and draft.record_operation is None:
        unresolved("record_operation", "조회 작업을 지정해주세요.")
    if draft.record_operation not in {None, "exercise_records", "list_sessions"}:
        unresolved("record_operation", "Phase A의 전체/기간 기록 조회로 표현할 수 없는 세션 선택입니다.")
    if draft.session_id:
        if draft.session_id not in question:
            raise GroundingError("session ID is absent from the question")
        unresolved("session_id", "Phase A에서는 특정 session ID 조회를 실행하지 않습니다.")
    for assumption in draft.user_assumptions:
        if assumption.source_text not in question or assumption.text not in question:
            raise GroundingError("user assumption must be an original question span")
    if re.search(r"가정|전제", question) and not draft.user_assumptions:
        unresolved("user_assumptions", "사용자 가정을 기록 사실과 분리해주세요.")
    if draft.clarification_required and not issues and not (candidates and canonical):
        unresolved("question", "실행에 필요한 조건을 추가로 확인해주세요.")
    if not draft.personal_record_requested and not draft.literature_requested:
        unresolved("question", "운동 기록 조회 또는 문헌 질문의 범위를 지정해주세요.")
    payload = draft.model_dump()
    if restriction:
        canonical = None
    payload.update(canonical_exercise_name=canonical,
                   candidate_exercises=[] if restriction else (candidates or ([canonical] if canonical else [])),
                   exercise_resolution_status="ambiguous" if restriction or len(candidates) > 1 else ("resolved" if canonical else (draft.exercise_resolution_status or "not_found")),
                   unresolved_fields=issues, clarification_required=bool(issues),
                   original_question=question, normalized_question=normalized, parsing_source=source, applied_policies=policies)
    return QuestionInterpretation.model_validate(payload)
