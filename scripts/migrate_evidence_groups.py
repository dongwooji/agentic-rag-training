"""One-time deterministic migration of the Phase 5 draft to claim groups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data/evaluation/eval_dataset_v1_draft.json"
DEFAULT_REVIEW = ROOT / "data/evaluation/human_review_v1.json"


def group(claim: str, match: str, chunks: list[str], *, required: bool = True) -> dict:
    return {
        "claim": claim,
        "required": required,
        "match": match,
        "chunk_ids": chunks,
    }


MAPPINGS = {
    "LIT-001": {
        "groups": [
            group("Volume-equated low- versus high-frequency strength gains were not significantly different.", "any", ["paper_pmc6081873_abstract_001_b783fdd6", "paper_pmc6081873_conclusions_001_f2473a22"]),
            group("Frequency effects must be separated from the effect of distributing or increasing weekly volume.", "any", ["paper_pmc6081873_discussion_considerations_towards_weekly_trainin_001_c448e257", "paper_pmc6081873_conclusions_001_f2473a22"]),
            group("The evidence base is limited and heterogeneous across populations.", "any", ["paper_pmc6081873_abstract_001_b783fdd6"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-002": {
        "groups": [
            group("Hypertrophy did not clearly differ across low, moderate, and high loads under the analyzed conditions.", "any", ["paper_pmc8126497_abstract_001_f200864c", "paper_pmc8126497_discussion_001_4d630baf"]),
            group("Strength gains were greater with moderate or high loads than with low loads.", "any", ["paper_pmc8126497_abstract_001_f200864c", "paper_pmc8126497_discussion_001_4d630baf"]),
            group("The included load comparison required sets performed to volitional failure.", "any", ["paper_pmc8126497_abstract_001_f200864c"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-003": {
        "groups": [
            group("The overall analysis found no significant failure versus non-failure difference for strength or hypertrophy.", "any", ["paper_pmc9068575_abstract_002_8c9ac68c", "paper_pmc9068575_conclusion_001_d4947343"]),
            group("Training to failure was not required for strength or muscle-size gains.", "any", ["paper_pmc9068575_abstract_002_8c9ac68c", "paper_pmc9068575_conclusion_001_d4947343"]),
            group("The evidence was concentrated in young adults and has limited generalizability.", "any", ["paper_pmc9068575_discussion_generalizability_of_the_results_001_70f61903", "paper_pmc9068575_abstract_002_8c9ac68c"]),
            group("Subgroup findings depend on volume equality and training status and should not replace the overall result.", "any", ["paper_pmc9068575_abstract_002_8c9ac68c", "paper_pmc9068575_conclusion_001_d4947343"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2], [3]],
    },
    "LIT-004": {
        "groups": [
            group("Momentary muscular failure was not superior to non-failure for hypertrophy.", "any", ["paper_pmc9935748_abstract_001_47d8e9e3", "paper_pmc9935748_conclusions_001_d77d57c2"]),
            group("Closer proximity or higher velocity loss did not always produce more hypertrophy, suggesting non-linearity.", "any", ["paper_pmc9935748_abstract_001_47d8e9e3", "paper_pmc9935748_conclusions_001_d77d57c2"]),
            group("Failure definitions and achieved proximity varied across studies.", "any", ["paper_pmc9935748_abstract_001_47d8e9e3", "paper_pmc9935748_conclusions_001_d77d57c2"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-005": {
        "groups": [
            group("The best dose-response model included time, average sets, and a nonlinear outcome-by-load interaction.", "any", ["paper_pmc11239729_abstract_001_804fa841"]),
            group("Load profiles differed for maximum strength, vertical jump, and power outcomes.", "any", ["paper_pmc11239729_abstract_001_804fa841", "paper_pmc11239729_conclusion_001_6c1a4564"]),
            group("A single load prescription should not be generalized across distinct outcomes.", "any", ["paper_pmc11239729_abstract_001_804fa841", "paper_pmc11239729_conclusion_001_6c1a4564"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-006": {
        "groups": [
            group("The meta-analysis favored autoregulation over fixed loading for maximum strength.", "any", ["paper_pmc7994759_abstract_001_c2446fa2", "paper_pmc7994759_conclusions_001_60ab1548"]),
            group("APRE, RPE-based, and velocity-based training are distinct autoregulation methods.", "any", ["paper_pmc7994759_abstract_001_c2446fa2"]),
            group("The evidence comprised eight studies, 166 participants, and interventions lasting five to ten weeks.", "any", ["paper_pmc7994759_abstract_001_c2446fa2"]),
            group("The small and short evidence base limits broad and long-term generalization.", "any", ["paper_pmc7994759_abstract_001_c2446fa2"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2], [3]],
    },
    "LIT-007": {
        "groups": [
            group("Deloading is a period of reduced training stress intended to mitigate fatigue, support recovery, and improve preparedness.", "any", ["paper_pmc10511399_abstract_001_81adf8c1"]),
            group("Deload design can manipulate volume, intensity or effort, and exercise selection, with consensus for reducing volume.", "any", ["paper_pmc10511399_results_general_perceptions_of_deloading_001_e3d0f432", "paper_pmc10511399_discussion_integrating_deloading_into_the_streng_001_834932bd"]),
            group("The source is a Delphi expert-consensus study rather than an experiment establishing an optimal design.", "any", ["paper_pmc10511399_abstract_001_81adf8c1"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-008": {
        "groups": [
            group("Older-adult trials showed preserved functional-capacity effects after four to thirty-six weeks of cessation.", "any", ["paper_pmc11705206_abstract_001_2277be05"]),
            group("Larger initial benefits predicted larger residual effects, while age negatively moderated persistence.", "any", ["paper_pmc11705206_abstract_001_2277be05"]),
            group("The athlete review reports that training cessation can negatively affect physical performance.", "any", ["paper_pmc7593778_conclusion_001_41081a04"]),
            group("Older-adult functional-capacity evidence does not directly represent young strength athletes or e1RM outcomes.", "any", ["paper_pmc11705206_abstract_001_2277be05"]),
            group("Cessation duration, population, setting, and outcome heterogeneity limit generalization.", "any", ["paper_pmc11705206_discussion_005_a2b7c803", "paper_pmc11705206_abstract_001_2277be05"]),
        ],
        "criteria": [[0], [1], [2]], "limitations": [[3], [4]],
    },
    "LIT-009": {
        "groups": [
            group("The reviewed studies did not compare periodized variation with varied non-periodized programs.", "any", ["paper_pmc6692867_abstract_001_b33f7355", "paper_pmc6692867_perspective_001_562852c2"]),
            group("Predicted adaptation timing was not tested and the evidence was limited mainly to strength training.", "any", ["paper_pmc6692867_abstract_001_b33f7355", "paper_pmc6692867_perspective_001_562852c2"]),
            group("Absence of proof should not be converted into proof that periodization is ineffective.", "any", ["paper_pmc6692867_perspective_001_562852c2"]),
        ],
        "criteria": [[0], [1]], "limitations": [[2]],
    },
    "LIT-010": {
        "groups": [
            group("Progressive overload is the gradual increase of stress placed on the body during training.", "any", ["paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6"]),
            group("Applying progressive overload depends on volume-load combinations, training status, goals, and individualization.", "all", ["paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6", "paper_pmc12965823_discussion_002_daf8292e"]),
            group("Progressive overload should not be reduced to a requirement for linear load increases every session.", "all", ["paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6", "paper_pmc12965823_discussion_002_daf8292e"]),
            group("The evidence does not establish one universal minimum intensity threshold.", "any", ["paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6"]),
        ],
        "criteria": [[0], [1], [2]], "limitations": [[3]],
    },
    "HYB-001": {
        "groups": [
            group("Resistance-training outcomes can be influenced by load, sets or volume, frequency, range of motion, and exercise order.", "any", ["paper_pmc12965823_abstract_001_8343007d"]),
            group("Volume has multiple operational definitions and needs explicit interpretation.", "any", ["paper_pmc9302196_discussion_sets_001_43510c9c"], required=False),
        ],
        "criteria": [[], [], [0, 1]], "limitations": [[], []],
        "criterion_structured": [[0], [1], []],
        "limitation_structured": [[0, 1], []],
        "limitation_contracts": [["observational_noncausality"], ["dataset_limitations_v1", "source_unit_unknown"]],
    },
    "HYB-002": {
        "groups": [
            group("Older-adult exercise effects can persist after training cessation.", "any", ["paper_pmc11705206_abstract_001_2277be05"]),
            group("Population, setting, and outcome differences constrain residual-effect generalization.", "any", ["paper_pmc11705206_discussion_005_a2b7c803"]),
            group("Training cessation can negatively affect athlete physical performance.", "any", ["paper_pmc7593778_conclusion_001_41081a04"]),
        ],
        "criteria": [[], [0, 2]], "limitations": [[0, 1, 2], []],
        "criterion_structured": [[0], []],
        "limitation_structured": [[0], [0]],
        "limitation_contracts": [["observational_noncausality"], []],
    },
    "HYB-003": {
        "groups": [group("When volume is equated, higher frequency does not show a significant independent strength advantage.", "any", ["paper_pmc6081873_abstract_001_b783fdd6", "paper_pmc6081873_discussion_considerations_towards_weekly_trainin_001_c448e257"])],
        "criteria": [[], [0]], "limitations": [[]],
        "criterion_structured": [[0], []], "limitation_structured": [[0]],
        "limitation_contracts": [["observational_noncausality"]],
    },
    "HYB-004": {
        "groups": [group("Failure training is not required for strength gains and the evidence has population limits.", "all", ["paper_pmc9068575_abstract_002_8c9ac68c", "paper_pmc9068575_discussion_generalizability_of_the_results_001_70f61903"])],
        "criteria": [[], [0]], "limitations": [[0]],
        "criterion_structured": [[0], []], "limitation_structured": [[]],
        "limitation_contracts": [["dataset_limitations_v1"]],
    },
    "HYB-005": {
        "groups": [group("A limited meta-analysis favored autoregulation over fixed loading for maximum strength.", "any", ["paper_pmc7994759_abstract_001_c2446fa2", "paper_pmc7994759_conclusions_001_60ab1548"])],
        "criteria": [[], [0]], "limitations": [[]],
        "criterion_structured": [[0], []], "limitation_structured": [[]],
        "limitation_contracts": [["dataset_limitations_v1"]],
    },
    "HYB-006": {
        "groups": [group("Momentary failure is not superior for hypertrophy and closer proximity does not always improve outcomes.", "any", ["paper_pmc9935748_abstract_001_47d8e9e3", "paper_pmc9935748_conclusions_001_d77d57c2"])],
        "criteria": [[], [0]], "limitations": [[0]],
        "criterion_structured": [[0], []], "limitation_structured": [[]],
        "limitation_contracts": [["dataset_limitations_v1", "metric_definition_v1"]],
    },
    "HYB-007": {
        "groups": [group("Progressive overload describes gradual stress progression rather than proof from a single isolated value.", "any", ["paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6", "paper_pmc13236796_abstract_001_1dff21c5"])],
        "criteria": [[], [0]], "limitations": [[]],
        "criterion_structured": [[0], []], "limitation_structured": [[0]],
        "limitation_contracts": [["source_unit_unknown"]],
    },
    "HYB-008": {
        "groups": [group("Existing periodization comparisons often fail to test varied non-periodized programs or predicted adaptation timing.", "any", ["paper_pmc6692867_abstract_001_b33f7355", "paper_pmc6692867_perspective_001_562852c2"])],
        "criteria": [[], [0]], "limitations": [[], []],
        "criterion_structured": [[0], []], "limitation_structured": [[], [0]],
        "limitation_contracts": [["dataset_limitations_v1"], ["metric_definition_v1"]],
    },
}


DEFAULT_SUPPORT = {
    "LOG-001": {"criteria": [[0], [0]], "limitations_contracts": [["session_semantics_v1"]]},
    "LOG-002": {"criteria": [[0], [0]]},
    "LOG-003": {"criteria": [[0]], "limitations_structured": [[0]]},
    "LOG-004": {"criteria": [[0], []], "criteria_contracts": [[], ["metric_definition_v1"]], "limitations_contracts": [["metric_definition_v1", "source_unit_unknown"]]},
    "LOG-005": {"criteria": [[0], [0]], "limitations_contracts": [["observational_noncausality"]]},
    "LOG-006": {"criteria": [[0], [0]], "limitations_structured": [[0]]},
    "UNA-001": {"criteria_contracts": [["dataset_limitations_v1"], ["dataset_limitations_v1"]]},
    "UNA-002": {"criteria_contracts": [["dataset_limitations_v1"]]},
    "UNA-003": {"criteria_contracts": [["dataset_limitations_v1"]]},
    "UNA-004": {"criteria_contracts": [["dataset_limitations_v1"]]},
    "UNA-005": {"criteria_contracts": [["dataset_limitations_v1"]]},
    "UNA-006": {"criteria_contracts": [["dataset_limitations_v1"], ["dataset_limitations_v1"]]},
}


def mapped_item(item_id: str, text: str, group_ids: list[str], structured_ids: list[str], contracts: list[str]) -> dict:
    return {
        "id": item_id,
        "text": text,
        "literature_evidence_group_ids": group_ids,
        "structured_evidence_ids": structured_ids,
        "supporting_contracts": contracts,
    }


def migrate(dataset: dict) -> dict:
    dataset["protocol"]["multiple_gold_policy"] = {
        "within_group": "match=any accepts any listed alternative; match=all requires every listed chunk",
        "across_groups": "all groups with required=true must be covered",
        "optional_groups": "required=false groups are review context and excluded from primary retrieval metrics",
    }
    dataset["protocol"]["retrieval_metric_policy"] = {
        "evidence_group_recall_at_k": "covered required literature groups divided by all required literature groups",
        "complete_evidence_at_k": "1 only when every required literature group is covered, otherwise 0",
        "chunk_recall_at_k": "unique retrieved chunks from required groups divided by all unique chunks in required groups",
        "reciprocal_rank": "reciprocal rank of the first chunk belonging to any required literature group",
        "aggregation": "macro average over the 18 literature-bearing cases",
        "default_k": [5, 10],
    }
    for case in dataset["cases"]:
        case_id = case["id"]
        gold = case["gold"]
        if gold.get("answer_criteria") and isinstance(gold["answer_criteria"][0], dict):
            raise RuntimeError("Dataset already appears migrated.")

        structured = gold.get("structured_evidence", [])
        for index, evidence in enumerate(structured, start=1):
            evidence["id"] = f"SE-{case_id}-{index:02d}"

        mapping = MAPPINGS.get(case_id)
        group_ids: list[str] = []
        if mapping:
            groups = []
            for index, definition in enumerate(mapping["groups"], start=1):
                definition = dict(definition)
                definition["id"] = f"EG-{case_id}-{index:02d}"
                groups.append(definition)
                group_ids.append(definition["id"])
            gold["literature_evidence_groups"] = groups
            gold["required_group_semantics"] = "all_required"
        else:
            gold["literature_evidence_groups"] = []
        gold.pop("literature_chunk_ids", None)

        default = DEFAULT_SUPPORT.get(case_id, {})
        criteria_group_indexes = mapping.get("criteria", []) if mapping else []
        limitation_group_indexes = mapping.get("limitations", []) if mapping else []
        criteria_structured = (mapping or {}).get("criterion_structured", default.get("criteria", []))
        limitation_structured = (mapping or {}).get("limitation_structured", default.get("limitations_structured", []))
        criteria_contracts = (mapping or {}).get("criterion_contracts", default.get("criteria_contracts", []))
        limitation_contracts = (mapping or {}).get("limitation_contracts", default.get("limitations_contracts", []))

        def at(values: list, index: int) -> list:
            return values[index] if index < len(values) else []

        old_criteria = gold["answer_criteria"]
        gold["answer_criteria"] = [
            mapped_item(
                f"AC-{case_id}-{index + 1:02d}",
                text,
                [group_ids[item] for item in at(criteria_group_indexes, index)],
                [structured[item]["id"] for item in at(criteria_structured, index)],
                at(criteria_contracts, index),
            )
            for index, text in enumerate(old_criteria)
        ]
        old_limitations = gold.get("limitations", [])
        gold["limitations"] = [
            mapped_item(
                f"LM-{case_id}-{index + 1:02d}",
                text,
                [group_ids[item] for item in at(limitation_group_indexes, index)],
                [structured[item]["id"] for item in at(limitation_structured, index)],
                at(limitation_contracts, index),
            )
            for index, text in enumerate(old_limitations)
        ]
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    migrated = migrate(dataset)
    args.dataset.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review = json.loads(args.review.read_text(encoding="utf-8"))
    import hashlib
    review["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    review["status"] = "pending"
    review["reviewer"] = None
    review["reviewed_at"] = None
    for item in review["cases"]:
        item.update({"status": "pending", "question_and_category_correct": None, "evidence_correct": None, "limitations_sufficient": None, "notes": None})
    args.review.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": len(migrated["cases"]), "literature_case_count": len(MAPPINGS), "dataset_sha256": review["dataset_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
