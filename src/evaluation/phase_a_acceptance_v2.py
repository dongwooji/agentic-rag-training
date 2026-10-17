"""Acceptance v2 offline labels/scoring; never imported by runtime."""
from typing import Literal

from pydantic import Field, model_validator
from src.agent.contracts import StrictModel
from src.evaluation.phase_a_semantics import GoldCase as V1Case, unique, score as score_v1


class GoldCase(V1Case):
    literature_answerability: Literal["answerable", "unanswerable", "not_applicable"]

    @model_validator(mode="after")
    def literature_scope(self):
        required = "literature" in self.required_evidence_sources
        if required == (self.literature_answerability == "not_applicable"):
            raise ValueError("answerability_must_match_required_literature_channel")
        return self


class GoldFile(StrictModel):
    version: Literal["phase_a_evidence_gold_v2"]
    cases: list[GoldCase] = Field(min_length=1)

    @model_validator(mode="after")
    def ids(self):
        unique([x.case_id for x in self.cases], "case_id")
        return self


def score(gold, observations):
    labels = GoldFile.model_validate(gold)
    legacy = dict(version="phase_a_evidence_gold_v1", cases=[
        x.model_dump(mode="json", exclude={"literature_answerability"}) for x in labels.cases])
    result = score_v1(legacy, observations)
    for case, actual, row in zip(labels.cases, observations, result["rows"], strict=True):
        row["literature_answerability"] = case.literature_answerability
        row["answerable_abstain"] = case.literature_answerability == "answerable" and actual["behavior"] == "abstain"
        row["unanswerable_answer"] = case.literature_answerability == "unanswerable" and actual["behavior"] == "answer"
    for key in ("answerable_abstain", "unanswerable_answer"):
        result["totals"][key] = sum(row[key] for row in result["rows"])
    result["totals"]["answerability_denominators"] = {
        label: sum(x.literature_answerability == label for x in labels.cases)
        for label in ("answerable", "unanswerable", "not_applicable")}
    return result


def compare(before, after):
    if [x["case_id"] for x in before["rows"]] != [x["case_id"] for x in after["rows"]]:
        raise ValueError("different_cases_or_order")
    a, b = before["totals"], after["totals"]
    if a["answerability_denominators"] != b["answerability_denominators"]:
        raise ValueError("different_denominators")
    criteria = dict(
        answerable_abstain_decreased=b["answerable_abstain"] < a["answerable_abstain"],
        unanswerable_answer_not_increased=b["unanswerable_answer"] <= a["unanswerable_answer"],
        execution_failure_not_increased=b["execution_failure"] <= a["execution_failure"],
        final_behavior_accuracy_not_decreased=b["final_behavior_accuracy"] >= a["final_behavior_accuracy"])
    return dict(criteria=criteria, eligible_for_adoption=all(criteria.values()),
                service_default_changed=False)
