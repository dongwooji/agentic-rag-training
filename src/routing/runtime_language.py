"""Bounded language aliases for the API; frozen preprocessing stays untouched."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config/runtime_input_language_v1.json"
LANGUAGE_VERSION = "runtime_korean_input_v1"
_LEFT = r"(?<![가-힣A-Za-z0-9])"
_RIGHT = r"(?=$|[^가-힣A-Za-z0-9]|(?:의|은|는|이|가|을|를|와|과|에서|부터|까지)(?=$|[^가-힣A-Za-z0-9]))"
_NUMBERS = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3,
    "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7,
    "여덟": 8, "아홉": 9, "열": 10,
}
_SESSION = re.compile(
    r"(?:처음|최초|첫|최근|마지막|first|last|latest)\s*"
    r"(?P<n>\d+|여덟|아홉|다섯|여섯|일곱|하나|한|둘|두|셋|세|넷|네|열)"
    r"\s*(?:개(?:의)?\s*)?(?:훈련\s*)?(?:세션|[- ]?\s*session)",
    re.IGNORECASE,
)


def phrase_pattern(value: str) -> str:
    return r"\s*".join(re.escape(part) for part in value.split())


class InputLanguageError(ValueError):
    """An ambiguous expression must not be silently assigned arguments."""


@dataclass(frozen=True)
class NormalizedQuestion:
    question: str
    replacements: tuple[dict[str, str], ...]


class RuntimeQuestionNormalizer:
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        raw = config_path.read_bytes()
        self.config_sha256 = hashlib.sha256(raw).hexdigest()
        self.config = json.loads(raw)
        if self.config.get("version") != LANGUAGE_VERSION:
            raise ValueError("Unsupported runtime input language version")
        self.exercise_aliases: dict[str, list[str]] = self.config["exercise_aliases"]

    def normalize(self, question: str) -> NormalizedQuestion:
        text = unicodedata.normalize("NFKC", question)
        replacements: list[dict[str, str]] = []
        candidates = [
            (alias, canonical)
            for canonical, aliases in self.exercise_aliases.items()
            for alias in [canonical, *aliases]
        ]
        candidates.sort(key=lambda item: (-len(item[0]), item[0]))
        targets: set[str] = set()
        # Resolve longest phrases first: incline bench press must not become bench press.
        protected: list[tuple[int, int]] = []
        edits: list[tuple[int, int, str]] = []
        modifiers = "|".join(
            phrase_pattern(value)
            for value in self.config["unsupported_exercise_modifiers"]
        )
        for alias, canonical in candidates:
            pattern = _LEFT + phrase_pattern(alias) + _RIGHT
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if any(match.start() < end and match.end() > start for start, end in protected):
                    continue
                before = text[:match.start()]
                after = text[match.end():]
                if re.search(r"(?:" + modifiers + r")\s*$", before, re.I) or re.match(
                    r"\s*\((?:" + modifiers + r")s?\)", after, re.I
                ):
                    raise InputLanguageError("운동의 장비 또는 변형을 지원되는 운동명으로 명확히 지정해주세요.")
                # An unknown equipment qualifier must not be turned into a barbell label.
                if "(" not in alias and re.match(r"\s*\(", after):
                    raise InputLanguageError("괄호에 지정된 운동 장비를 확인해주세요.")
                targets.add(canonical)
                protected.append((match.start(), match.end()))
                edits.append((match.start(), match.end(), canonical))
        if len(targets) > 1:
            raise InputLanguageError("현재는 한 요청에서 하나의 운동만 계산합니다. 운동별로 질문해주세요.")
        for start, end, canonical in sorted(edits, reverse=True):
            source = text[start:end]
            if source != canonical:
                replacements.append({"source": source, "target": canonical})
                text = text[:start] + canonical + text[end:]

        for pattern in self.config["personal_record_patterns"]:
            def record_replace(match: re.Match[str]) -> str:
                replacement = "내 운동 기록"
                if match.group() != replacement:
                    replacements.append({"source": match.group(), "target": replacement})
                return replacement

            text = re.sub(pattern, record_replace, text)

        session_values: set[int] = set()

        def session_replace(match: re.Match[str]) -> str:
            raw_n = match.group("n")
            n = int(raw_n) if raw_n.isdigit() else _NUMBERS[raw_n]
            session_values.add(n)
            prefix = "처음" if re.match(r"처음|최초|첫|first", match.group(), re.I) else "최근"
            replacement = f"{prefix} {n}-session"
            replacements.append({"source": match.group(), "target": replacement})
            return replacement

        text = _SESSION.sub(session_replace, text)
        if len(session_values) > 1:
            raise InputLanguageError("처음과 최근 비교에 사용할 세션 수를 동일하게 지정해주세요.")

        metric_candidates = [
            (alias, canonical)
            for canonical, aliases in self.config["metric_aliases"].items()
            for alias in aliases
        ]
        for alias, canonical in sorted(metric_candidates, key=lambda item: -len(item[0])):
            # Korean metric names may be directly followed by a particle.
            pattern = _LEFT + phrase_pattern(alias) + _RIGHT

            def metric_replace(match: re.Match[str], target: str = canonical) -> str:
                replacements.append({"source": match.group(), "target": target})
                return target

            text = re.sub(pattern, metric_replace, text, flags=re.IGNORECASE)
        return NormalizedQuestion(text, tuple(replacements))
