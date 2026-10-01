"""Bounded, dataset-backed ambiguity rules; no fuzzy string merging."""

import json
from pathlib import Path
from .exercise_relations import _key


class ExerciseSelectionPolicy:
    def __init__(self):
        self.config = json.loads((Path(__file__).resolve().parents[2] / "config/exercise_selection_v1.json").read_text(encoding="utf-8"))

    def candidates(self, mention, question=""):
        key = _key(mention or "")
        explicit = [rule["canonical"] for rule in self.config["explicit_mentions"]
                    if any(_key(x) in _key(question) and key and key in _key(x) for x in rule["mentions"])]
        if explicit:
            return list(dict.fromkeys(explicit))
        for rule in self.config["explicit_mentions"]:
            if key in {_key(x) for x in rule["mentions"]}:
                return [rule["canonical"]]
        for rule in self.config["candidate_sets"]:
            if key in {_key(x) for x in rule["mentions"]}:
                return list(rule["candidates"])
        return []
