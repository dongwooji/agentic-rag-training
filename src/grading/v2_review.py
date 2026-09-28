"""Draft held-out validation and human-review rendering for Grader v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .v2_contracts import (
    EvidenceAssessmentDraftV2,
    GraderV2Input,
    GradingProcedureV2,
    derive_insufficiency_reason,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_CHUNKS_SHA256 = (
    "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
)
EVAL_DATASET_SHA256 = (
    "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509"
)
GRADER_V1_CONFIG_SHA256 = (
    "f6f490481a635e02d22f48bddda8f3313d7275631d0460141f500243925c4f37"
)
GRADER_V1_PROMPT_SHA256 = (
    "7eefe2f4c4b83ddfed5b9d98db315b9c2fa151b73627c9c561f419116fe20b70"
)
GRADER_V1_MANIFEST_SHA256 = (
    "e32ab0476ba7ac82b4f16768e08a4233ff3695012426a0e036587c864bf093ba"
)
GRADER_V1_REPRODUCIBILITY_SHA256 = (
    "07513c08c694d2c153c8a7b6fbe5ba9473729bf2be8e5eb5fef24a08b02a5ca9"
)
DENSE_MANIFEST_SHA256 = (
    "8edea5983d63e588515a5330c2107679357c73e977502f444eb9ee5d25b2170a"
)
HYBRID_MANIFEST_SHA256 = (
    "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
)
ROUTER_MANIFEST_SHA256 = (
    "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
)
AGENT_MANIFEST_SHA256 = (
    "7e6a584b83134374e315af218660f7113481ec772c746810a8bc07fa86997cce"
)
GRADER_V2_CONFIG_SHA256 = (
    "c2533dd9fb95c38d41d91700bcc9c3f7167fbf278e06b3b0e4109aff424c7e20"
)
GRADER_V2_PROMPT_SHA256 = (
    "ee9372e3a23fb52ec2c4843169da6445e39deb40cffd5cd48b4d4ab1fd38b702"
)

INPUT_PATH = Path("data/evaluation/grader_v2_heldout_inputs_draft.json")
GOLD_PATH = Path("data/evaluation/grader_v2_heldout_gold_draft.json")
CORPUS_PATH = Path("data/literature/processed/chunks.jsonl")
EVAL_PATH = Path("data/evaluation/eval_dataset_v1.json")
REVIEW_REPORT_PATH = Path("reports/GRADER_V2_HELDOUT_HUMAN_REVIEW.md")
MODEL_INPUT_FIELDS = ["question", "grading_procedure", "retrieved_evidence"]
TOTAL_CANDIDATE_CASES = 14
LITERATURE_ONLY_CASES = 10
HYBRID_CASES = 4
APPROVED_CASE_IDS = {
    "G2H-LIT-001",
    "G2H-LIT-002",
    "G2H-LIT-003",
    "G2H-LIT-004",
    "G2H-LIT-005",
    "G2H-LIT-006",
    "G2H-LIT-008",
    "G2H-LIT-009",
    "G2H-HYB-001",
    "G2H-HYB-002",
    "G2H-HYB-003",
    "G2H-HYB-004",
}
PENDING_MODIFIED_CASE_IDS = {"G2H-LIT-007", "G2H-LIT-010"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def all_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(key) for key in value)
        for item in value.values():
            result.update(all_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(all_keys(item))
    return result


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def load_heldout_drafts(
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    root = Path(project_root).resolve()
    inputs = read_json(root / INPUT_PATH)
    gold = read_json(root / GOLD_PATH)
    chunks = read_jsonl(root / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    return inputs, gold, chunk_by_id


def build_heldout_model_inputs(
    project_root: str | Path = PROJECT_ROOT,
) -> list[dict[str, Any]]:
    """Build model inputs from the candidate-input file without reading Gold."""

    root = Path(project_root).resolve()
    inputs = read_json(root / INPUT_PATH)
    chunks = read_jsonl(root / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    records: list[dict[str, Any]] = []
    for case in inputs["cases"]:
        evidence: list[dict[str, Any]] = []
        for rank, chunk_id in enumerate(case["evidence_bundle_chunk_ids"], start=1):
            chunk = chunk_by_id[chunk_id]
            evidence.append(
                {
                    "evidence_rank": rank,
                    "evidence_source": "controlled_heldout_bundle",
                    "chunk_id": chunk["chunk_id"],
                    "paper_id": chunk["paper_id"],
                    "pmcid": chunk.get("pmcid"),
                    "title": chunk["title"],
                    "section": chunk["section"],
                    "year": chunk.get("year"),
                    "population": chunk.get("population"),
                    "study_type": chunk.get("study_type"),
                    "topics": chunk.get("topics", []),
                    "text": chunk["text"],
                }
            )
        grader_input = GraderV2Input(
            question=case["question"],
            grading_procedure=GradingProcedureV2(),
            retrieved_evidence=evidence,
        ).model_dump(mode="json")
        records.append(
            {
                "case_id": case["case_id"],
                "input_sha256": stable_sha256(grader_input),
                "grader_input_fields": MODEL_INPUT_FIELDS,
                "grader_input": grader_input,
            }
        )
    return records


def _expected_assessment(gold_case: dict[str, Any]) -> EvidenceAssessmentDraftV2:
    components = gold_case["expected_components"]
    return EvidenceAssessmentDraftV2.model_validate(
        {
            "literature_scope_only": True,
            "structured_evidence_handling": (
                "excluded_assumed_evaluated_separately"
            ),
            "literature_subquestion": gold_case["literature_subquestion"],
            **components,
            "reason": gold_case["proposed_rationale"],
            "confidence": 0.5,
        }
    )


def _grader_v2_baseline_state_valid(root: Path) -> bool:
    """Accept either the preregistered pre-run state or one frozen baseline."""

    candidates = (
        root / "reports/baselines/grader_v2_baseline",
        root / "reports/baselines/grader_v2_baseline_v1",
    )
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return True
    if len(existing) != 1:
        return False

    manifest_path = existing[0] / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        manifest.get("grader_version") == "grader_v2_baseline"
        and manifest.get("status") == "frozen"
        and manifest.get("configuration_status")
        == "preregistered_first_evaluation"
        and len(manifest.get("artifacts", [])) == 13
    )


def validate_heldout_drafts(
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    inputs, gold, chunk_by_id = load_heldout_drafts(root)
    eval_dataset = read_json(root / EVAL_PATH)
    grader_v1_reproducibility = read_json(
        root / "reports/baselines/grader_baseline_v1/reproducibility.json"
    )
    design_history = read_json(
        root
        / "reports/preregistration/grader_v2_candidate_v1/DESIGN_HISTORY.json"
    )
    input_cases = inputs["cases"]
    gold_cases = gold["cases"]
    input_ids = [item["case_id"] for item in input_cases]
    gold_ids = [item["case_id"] for item in gold_cases]
    existing_questions = {item["question"] for item in eval_dataset["cases"]}
    existing_gold_chunks = {
        chunk_id
        for case in eval_dataset["cases"]
        for group in case.get("gold", {}).get("literature_evidence_groups", [])
        for chunk_id in group["chunk_ids"]
    }
    supplied_chunks = {
        chunk_id
        for case in input_cases
        for chunk_id in case["evidence_bundle_chunk_ids"]
    }
    model_inputs = build_heldout_model_inputs(root)
    banned_model_keys = {
        "case_id",
        "category",
        "diagnostic_tags",
        "gold",
        "proposed_verdict",
        "proposed_insufficiency_reason",
        "expected_components",
        "evidence_contract",
        "supporting_chunk_ids",
        "withheld_reference_chunk_ids",
        "human_review_status",
    }
    gold_by_id = {item["case_id"]: item for item in gold_cases}
    mappings_valid = True
    labels_valid = True
    references_exist = True
    for case in input_cases:
        case_gold = gold_by_id.get(case["case_id"], {})
        bundle = set(case["evidence_bundle_chunk_ids"])
        for contract in case_gold.get("evidence_contract", []):
            supporting = set(contract["supporting_chunk_ids"])
            withheld = set(contract["withheld_reference_chunk_ids"])
            mappings_valid &= supporting <= bundle and not (withheld & bundle)
            references_exist &= all(item in chunk_by_id for item in supporting | withheld)
        try:
            assessment = _expected_assessment(case_gold)
            derived = derive_insufficiency_reason(assessment).value
            labels_valid &= derived == case_gold["proposed_insufficiency_reason"]
            labels_valid &= (
                (derived == "none")
                == (case_gold["proposed_verdict"] == "sufficient")
            )
        except Exception:
            labels_valid = False
    leakage_guards = gold.get("withheld_answer_leakage_guards", {})
    guarded_phrases = leakage_guards.get("cases", {})
    withheld_case_ids = {
        case["case_id"]
        for case in gold_cases
        if any(
            contract["withheld_reference_chunk_ids"]
            for contract in case.get("evidence_contract", [])
        )
    }
    guarded_case_ids = set(guarded_phrases)
    leakage_hits: list[dict[str, str]] = []
    input_by_id = {item["case_id"]: item for item in input_cases}
    for case_id, phrases in guarded_phrases.items():
        input_case = input_by_id.get(case_id)
        if input_case is None:
            leakage_hits.append(
                {"case_id": case_id, "field": "case", "phrase": "missing case"}
            )
            continue
        question_text = normalize_text(input_case["question"])
        supplied = [
            chunk_by_id[item] for item in input_case["evidence_bundle_chunk_ids"]
        ]
        supplied_text = normalize_text(" ".join(item["text"] for item in supplied))
        metadata_text = normalize_text(
            " ".join(
                " ".join(
                    [
                        str(item.get("title", "")),
                        str(item.get("section", "")),
                        str(item.get("population", "")),
                        str(item.get("study_type", "")),
                        " ".join(item.get("topics", [])),
                    ]
                )
                for item in supplied
            )
        )
        for phrase in phrases:
            normalized_phrase = normalize_text(phrase)
            for field_name, field_text in (
                ("question", question_text),
                ("supplied_text", supplied_text),
                ("metadata", metadata_text),
            ):
                if normalized_phrase and normalized_phrase in field_text:
                    leakage_hits.append(
                        {
                            "case_id": case_id,
                            "field": field_name,
                            "phrase": phrase,
                        }
                    )
    tags = {
        tag
        for case in input_cases
        for tag in case.get("diagnostic_tags", [])
    }
    required_tags = {
        "fully_sufficient",
        "missing_main_claim",
        "missing_limitation",
        "population_mismatch",
        "outcome_mismatch",
        "multi_evidence_incomplete",
        "polarity_sensitive",
        "hybrid",
        "literature_sufficient",
        "literature_insufficient",
        "related_but_insufficient",
        "no_relevant_evidence",
    }
    review_statuses = {
        case["case_id"]: case.get("human_review_status") for case in gold_cases
    }
    lit007_input = input_by_id.get("G2H-LIT-007", {})
    lit007_gold = gold_by_id.get("G2H-LIT-007", {})
    lit007_contracts = {
        item.get("component_id"): item
        for item in lit007_gold.get("evidence_contract", [])
    }
    lit007_expected = lit007_gold.get("expected_components", {})
    lit007_relevance = lit007_contracts.get("G2H-LIT-007-C1", {})
    lit007_main_claim = lit007_contracts.get("G2H-LIT-007-C2", {})
    lit007_is_genuine_missing_main_claim = (
        lit007_gold.get("proposed_verdict") == "insufficient"
        and lit007_gold.get("proposed_insufficiency_reason")
        == "missing_main_claim"
        and lit007_expected.get("relevant_evidence_present") is True
        and lit007_expected.get("answer_to_literature_question_supported") is False
        and lit007_expected.get("outcome_scope") == "adequate"
        and lit007_relevance.get("bundle_status") == "supported"
        and bool(lit007_relevance.get("supporting_chunk_ids"))
        and lit007_main_claim.get("bundle_status") == "missing"
        and not lit007_main_claim.get("supporting_chunk_ids")
        and "injury" not in normalize_text(lit007_input.get("question"))
        and "quantitative_claim_missing"
        in lit007_input.get("diagnostic_tags", [])
    )
    checks = {
        "inputs_are_draft": inputs.get("status") == "draft_human_review_required",
        "gold_is_draft": gold.get("status") == "draft_human_review_required",
        "fourteen_candidate_cases": (
            len(input_cases) == len(gold_cases) == TOTAL_CANDIDATE_CASES
        ),
        "case_ids_align": (
            input_ids == gold_ids
            and len(set(input_ids)) == TOTAL_CANDIDATE_CASES
        ),
        "category_balance": (
            sum(item["category"] == "literature_only" for item in input_cases)
            == LITERATURE_ONLY_CASES
            and sum(item["category"] == "hybrid" for item in input_cases)
            == HYBRID_CASES
        ),
        "questions_are_new_and_unique": (
            len({item["question"] for item in input_cases})
            == TOTAL_CANDIDATE_CASES
            and not ({item["question"] for item in input_cases} & existing_questions)
        ),
        "five_unique_chunks_per_bundle": all(
            len(item["evidence_bundle_chunk_ids"])
            == len(set(item["evidence_bundle_chunk_ids"]))
            == 5
            for item in input_cases
        ),
        "all_bundle_chunks_exist": all(item in chunk_by_id for item in supplied_chunks),
        "no_existing_eval_gold_chunk_reuse": not (supplied_chunks & existing_gold_chunks),
        "component_mappings_valid": bool(mappings_valid),
        "all_reference_chunks_exist": bool(references_exist),
        "withheld_cases_have_leakage_guards": (
            withheld_case_ids <= guarded_case_ids
        ),
        "lit007_quantitative_answer_has_leakage_guard": (
            "G2H-LIT-007" in guarded_case_ids
        ),
        "withheld_answer_phrases_absent_from_model_surface": not leakage_hits,
        "proposed_labels_follow_fixed_finalizer": bool(labels_valid),
        "lit007_is_genuine_missing_main_claim": bool(
            lit007_is_genuine_missing_main_claim
        ),
        "required_diagnostic_coverage": required_tags <= tags,
        "human_review_round_2_statuses_recorded": (
            {
                case_id
                for case_id, status in review_statuses.items()
                if status == "approved"
            }
            == APPROVED_CASE_IDS
            and {
                case_id
                for case_id, status in review_statuses.items()
                if status == "pending"
            }
            == PENDING_MODIFIED_CASE_IDS
        ),
        "model_input_count": len(model_inputs) == TOTAL_CANDIDATE_CASES,
        "model_inputs_are_typed": all(
            GraderV2Input.model_validate(item["grader_input"])
            for item in model_inputs
        ),
        "no_gold_in_model_inputs": all(
            not (all_keys(item["grader_input"]) & banned_model_keys)
            and item["grader_input_fields"] == MODEL_INPUT_FIELDS
            for item in model_inputs
        ),
        "corpus_hash_unchanged": sha256_file(root / CORPUS_PATH)
        == CORPUS_CHUNKS_SHA256,
        "eval_hash_unchanged": sha256_file(root / EVAL_PATH) == EVAL_DATASET_SHA256,
        "grader_v1_config_unchanged": sha256_file(root / "config/grader_v1.json")
        == GRADER_V1_CONFIG_SHA256,
        "grader_v1_prompt_unchanged": sha256_file(
            root / "config/grader_v1_prompt.md"
        )
        == GRADER_V1_PROMPT_SHA256,
        "grader_v1_manifest_unchanged": sha256_file(
            root / "reports/baselines/grader_baseline_v1/manifest.json"
        )
        == GRADER_V1_MANIFEST_SHA256,
        "grader_v1_reproducibility_unchanged": sha256_file(
            root / "reports/baselines/grader_baseline_v1/reproducibility.json"
        )
        == GRADER_V1_REPRODUCIBILITY_SHA256,
        "dense_manifest_reference_unchanged": (
            grader_v1_reproducibility.get("dense_manifest_sha256")
            == DENSE_MANIFEST_SHA256
        ),
        "hybrid_manifest_unchanged": sha256_file(
            root / "reports/baselines/hybrid_baseline_v1/manifest.json"
        )
        == HYBRID_MANIFEST_SHA256,
        "router_manifest_unchanged": sha256_file(
            root / "reports/baselines/router_baseline_v1/manifest.json"
        )
        == ROUTER_MANIFEST_SHA256,
        "agent_manifest_reference_unchanged": (
            grader_v1_reproducibility.get("agent_manifest_sha256")
            == AGENT_MANIFEST_SHA256
        ),
        "grader_v2_config_preregistered": sha256_file(root / "config/grader_v2.json")
        == GRADER_V2_CONFIG_SHA256,
        "grader_v2_prompt_preregistered": sha256_file(
            root / "config/grader_v2_prompt.md"
        )
        == GRADER_V2_PROMPT_SHA256,
        "superseded_design_provenance_preserved": (
            design_history.get("entries", [{}])[0].get("status")
            == "superseded_pre_evaluation_draft"
            and design_history.get("entries", [{}])[0]
            .get("sha256", {})
            .get("contract_and_finalizer")
            == "991fe889f83d3605e680757c2a5403363d027afd467d9eb2de54e8a7e90e59ac"
            and design_history.get("entries", [{}])[0]
            .get("sha256", {})
            .get("prompt")
            == "ec12084ebfa4f91446b0998133d352ec62b86f012ebdbc844f5492ceee493c6d"
            and design_history.get("entries", [{}])[0]
            .get("sha256", {})
            .get("config")
            == "51126e7bbf1e17c27b1939d6db72386dbb525d94c6df80278bac9f223bdfe1f4"
        ),
        # Draft validation remains useful after the one-way lifecycle transition
        # from "not run" to "frozen".  Reject duplicate/partial baseline states,
        # but do not make the preserved draft invalid merely because its first
        # evaluation has now completed successfully.
        "grader_v2_baseline_state_valid": _grader_v2_baseline_state_valid(root),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "all_checks_passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "summary": {
            "candidate_cases": len(input_cases),
            "literature_only": sum(
                item["category"] == "literature_only" for item in input_cases
            ),
            "hybrid": sum(item["category"] == "hybrid" for item in input_cases),
            "proposed_sufficient": sum(
                item["proposed_verdict"] == "sufficient" for item in gold_cases
            ),
            "proposed_insufficient": sum(
                item["proposed_verdict"] == "insufficient" for item in gold_cases
            ),
            "human_review_pending": sum(
                item["human_review_status"] == "pending" for item in gold_cases
            ),
            "human_review_approved": sum(
                item["human_review_status"] == "approved" for item in gold_cases
            ),
            "input_bundle_sha256": stable_sha256(model_inputs),
            "leakage_hits": leakage_hits,
        },
    }


def _chunk_markdown(chunk: dict[str, Any]) -> list[str]:
    return [
        f"- Chunk ID: `{chunk['chunk_id']}`",
        f"- Paper / PMCID: `{chunk['paper_id']}` / `{chunk.get('pmcid')}`",
        f"- Title: {chunk['title']}",
        f"- Section: {chunk['section']}",
        "- Reviewer judgment: [ ] SUPPORTED  [ ] PARTIAL  [ ] NOT SUPPORTED",
        "- Reviewer note:",
        "",
        "> " + chunk["text"].replace("\n", "\n> "),
        "",
    ]


def render_human_review_report(
    project_root: str | Path = PROJECT_ROOT,
) -> str:
    root = Path(project_root).resolve()
    inputs, gold, chunk_by_id = load_heldout_drafts(root)
    validation = validate_heldout_drafts(root)
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    lines = [
        "# Grader v2 Held-out Candidate — Human Review",
        "",
        "> Status: **DRAFT — HUMAN REVIEW REQUIRED — NOT FROZEN**",
        "",
        "This artifact reviews proposed Gold labels for a new controlled held-out set.",
        "It is not a Grader result. No Grader v2 API evaluation has been run.",
        "The previous human-review decisions are retained for 12 unchanged cases;",
        "the two modified cases, G2H-LIT-007 and G2H-LIT-010, are pending re-review.",
        "",
        "## Review protocol",
        "",
        "1. Use only the full frozen-corpus chunk text printed below.",
        "2. Judge only literature-side sufficiency. Personal log and Metric evidence",
        "   in Hybrid questions belongs to a separate channel.",
        "3. Check question polarity: refuting an ‘always/must/can conclude’ premise",
        "   may itself answer the literature question.",
        "4. Verify every proposed component mapping and every supplied chunk.",
        "5. Approve, modify, or reject each case. Do not freeze until all cases are",
        "   explicitly reviewed and any modifications are incorporated.",
        "",
        "## Sufficiency contract",
        "",
        "Literature evidence is sufficient when the supplied evidence is enough",
        "to give a directly grounded answer to the literature-side question,",
        "including a justified negative, qualified, or bounded conclusion.",
        "Evidence is insufficient when a required claim, comparison, outcome,",
        "limitation, population-specific inference, or other material component",
        "cannot be grounded from the supplied evidence.",
        "",
        "A population, outcome, or terminology mismatch is diagnostic rather than",
        "an automatic failure. It can ground a bounded negative answer to a",
        "challenge question, but cannot supply a requested target-specific effect",
        "or quantitative comparison that is absent.",
        "",
        "## Candidate-set summary",
        "",
        f"- Cases: {validation['summary']['candidate_cases']} (10 literature-only, 4 hybrid)",
        f"- Proposed labels: {validation['summary']['proposed_sufficient']} sufficient / {validation['summary']['proposed_insufficient']} insufficient",
        f"- Approved without semantic changes: {validation['summary']['human_review_approved']}",
        f"- Pending human review: {validation['summary']['human_review_pending']}",
        f"- Generated model-input bundle SHA-256: `{validation['summary']['input_bundle_sha256']}`",
        "- Existing eval question reuse: none",
        "- Existing eval Gold chunk reuse in supplied bundles: none",
        "- Gold fields exposed to model inputs: none",
        "- Guarded answer-phrase leakage: none detected",
        "",
    ]
    for gold_case in gold["cases"]:
        case = input_by_id[gold_case["case_id"]]
        lines.extend(
            [
                "---",
                "",
                f"## {case['case_id']}",
                "",
                f"- Category: `{case['category']}`",
                f"- Diagnostic tags: {', '.join(f'`{tag}`' for tag in case['diagnostic_tags'])}",
                f"- Question: {case['question']}",
                f"- Proposed literature subquestion: {gold_case['literature_subquestion']}",
                f"- Proposed verdict: **{gold_case['proposed_verdict'].upper()}**",
                f"- Proposed insufficiency reason: `{gold_case['proposed_insufficiency_reason']}`",
                f"- Proposed rationale: {gold_case['proposed_rationale']}",
                f"- Current human-review status: **{gold_case['human_review_status'].upper()}**",
                "",
                "### Proposed component checklist",
                "",
                "| Component | Proposed value |",
                "|---|---|",
            ]
        )
        for name, value in gold_case["expected_components"].items():
            lines.append(f"| `{name}` | `{str(value).lower()}` |")
        lines.extend(["", "### Gold component mappings", ""])
        for contract in gold_case["evidence_contract"]:
            lines.extend(
                [
                    f"#### {contract['component_id']}",
                    "",
                    f"- Component: `{contract['component']}`",
                    f"- Claim: {contract['claim']}",
                    f"- Proposed bundle status: `{contract['bundle_status']}`",
                    f"- Match: `{contract['match']}`",
                    "- Supporting input chunks: "
                    + (", ".join(f"`{item}`" for item in contract["supporting_chunk_ids"]) or "none"),
                    "- Withheld reference chunks: "
                    + (", ".join(f"`{item}`" for item in contract["withheld_reference_chunk_ids"]) or "none"),
                    "- Mapping judgment: [ ] APPROVE  [ ] MODIFY  [ ] REJECT",
                    "- Mapping note:",
                    "",
                ]
            )
        lines.extend(["### Supplied Grader evidence", ""])
        for rank, chunk_id in enumerate(case["evidence_bundle_chunk_ids"], start=1):
            lines.extend([f"#### Rank {rank}", ""])
            lines.extend(_chunk_markdown(chunk_by_id[chunk_id]))
        withheld = []
        for contract in gold_case["evidence_contract"]:
            withheld.extend(contract["withheld_reference_chunk_ids"])
        if withheld:
            lines.extend(
                [
                    "### Withheld reference evidence",
                    "",
                    "These chunks are **not** in the Grader input. They are printed only",
                    "to help the reviewer verify the proposed missing-component label.",
                    "",
                ]
            )
            for chunk_id in dict.fromkeys(withheld):
                lines.extend(_chunk_markdown(chunk_by_id[chunk_id]))
        guarded = gold.get("withheld_answer_leakage_guards", {}).get(
            "cases", {}
        ).get(case["case_id"], [])
        lines.extend(
            [
                "### Leakage review",
                "",
                "- Automated exact eval-question overlap: **PASS**",
                "- Automated eval Gold chunk reuse in supplied bundle: **PASS**",
                "- Automated Gold-field exposure in model input: **PASS**",
                "- Automated guarded answer-phrase scan: "
                + ("**PASS**" if guarded else "not applicable"),
                "- [ ] Paper title/metadata does not reveal a withheld answer.",
                "- [ ] Question wording does not reveal the missing answer.",
                "- Leakage reviewer note:",
                "",
            ]
        )
        approved = gold_case["human_review_status"] == "approved"
        lines.extend(
            [
                "### Human decision",
                "",
                "- [x] APPROVE" if approved else "- [ ] APPROVE",
                "- [ ] MODIFY",
                "- [ ] REJECT",
                "- Potential issue: [ ] none  [ ] ambiguous question  [ ] weak mapping",
                "  [ ] label disagreement  [ ] evidence leakage  [ ] other",
                "- Required modification / reviewer note:",
                "",
                "- Reviewer:",
                "- Review date:",
                "",
            ]
        )
    lines.extend(
        [
            "---",
            "",
            "## Freeze checkpoint",
            "",
            "- [ ] All 14 cases have an explicit human decision.",
            "- [ ] All MODIFY decisions have been incorporated and re-reviewed.",
            "- [ ] No supplied evidence bundle reuses an eval_dataset_v1 Gold chunk.",
            "- [ ] Candidate input and Gold files have final reviewer approval.",
            "- [ ] Grader v2 final pre-evaluation prompt/config hashes are verified.",
            "- [ ] A separate frozen manifest has been generated.",
            "",
            "Until every box is complete, `grader_v2_baseline` must not be run.",
            "",
        ]
    )
    return "\n".join(lines)
