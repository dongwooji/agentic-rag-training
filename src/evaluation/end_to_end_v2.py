"""Versioned entry points for the preregistered E2E baseline v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.database.config import DatabaseConfig

from .end_to_end_runner import (
    PROJECT_ROOT,
    EndToEndBaselineProtocol,
    run_end_to_end_baseline,
    verify_protocol_only,
)


V2_BASELINE_VERSION = "end_to_end_baseline_v2"
V2_CONFIG_PATH = PROJECT_ROOT / "config/end_to_end_baseline_v2.json"
V2_PREREGISTRATION_DIR = (
    PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v2_preregistration"
)
V2_OUTPUT_DIR = PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v2"
V2_CHECKPOINT_DIR = (
    PROJECT_ROOT / "reports/baselines/.end_to_end_baseline_v2_checkpoint"
)
V2_PROTOCOL = EndToEndBaselineProtocol(
    baseline_version=V2_BASELINE_VERSION,
    config_path=V2_CONFIG_PATH,
    preregistration_manifest=V2_PREREGISTRATION_DIR / "manifest.json",
    preregistration_lock=V2_PREREGISTRATION_DIR / "manifest.sha256",
    output_dir=V2_OUTPUT_DIR,
    checkpoint_dir=V2_CHECKPOINT_DIR,
)


def verify_v2_only(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Verify the v2 freeze without constructing any external component."""

    if root.resolve() != PROJECT_ROOT.resolve():
        protocol = EndToEndBaselineProtocol(
            baseline_version=V2_BASELINE_VERSION,
            config_path=root / "config/end_to_end_baseline_v2.json",
            preregistration_manifest=(
                root
                / "reports/baselines/end_to_end_baseline_v2_preregistration/manifest.json"
            ),
            preregistration_lock=(
                root
                / "reports/baselines/end_to_end_baseline_v2_preregistration/manifest.sha256"
            ),
            output_dir=root / "reports/baselines/end_to_end_baseline_v2",
            checkpoint_dir=(
                root / "reports/baselines/.end_to_end_baseline_v2_checkpoint"
            ),
        )
    else:
        protocol = V2_PROTOCOL
    return verify_protocol_only(root, protocol=protocol)


def run_end_to_end_baseline_v2(
    *,
    database_config: DatabaseConfig,
    postgres_password: str,
    openai_api_key: str,
    model_cache: Path,
    output_dir: Path | None = None,
    checkpoint_dir: Path | None = None,
    min_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Run exactly one v2 evaluation under the frozen v2 protocol."""

    return run_end_to_end_baseline(
        database_config=database_config,
        postgres_password=postgres_password,
        openai_api_key=openai_api_key,
        model_cache=model_cache,
        output_dir=output_dir,
        checkpoint_dir=checkpoint_dir,
        min_interval_seconds=min_interval_seconds,
        protocol=V2_PROTOCOL,
    )
