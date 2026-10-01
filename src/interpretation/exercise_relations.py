"""Approved dataset-specific relations; never modify frozen aliases or rows."""

import hashlib
import json
from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[2]


def _key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


class RuntimeExerciseRelations:
    def __init__(self, path=None):
        path = Path(path) if path else ROOT / "config/runtime_exercise_relations_v1.json"
        raw = path.read_bytes()
        self.config = json.loads(raw)
        if self.config["version"] != "runtime_exercise_relations_v1":
            raise ValueError("unknown_runtime_exercise_relations")
        self.sha256 = hashlib.sha256(raw).hexdigest()

    def group_for(self, value):
        if not value:
            return None
        matches = [group for group in self.config["groups"]
                   if _key(value) in {_key(x) for x in [*group["members"], *group["mentions"]]}]
        if len(matches) > 1:
            raise ValueError("conflicting_runtime_groups")
        return matches[0] if matches else None

    def restriction(self, question):
        text = unicodedata.normalize("NFKC", question)
        for rule in self.config["restricted_generic_mentions"]:
            qualifier = "|".join(rule["qualifiers"])
            for pattern in rule["patterns"]:
                for match in re.finditer(r"(?<![A-Za-z가-힣])(?:" + pattern + r")(?![A-Za-z])", text, re.I):
                    if not rule["qualifiers"]:
                        return rule
                    before, after = text[:match.start()], text[match.end():]
                    prefix = re.search(r"(?:" + qualifier + r")\s*$", before, re.I)
                    suffix = re.match(r"\s*(?:\(\s*)?(?:" + qualifier + r")(?![A-Za-z])", after, re.I)
                    if not prefix and not suffix:
                        return rule
        return None

    def provenance(self):
        return {"runtime_exercise_relations_version": self.config["version"],
                "runtime_exercise_relations_sha256": self.sha256,
                "scope": self.config["scope"]}
