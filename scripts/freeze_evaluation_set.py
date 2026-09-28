from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.validation import sha256_file, validate_dataset


REVIEW_CHECKS = (
    "question_and_category_correct",
    "evidence_correct",
    "limitations_sufficient",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze eval_dataset_v1 only after explicit item-level human review."
    )
    parser.add_argument(
        "--draft",
        type=Path,
        default=Path("data/evaluation/eval_dataset_v1_draft.json"),
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("data/evaluation/human_review_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/eval_dataset_v1.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/evaluation/eval_dataset_v1.manifest.json"),
    )
    parser.add_argument(
        "--confirm-human-review",
        action="store_true",
        help="Required acknowledgement that the review record reflects a real item-level review.",
    )
    return parser.parse_args()


def _require_review(review: dict, draft: dict, draft_hash: str) -> None:
    if review.get("status") != "approved":
        raise RuntimeError("Human review status is not approved.")
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise RuntimeError("Human reviewer and reviewed_at are required.")
    if review.get("dataset_version") != draft.get("dataset_version"):
        raise RuntimeError("Review dataset version does not match the draft.")
    if review.get("dataset_sha256") != draft_hash:
        raise RuntimeError("Review was not performed against the current draft hash.")
    draft_ids = {case["id"] for case in draft["cases"]}
    reviews = review.get("cases", [])
    if {item.get("id") for item in reviews} != draft_ids:
        raise RuntimeError("Review case IDs do not exactly match the draft.")
    for item in reviews:
        if item.get("status") != "approved" or not all(
            item.get(check) is True for check in REVIEW_CHECKS
        ):
            raise RuntimeError(f"Case {item.get('id')} does not have complete approval.")


def main() -> None:
    args = parse_args()
    if not args.confirm_human_review:
        raise SystemExit("Refusing to freeze without --confirm-human-review.")
    draft_path = (PROJECT_ROOT / args.draft).resolve()
    review_path = (PROJECT_ROOT / args.review).resolve()
    output_path = (PROJECT_ROOT / args.output).resolve()
    manifest_path = (PROJECT_ROOT / args.manifest).resolve()
    if output_path.exists() or manifest_path.exists():
        raise RuntimeError(
            "Frozen v1 artifacts already exist. Do not overwrite; create a new version."
        )

    automated = validate_dataset(draft_path, PROJECT_ROOT)
    if not automated["all_automated_checks_passed"]:
        raise RuntimeError("Draft automated validation failed.")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    draft_hash = sha256_file(draft_path)
    _require_review(review, draft, draft_hash)

    frozen = copy.deepcopy(draft)
    frozen["status"] = "frozen"
    frozen["frozen_at"] = review["reviewed_at"]
    frozen["human_review"] = {
        "review_version": review["review_version"],
        "reviewer": review["reviewer"],
        "reviewed_at": review["reviewed_at"],
        "review_sha256": sha256_file(review_path),
    }
    for case in frozen["cases"]:
        case["review"]["human_status"] = "approved"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    distribution = Counter(case["category"] for case in frozen["cases"])
    manifest = {
        "dataset_version": frozen["dataset_version"],
        "status": "frozen",
        "frozen_at": frozen["frozen_at"],
        "dataset_path": output_path.relative_to(PROJECT_ROOT).as_posix(),
        "dataset_sha256": sha256_file(output_path),
        "draft_sha256": draft_hash,
        "review_path": review_path.relative_to(PROJECT_ROOT).as_posix(),
        "review_sha256": sha256_file(review_path),
        "case_count": len(frozen["cases"]),
        "category_counts": dict(distribution),
        "source_artifacts": frozen["source_artifacts"],
        "mutation_policy": "Never overwrite v1; change version and record rationale.",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

