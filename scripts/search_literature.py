"""Search PubMed using the versioned Phase 4 protocol and save candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.literature.pubmed import discover_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "literature_corpus_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "literature" / "manifests" / "candidates_v1.json",
    )
    parser.add_argument("--retmax", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = discover_candidates(config, retmax=args.retmax)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pmc_count = sum(
        bool(candidate["pmcid"]) for candidate in result["candidates"]
    )
    print(
        f"Saved {len(result['candidates'])} unique candidates "
        f"({pmc_count} with PMC full text) to {args.output}"
    )


if __name__ == "__main__":
    main()
