"""Prepare no evaluation content: run frozen questions OR score frozen raw offline."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.evaluation.acceptance_v2_runner import run, score_saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["run", "score"])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    function = run if args.stage == "run" else score_saved
    function(args.manifest, args.manifest_sha256, args.output)


if __name__ == "__main__":
    main()
