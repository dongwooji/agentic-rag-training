"""Runtime-only read adapter for actual training.exercises canonical labels."""

import hashlib
import json

from pydantic import create_model
from typing import Literal

from .contracts import InterpretationDraft


class RepositoryExerciseCatalog:
    def __init__(self, repository):
        self.repository = repository

    def names(self) -> tuple[str, ...]:
        # A repository port supports offline fixtures; production reuses its
        # existing read-only connection rather than changing the frozen Tool.
        if callable(getattr(self.repository, "list_canonical_exercises", None)):
            values = self.repository.list_canonical_exercises()
        else:
            rows = self.repository._fetch_all(
                "SELECT canonical_name FROM training.exercises ORDER BY canonical_name", ()
            )
            values = [row["canonical_name"] for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("invalid_canonical_catalog")
        names = tuple(sorted(set(values)))
        if not names:
            raise ValueError("empty_canonical_catalog")
        return names


def catalog_sha256(names: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(names, ensure_ascii=False).encode("utf-8")).hexdigest()


def candidate_schema(names: tuple[str, ...]):
    """Constrain the existing Interpreter output, not a second LLM request."""
    if not names:
        raise ValueError("empty_canonical_catalog")
    allowed = Literal.__getitem__(names)
    return create_model(
        "CatalogInterpretationDraft", __base__=InterpretationDraft,
        canonical_exercise_name=(allowed | None, ...),
        candidate_exercises=(list[allowed], ...),
        exercise_resolution_status=(Literal["resolved", "ambiguous", "not_found"] | None, ...),
    )
