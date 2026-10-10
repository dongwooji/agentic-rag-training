"""One initial query generation and optional translation; never Router or Gold input."""

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class GenerationDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    has_personal_context: bool
    literature_query: str


class TranslationDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    literature_query: str


@dataclass(frozen=True)
class QueryProviderResult:
    payload: dict[str, Any] | None
    provenance: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class QueryProvider(Protocol):
    def invoke(self, text: str) -> QueryProviderResult: ...


# General syntax, never IDs, dataset labels or retrieval results.
PERSONAL_PATTERNS = {
    "personal_date": r"(?<!\d)(?:\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{4}\s*년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?)?|\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}[-/]\d{1,2}[-/]\d{4})(?!\d)",
    "personal_session_condition": r"\d+\s*[-–]?\s*(?:sessions?(?![A-Za-z])|세션|회\s*세션)|(?:최근|마지막|last|recent)\s*\d+\s*(?:회|sessions?)",
    "canonical_catalog_form": r"[A-Za-z][A-Za-z /-]*\s*\((?:Barbell|Dumbbell|Cable|Machine|Smith Machine|Bodyweight|Kettlebell|EZ Bar)\)",
    "record_judgement": r"plateau[ _-]*candidate|정체\s*후보|training[ _-]*gap|(?:기록|세션)\s*(?:판정|구간)|(?:median|평균|중앙값)\s*e1RM",
}
NUMBER = r"\d+(?:\.\d+)?"
WEIGHT = re.compile(rf"(?:e1RM|weight|중량|체중)\s*(?:값|value|[:=]|은|는|이|가)?\s*({NUMBER})\s*(kg|킬로그램|lb|lbs|파운드)?|({NUMBER})\s*(kg|킬로그램|lb|lbs|파운드)", re.I)
PERSONAL_MARKER = re.compile(r"내\s|나의|제\s|my\b|기록|세션|session|e1RM|중량\s*(?:값|[:=])", re.I)
ACRONYM = re.compile(r"(?<![A-Za-z0-9])(?:e1RM|1RM|RIR|RPE)(?![A-Za-z])")


def validate_generation(original: str, query: str) -> list[str]:
    """Called only when has_personal_context=True. Scientific numbers are legal."""
    reasons = [name for name, pattern in PERSONAL_PATTERNS.items() if re.search(pattern, query, re.I)]
    # Only weight values tied to personal-record clauses in the ORIGINAL input.
    for clause in re.split(r"[;!?\n]|\.(?!\d)", original):
        if not PERSONAL_MARKER.search(clause):
            continue
        for match in WEIGHT.finditer(clause):
            value = match.group(1) or match.group(3)
            unit = match.group(2) or match.group(4)
            if unit:
                units = r"(?:kg|킬로그램)" if unit.casefold() in ("kg", "킬로그램") else r"(?:lb|lbs|파운드)"
                pattern = rf"(?<![\d.]){re.escape(value)}\s*{units}(?![A-Za-z])"
            else:
                pattern = rf"(?:e1RM|weight|중량|체중)\s*(?:값|value|[:=]|은|는|이|가)?\s*{re.escape(value)}(?![\d.]|\s*%)"
            if re.search(pattern, query, re.I):
                reasons.append("personal_record_value")
    return list(dict.fromkeys(reasons))


# Unit synonyms preserve dimensional units; no conversion (seconds != minutes).
UNIT_GROUPS = {
    "seconds": r"seconds?|secs?|초",
    "minutes": r"minutes?|mins?|분",
    "hours": r"hours?|hrs?|시간",
    "days": r"days?|일",
    "weeks": r"weeks?|주",
    "months": r"months?|개월|월",
    "years": r"years?|년",
    "kg": r"kg|kilograms?|킬로그램",
    "lb": r"lbs?|pounds?|파운드",
    "percent": r"%|percent",
    "sessions": r"sessions?|회\s*세션|세션",
    "reps": r"reps?|repetitions?|times?|회|번",
    "sets": r"sets?|세트",
    "cm": r"cm|centimeters?|센티미터",
    "m": r"m|meters?|미터",
    "km": r"km|kilometers?|킬로미터",
    "m_per_second": r"m/s|meters?\s*(?:per|/)\s*seconds?|미터\s*/\s*초",
    "km_per_hour": r"km/h|kilometers?\s*(?:per|/)\s*hours?|킬로미터\s*/\s*시간",
    "m_per_second_squared": r"m/s[²^]2?|meters?\s*(?:per|/)\s*seconds?\s*squared",
    "g": r"g|grams?|그램",
    "watts": r"W|watts?|와트",
    "newtons": r"N|newtons?|뉴턴",
    "volts": r"V|volts?|볼트",
    "amps": r"A|amps?|amperes?|암페어",
}

TRANSLATION_VALIDATOR_VERSION = "translation_units_v2"
DATE_LITERAL = re.compile(PERSONAL_PATTERNS["personal_date"], re.I)


