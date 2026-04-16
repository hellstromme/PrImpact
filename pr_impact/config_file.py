"""Loader for .primpact.yml team configuration files.

Reads an optional .primpact.yml from the repo root and returns a PrImpactConfig.
Returns None (not an error) when the file is absent. Parse failures are logged to
stderr as warnings and return None so they never block the pipeline.

Called by cli.py only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .models import PrImpactConfig, SuppressedSignal


def load_config_file(repo_path: str) -> PrImpactConfig | None:
    """Read .primpact.yml from *repo_path* root and return a PrImpactConfig.

    Returns None if the file is absent or cannot be parsed.
    Warnings are written to stderr; they never raise.
    """
    config_path = Path(repo_path) / ".primpact.yml"
    if not config_path.exists():
        return None

    try:
        import yaml  # PyYAML — optional dep; only needed when config file present
    except ImportError:
        print(
            "Warning: .primpact.yml found but PyYAML is not installed. "
            "Install it with: pip install pyyaml",
            file=sys.stderr,
        )
        return None

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: Could not parse .primpact.yml: {exc}", file=sys.stderr)
        return None

    if not isinstance(raw, dict):
        print("Warning: .primpact.yml must be a YAML mapping; ignoring.", file=sys.stderr)
        return None

    return _parse_config(raw)


def _parse_config(raw: dict) -> PrImpactConfig:
    """Convert a raw YAML dict into a PrImpactConfig, ignoring unknown keys."""
    high_sensitivity_modules = _as_str_list(raw.get("high_sensitivity_modules", []))

    suppressed_signals: list[SuppressedSignal] = []
    for item in raw.get("suppressed_signals", []):
        if not isinstance(item, dict):
            continue
        signal_type = str(item.get("signal_type", ""))
        path_prefix = str(item.get("path_prefix", ""))
        reason = str(item.get("reason", ""))
        if signal_type and path_prefix:
            suppressed_signals.append(SuppressedSignal(signal_type, path_prefix, reason))

    blast_radius_depth: dict[str, int] = {}
    for prefix, depth in (raw.get("blast_radius_depth") or {}).items():
        try:
            blast_radius_depth[str(prefix)] = int(depth)
        except (TypeError, ValueError):
            pass

    fail_on_severity = raw.get("fail_on_severity")
    if fail_on_severity not in (None, "none", "low", "medium", "high"):
        fail_on_severity = None
    # "none" is equivalent to not set — normalise to None so callers can check truthiness
    if fail_on_severity == "none":
        fail_on_severity = None

    anomaly_thresholds: dict[str, str] = {}
    for k, v in (raw.get("anomaly_thresholds") or {}).items():
        anomaly_thresholds[str(k)] = str(v)

    return PrImpactConfig(
        high_sensitivity_modules=high_sensitivity_modules,
        suppressed_signals=suppressed_signals,
        blast_radius_depth=blast_radius_depth,
        fail_on_severity=fail_on_severity,
        anomaly_thresholds=anomaly_thresholds,
    )


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []
