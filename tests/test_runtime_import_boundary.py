"""Runtime ↔ historical experiment import boundary.

The FastAPI entrypoint must not load historical experiment modules except the
explicitly documented couplings below (see docs/REPOSITORY_MAP.md). Any change
to that set must update this test and the map together.
"""

import ast
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

# Historical experiment modules kept in place for reproducibility.
HISTORICAL_EXPERIMENT_MODULES = frozenset(
    {
        # Router vs LLM Planner
        "src.agent.baseline",
        "src.agent.graph",
        "src.agent.planner",
        "src.routing.baseline",
        "src.evaluation.routing_metrics",
        # Retrieval v1 baselines
        "src.retrieval.baseline",
        "src.retrieval.hybrid_baseline",
        "src.evaluation.retrieval_metrics",
        # Grader v1 / v2 / v2.1
        "src.grading.baseline",
        "src.grading.contracts",
        "src.grading.evaluation",
        "src.grading.provider",
        "src.grading.provider_v2",
        "src.grading.provider_v21",
        "src.grading.v2_baseline",
        "src.grading.v2_contracts",
        "src.grading.v2_freeze",
        "src.grading.v2_review",
        "src.grading.v21_baseline",
        "src.grading.v21_contracts",
        "src.grading.v21_freeze",
        "src.grading.v21_review",
        # E2E v1 / v2 and smoke harness
        "src.evaluation.end_to_end_metrics",
        "src.evaluation.end_to_end_runner",
        "src.evaluation.end_to_end_v2",
        "src.evaluation.end_to_end_v2_metrics",
        "src.smoke.e2e",
        # Frozen evaluation-set construction
        "src.evaluation.reference",
        "src.evaluation.report",
        "src.evaluation.validation",
    }
)

# Historical modules the runtime still loads, with the import path that pulls
# them in. Removing a coupling means deleting its entry here and in the map.
KNOWN_RUNTIME_COUPLINGS = {
    "src.agent.planner": "src/agent/__init__.py (loaded with agent.contracts/agent.executor)",
    "src.agent.graph": "src/agent/__init__.py (loaded with agent.contracts/agent.executor)",
    "src.grading.contracts": "src/grading/__init__.py (loaded with grading.runtime_*)",
    "src.grading.v2_contracts": "src/grading/__init__.py (loaded with grading.runtime_*)",
    "src.retrieval.baseline": "src/retrieval/__init__.py, src/retrieval/hybrid.py",
    "src.evaluation.retrieval_metrics": "imported by src/retrieval/baseline.py",
}

# Runtime source files allowed to import historical modules directly.
KNOWN_DIRECT_IMPORTS = {
    "src/agent/__init__.py": {"src.agent.graph", "src.agent.planner"},
    "src/grading/__init__.py": {"src.grading.contracts", "src.grading.v2_contracts"},
    "src/retrieval/__init__.py": {"src.retrieval.baseline"},
    "src/retrieval/hybrid.py": {"src.retrieval.baseline"},
}


def _runtime_loaded_modules() -> set[str]:
    script = (
        "import json, sys\n"
        "import src.api.app\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'src' or m.startswith('src.'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_known_couplings_are_declared_historical_modules():
    assert set(KNOWN_RUNTIME_COUPLINGS) <= HISTORICAL_EXPERIMENT_MODULES


def test_runtime_entrypoint_loads_only_documented_historical_modules():
    loaded = _runtime_loaded_modules()

    assert "src.api.app" in loaded
    assert loaded & HISTORICAL_EXPERIMENT_MODULES == set(KNOWN_RUNTIME_COUPLINGS)


def _module_path(module: str) -> Path:
    path = ROOT.joinpath(*module.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def _direct_imports(path: Path, module: str) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)
    return imported


def test_runtime_sources_import_historical_modules_only_where_documented():
    runtime_modules = _runtime_loaded_modules() - HISTORICAL_EXPERIMENT_MODULES
    found: dict[str, set[str]] = {}
    for module in sorted(runtime_modules):
        path = _module_path(module)
        historical = _direct_imports(path, module) & HISTORICAL_EXPERIMENT_MODULES
        if historical:
            found[path.relative_to(ROOT).as_posix()] = historical

    assert found == KNOWN_DIRECT_IMPORTS
