"""Offline only: verify supplied frozen files, then score saved observations once."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluation.phase_a_semantics import BASELINE_SHA, compare, score


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verified_files(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = load_json(manifest_path)
    if manifest["baseline_sha"] != BASELINE_SHA:
        raise ValueError("baseline_sha_mismatch")
    files = {}
    for name in ("questions", "gold", "before", "after"):
        entry = manifest["files"][name]
        path = manifest_path.parent / entry["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("hash_mismatch_" + name)
        files[name] = path
    return manifest, files


def evaluate(manifest_path):
    manifest, files = verified_files(manifest_path)
    questions = [json.loads(line) for line in files["questions"].read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = load_json(files["gold"])
    if [(x["case_id"], x["pair_id"]) for x in questions] != [(x["case_id"], x["pair_id"]) for x in gold["cases"]]:
        raise ValueError("questions_gold_order_mismatch")
    saved = {name: load_json(files[name]) for name in ("before", "after")}
    for name, data in saved.items():
        expected = BASELINE_SHA if name == "before" else manifest["changed_sha"]
        if data["commit_sha"] != expected:
            raise ValueError("observation_commit_mismatch")
        if data["run_count_per_case"] != 1:
            raise ValueError("single_run_required")
        # Record unknown usage as null, never as zero. No provider/REST calls.
        for key in ("execution_order", "model_config_hashes", "stage_llm_calls", "usage", "latency_ms"):
            if key not in data:
                raise ValueError("missing_run_metadata_" + key)
    before = score(gold, saved["before"]["observations"])
    after = score(gold, saved["after"]["observations"])
    return dict(version="phase_a_semantics_comparison_v1", baseline_sha=BASELINE_SHA,
        changed_sha=manifest["changed_sha"], frozen_manifest=manifest,
        before=before, after=after, decision=compare(before, after),
        run_metadata={name: {k: v for k, v in data.items() if k != "observations"} for name, data in saved.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = evaluate(args.manifest)
    with Path(args.output).open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
