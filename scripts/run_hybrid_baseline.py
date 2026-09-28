"""Execute and freeze the one-shot Phase 7 BM25/RRF comparison."""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.retrieval.hybrid_baseline import (
    PHASE7_VERSION,
    PRIMARY_METRICS,
    execute_phase7_once,
)


OUTPUT_DIR = PROJECT_ROOT / "reports/baselines/hybrid_baseline_v1"


def main() -> None:
    if OUTPUT_DIR.exists():
        raise FileExistsError(
            f"Immutable {PHASE7_VERSION} already exists; refusing to rerun or overwrite."
        )
    print("Phase 7 pre-registered configuration:")
    print("  BM25: k1=1.2, b=0.75, script-aware tokenizer, Top-10")
    print("  RRF: equal weights, k=60, Dense Top-10 + BM25 Top-10")
    print("  Dense source: frozen dense_baseline_v1 rankings (not rerun)")
    manifest, comparison = execute_phase7_once(
        project_root=PROJECT_ROOT,
        output_dir=OUTPUT_DIR,
    )
    summary = {
        method: {
            metric: comparison["metrics"][method]["macro"][metric]
            for metric in PRIMARY_METRICS
        }
        for method in ("dense", "bm25", "hybrid")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Frozen Phase 7 report: {OUTPUT_DIR / 'REPORT.md'}")
    print(f"Artifact manifest contains {len(manifest['artifacts'])} hashed files.")
    print("No post-result tuning was performed. Phase 8 was not started.")


if __name__ == "__main__":
    main()