def _translation_signature(text: str):
    acronyms = Counter(ACRONYM.findall(text))
    text = ACRONYM.sub(" ", text)
    numbers = Counter(re.findall(NUMBER, text))
    # Dates remain in the number signature, but their last component is not a
    # quantity whose unit is the next word in prose.
    text = DATE_LITERAL.sub(" ", text)
    quantities = Counter()
    unit_matches = {}
    for unit, pattern in UNIT_GROUPS.items():
        for match in re.finditer(rf"({NUMBER})\s*[-–]?\s*({pattern})(?![A-Za-z/²³^])", text, re.I):
            previous = unit_matches.get(match.start())
            if previous is None or match.end() > previous[0]:
                unit_matches[match.start()] = (match.end(), match.group(1), unit)
    for _, value, unit in unit_matches.values():
        quantities[(value, unit)] += 1
    # Unknown identifiers attached to a number remain literal units. For
    # whitespace-separated text only the explicit unit vocabulary above is
    # recognized: ordinary prose after a number is not a dimensional unit.
    for match in re.finditer(rf"({NUMBER})([A-Za-zµμ°][A-Za-zµμ°²³/^*-]*)", text):
        identifier = match.group(2)
        if identifier.casefold() in ('vs', 'versus', 'to', 'and', 'or'):
            continue
        if not any(re.fullmatch(pattern, identifier, re.I) for pattern in UNIT_GROUPS.values()):
            quantities[(match.group(1), 'literal:'+identifier.casefold())] += 1
    return numbers, quantities, acronyms


def validate_translation(source: str, translated: str) -> list[str]:
    before, after = _translation_signature(source), _translation_signature(translated)
    return [name for name, old, new in zip(
        ("number_preservation", "unit_preservation", "acronym_preservation"), before, after
    ) if old != new]


def _invoke(provider: QueryProvider, text: str) -> QueryProviderResult:
    try:
        return provider.invoke(text)
    except Exception:
        # Never expose exception messages or arbitrary provider details.
        return QueryProviderResult(None, error="provider_failure")


def generate_korean(provider: QueryProvider, original: str) -> dict[str, Any]:
    result = _invoke(provider, original)
    row = dict(input_query=original, generated_query=None, actual_query=original,
               has_personal_context=None, query_source="original", isolation_status="validation_failed",
               validation_status="failed", fallback=True, fallback_reasons=[], provenance=result.provenance)
    if result.error:
        row["fallback_reasons"] = ["provider_failure"]
        return row
    try:
        draft = GenerationDraft.model_validate(result.payload)
    except Exception:
        row["fallback_reasons"] = ["schema_validation"]
        return row
    row.update(generated_query=draft.literature_query, has_personal_context=draft.has_personal_context)
    # False explicitly ignores the output, including an empty/incorrect rewrite.
    if not draft.has_personal_context:
        row.update(validation_status="passed", isolation_status="original_already_suitable", fallback=False)
        return row
    query = draft.literature_query.strip()
    reasons = validate_generation(original, query) if query else ["empty_query"]
    if reasons:
        row["fallback_reasons"] = reasons
        return row
    row.update(actual_query=query, query_source="generator", validation_status="passed", fallback=False,
               isolation_status="isolated" if query != original else "original_already_suitable")
    return row


def translate_english(provider: QueryProvider, step2_query: str) -> dict[str, Any]:
    result = _invoke(provider, step2_query)
    row = dict(input_query=step2_query, generated_query=None, actual_query=step2_query,
               query_source="step2",
               validation_status="failed", fallback=True, fallback_reasons=[], provenance=result.provenance)
    if result.error:
        row["fallback_reasons"] = ["provider_failure"]
        return row
    try:
        draft = TranslationDraft.model_validate(result.payload)
    except Exception:
        row["fallback_reasons"] = ["schema_validation"]
        return row
    row["generated_query"] = draft.literature_query
    query = draft.literature_query.strip()
    reasons = validate_translation(step2_query, query) if query else ["empty_query"]
    if reasons:
        row["fallback_reasons"] = reasons
        return row
    row.update(actual_query=query, query_source="translation", validation_status="passed", fallback=False)
    return row


class LiteratureQueryGenerator:
    def __init__(self, generation: QueryProvider, translation: QueryProvider | None = None):
        self.generation, self.translation = generation, translation

    def prepare(self, original: str, mode: str) -> dict[str, Any]:
        if mode not in ("generated_ko", "translated_bm25", "translated_both"):
            raise ValueError("Unsupported query generation mode")
        step2 = generate_korean(self.generation, original)
        step3 = None
        if mode != "generated_ko":
            if self.translation is None:
                raise ValueError("Translation provider is required")
            step3 = translate_english(self.translation, step2["actual_query"])
        ko = step2["actual_query"]
        en = step3["actual_query"] if step3 else ko
        return dict(step2=step2, step3=step3, dense_query=en if mode == "translated_both" else ko,
                    bm25_query=en, mode=mode)
