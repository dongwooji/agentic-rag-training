"""Full-match allowlist: recognizing a keyword never establishes completeness."""

import re

from src.routing.runtime_language import InputLanguageError, RuntimeQuestionNormalizer
from .contracts import InterpretationDraft


class RuleQuestionParser:
    def __init__(self, normalizer=None):
        self.normalizer = normalizer or RuntimeQuestionNormalizer()

    def parse(self, question: str) -> InterpretationDraft | None:
        try:
            normalized = self.normalizer.normalize(question)
            text = normalized.question
        except InputLanguageError:
            return None
        exercises = "|".join(re.escape(x) for x in self.normalizer.exercise_aliases)
        prefix = rf"(?:내 운동 기록에서\s*|(?:내|제|저의|나의)\s+)(?P<exercise>{exercises})(?:의|에서)?\s*"
        dates = r"(?:(?P<start>20\d{2}-\d{2}-\d{2})(?:부터\s*(?P<end>20\d{2}-\d{2}-\d{2})까지)?\s*)?"
        terminal = r"(?:을|를)?\s*(?:계산해줘|보여줘|조회해줘|비교해줘|정리해줘)[.!?]?"
        patterns = [
            ("first_last_n_session_median_e1rm", r"처음\s*(?P<n>\d+)-session과\s*최근\s*(?P<n2>\d+)-session(?:의)?\s*median\s+e1RM"),
            ("weekly_volume", r"weekly volume"),
            ("weekly_frequency", r"weekly frequency"),
            ("training_gap", r"(?:가장 긴\s*)?training gap"),
            (None, r"(?:운동\s*)?기록"),
        ]
        for operation, body in patterns:
            match = re.fullmatch(prefix + dates + body + terminal, text, re.I)
            if not match:
                continue
            values = match.groupdict()
            n = int(values["n"]) if values.get("n") else None
            if n is not None and (n != int(values["n2"]) or not 1 <= n <= 50):
                return None
            start, end = values.get("start"), values.get("end")
            # For a single explicit date, both inclusive bounds refer to that day.
            return InterpretationDraft(
                personal_record_requested=True, literature_requested=False,
                exercise_mention=next((item["source"] for item in normalized.replacements
                                       if item["target"] == values["exercise"]), values["exercise"]),
                canonical_exercise_name=values["exercise"], exercise_resolution_status="resolved",
                time_condition={"scope": "explicit" if start else "all_records", "start_date": start, "end_date": end or start,
                                "source_text": ""},
                record_operation="exercise_records", session_id=None,
                requested_analyses=[] if operation is None else [{"operation": operation, "n_sessions": n, "source_text": ""}],
                literature_subquestion=None, user_assumptions=[], unresolved_fields=[], clarification_required=False,
            )
        return None
