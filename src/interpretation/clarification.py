"""Short-lived, single-use local selection state, not conversational memory."""

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from time import time
from uuid import uuid4

from .contracts import QuestionInterpretation


class ClarificationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ClarificationStore:
    def __init__(self, *, ttl_seconds=1200, capacity=1024, clock=time):
        self.ttl_seconds, self.capacity, self.clock = ttl_seconds, capacity, clock
        self._entries, self._lock = {}, Lock()

    def create(self, base, interpretation, relations):
        names = interpretation.candidate_exercises
        if len(names) < 2:
            raise ValueError("selection requires multiple candidates")
        options = [dict(id=str(i + 1), label=name, canonical_exercises=[name]) for i, name in enumerate(names)]
        # Joint lookup is explicit and limited to an existing approved group.
        group = relations.group_for(names[0])
        if group and set(group["members"]) == set(names):
            options.append(dict(id=str(len(options) + 1), label="두 기록 함께", canonical_exercises=list(names)))
        now = self.clock()
        identifier = uuid4().hex
        with self._lock:
            self._entries = {key: item for key, item in self._entries.items() if item["expires_at"] > now}
            if len(self._entries) >= self.capacity:
                raise ClarificationError("clarification_capacity_reached")
            self._entries[identifier] = deepcopy(dict(base=base, interpretation=interpretation.model_dump(mode="json"),
                options=options, created_at=now, expires_at=now + self.ttl_seconds))
        return dict(response_mode="clarification", clarification_required=True, clarification_id=identifier,
                    clarification_type="exercise_selection", options=options,
                    expires_at=datetime.fromtimestamp(now + self.ttl_seconds, timezone.utc).isoformat())

    def consume(self, identifier, selected_option):
        with self._lock:
            entry = self._entries.get(identifier)
            if entry is None:
                raise ClarificationError("clarification_not_found")
            if entry["expires_at"] <= self.clock():
                del self._entries[identifier]
                raise ClarificationError("clarification_expired")
            option = next((x for x in entry["options"] if x["id"] == selected_option), None)
            if option is None:
                raise ClarificationError("invalid_selection")
            del self._entries[identifier]
            return deepcopy(entry), deepcopy(option)


def selected_interpretation(payload, names):
    """Change only exercise resolution. Other unresolved fields remain blocking."""
    interpretation = QuestionInterpretation.model_validate(payload)
    if not names or not set(names).issubset(interpretation.candidate_exercises):
        raise ClarificationError("invalid_selection")
    issues = [x for x in interpretation.unresolved_fields
              if x.field not in {"canonical_exercise_name", "exercise_resolution_status", "candidate_exercises"}]
    return interpretation.model_copy(update=dict(canonical_exercise_name=names[-1],
        candidate_exercises=list(names), exercise_resolution_status="resolved", unresolved_fields=issues,
        clarification_required=bool(issues), applied_policies=[*interpretation.applied_policies, "user_exercise_selection_v1"]))
