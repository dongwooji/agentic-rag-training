"""Runtime ↔ evaluation / data-preparation import boundary.

Historical experiment code was removed from the current tree and is preserved in
the ``legacy-pre-retrieval-v2`` Git tag. The FastAPI entrypoint must load only
runtime modules: evaluation infrastructure and data-preparation code stay
outside the request path (see docs/REPOSITORY_MAP.md).
"""

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

# Packages/modules that belong to evaluation or offline data preparation.
NON_RUNTIME_PREFIXES = (
    "src.evaluation",
    "src.smoke",
    "src.literature",
    "src.database.bootstrap",
    "src.database.loader",
    "src.database.psql",
)


def _is_non_runtime(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in NON_RUNTIME_PREFIXES
    )


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


def test_runtime_entrypoint_loads_no_evaluation_or_data_preparation_modules():
    loaded = _runtime_loaded_modules()

    assert "src.api.app" in loaded
    assert sorted(module for module in loaded if _is_non_runtime(module)) == []


def _module_path(module: str) -> Path:
    path = ROOT.joinpath(*module.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def _imports_from_source(source: str, module: str, *, is_package: bool) -> set[str]:
    """Return every module name an import statement may reference, at any depth.

    ``from X import Y`` yields both ``X`` and ``X.Y`` because ``Y`` may be a
    submodule (``from src import evaluation`` loads ``src.evaluation``).
    Relative imports are resolved against ``module``.
    """

    package = module if is_package else module.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            imported.add(base)
            imported.update(
                f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
            )
    return imported


def _non_runtime_imports(source: str, module: str, *, is_package: bool) -> list[str]:
    return sorted(
        name
        for name in _imports_from_source(source, module, is_package=is_package)
        if _is_non_runtime(name)
    )


def test_runtime_sources_do_not_import_non_runtime_modules():
    # Includes imports inside functions, which the load check above cannot see.
    found: dict[str, list[str]] = {}
    for module in sorted(_runtime_loaded_modules()):
        path = _module_path(module)
        offending = _non_runtime_imports(
            path.read_text(encoding="utf-8"),
            module,
            is_package=path.name == "__init__.py",
        )
        if offending:
            found[path.relative_to(ROOT).as_posix()] = offending

    assert found == {}


@pytest.mark.parametrize(
    ("source", "module", "is_package", "expected"),
    [
        # Regression: the submodule named in ``from src import evaluation`` must be seen.
        ("def run():\n    from src import evaluation\n", "src.graph.nodes", False, ["src.evaluation"]),
        ("from src import evaluation\n", "src.graph.nodes", False, ["src.evaluation"]),
        ("from src.database import loader\n", "src.tools.training_log", False, ["src.database.loader"]),
        (
            "def run():\n    from src.database import psql, config\n",
            "src.api.app",
            False,
            ["src.database.psql"],
        ),
        (
            "import src.evaluation.retrieval_metrics\n",
            "src.graph.nodes",
            False,
            ["src.evaluation.retrieval_metrics"],
        ),
        (
            "from src.evaluation import retrieval_metrics\n",
            "src.graph.nodes",
            False,
            ["src.evaluation", "src.evaluation.retrieval_metrics"],
        ),
        (
            "def run():\n    from src.smoke.e2e import run_end_to_end_smoke\n",
            "src.api.app",
            False,
            ["src.smoke.e2e", "src.smoke.e2e.run_end_to_end_smoke"],
        ),
        ("from .. import evaluation\n", "src.graph.nodes", False, ["src.evaluation"]),
        ("from . import literature\n", "src", True, ["src.literature"]),
        # Runtime-only imports stay allowed.
        ("from src import tools\nfrom src.database import config\n", "src.graph.nodes", False, []),
        ("from .contracts import RouterInput\n", "src.routing.runtime", False, []),
    ],
)
def test_import_analysis_resolves_every_import_form(source, module, is_package, expected):
    assert _non_runtime_imports(source, module, is_package=is_package) == expected
