"""Run and freeze the preregistered Phase 9 deterministic Router baseline."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.routing.baseline import run_router_baseline


def main() -> None:
    output = PROJECT_ROOT / "reports/baselines/router_baseline_v1"
    manifest, metrics = run_router_baseline(PROJECT_ROOT, output_dir=output)
    aggregate = metrics["aggregate"]
    print(f"Frozen Router baseline written to: {output}")
    print(
        "Exact Tool Set Match: "
        f"{aggregate['exact_tool_set_match_count']}/{aggregate['case_count']} "
        f"({aggregate['exact_tool_set_match_accuracy']:.6f})"
    )
    print(
        f"Unnecessary calls: {aggregate['unnecessary_tool_call_count']}; "
        f"missing required: {aggregate['missing_required_tool_count']}"
    )
    print(f"Frozen artifacts: {len(manifest['artifacts'])}")


if __name__ == "__main__":
    main()
