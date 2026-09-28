"""Run and freeze the preregistered Phase 10 Agent planning baseline."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.baseline import run_agent_baseline
from src.agent.planner import OpenAIPlannerBackend


def main() -> None:
    output = PROJECT_ROOT / "reports/baselines/agent_baseline_v1"
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "OpenAI API key is read without echo and is passed only in "
            "process memory."
        )
        api_key = getpass.getpass("OpenAI API key: ")
    if not api_key.strip():
        raise SystemExit("A non-empty OpenAI API key is required.")
    backend = OpenAIPlannerBackend(api_key=api_key.strip())
    manifest, metrics, comparison = run_agent_baseline(
        PROJECT_ROOT, output_dir=output, backend=backend
    )
    aggregate = metrics["aggregate"]
    print(f"Frozen Agent baseline written to: {output}")
    print(
        "Exact Tool Set Match: "
        f"{aggregate['exact_tool_set_match_count']}/{aggregate['case_count']} "
        f"({aggregate['exact_tool_set_match_accuracy']:.6f})"
    )
    print(
        f"Unnecessary calls: {aggregate['unnecessary_tool_call_count']}; "
        f"missing required: {aggregate['missing_required_tool_count']}"
    )
    print(f"HYB-008 Agent correct: {comparison['hyb_008']['agent_correct']}")
    print(f"Frozen artifacts: {len(manifest['artifacts'])}")


if __name__ == "__main__":
    main()
