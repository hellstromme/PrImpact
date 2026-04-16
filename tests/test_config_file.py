"""Tests for pr_impact/config_file.py and the _apply_suppressions helper in analyzer.py."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml not installed; skipping config_file tests")

from pr_impact.config_file import _as_str_list, _parse_config, load_config_file
from pr_impact.models import PrImpactConfig, SourceLocation, SuppressedSignal


# ---------------------------------------------------------------------------
# load_config_file — file-level tests
# ---------------------------------------------------------------------------


def test_load_config_file_returns_none_when_absent(tmp_path):
    """No .primpact.yml → returns None without raising."""
    result = load_config_file(str(tmp_path))
    assert result is None


def test_load_config_file_parses_valid_yaml(tmp_path):
    """A valid .primpact.yml is parsed into a populated PrImpactConfig."""
    config_path = tmp_path / ".primpact.yml"
    config_path.write_text(
        """
high_sensitivity_modules:
  - src/auth/
  - src/payments/

suppressed_signals:
  - signal_type: shell_invoke
    path_prefix: tools/
    reason: "Build tools"

blast_radius_depth:
  src/utils/: 2

fail_on_severity: high

anomaly_thresholds:
  credential: medium
""",
        encoding="utf-8",
    )

    result = load_config_file(str(tmp_path))
    assert result is not None
    assert isinstance(result, PrImpactConfig)
    assert result.high_sensitivity_modules == ["src/auth/", "src/payments/"]
    assert len(result.suppressed_signals) == 1
    assert result.suppressed_signals[0].signal_type == "shell_invoke"
    assert result.suppressed_signals[0].path_prefix == "tools/"
    assert result.suppressed_signals[0].reason == "Build tools"
    assert result.blast_radius_depth == {"src/utils/": 2}
    assert result.fail_on_severity == "high"
    assert result.anomaly_thresholds == {"credential": "medium"}


def test_load_config_file_returns_none_on_invalid_yaml(tmp_path, capsys):
    """Invalid YAML returns None and writes a warning to stderr."""
    config_path = tmp_path / ".primpact.yml"
    config_path.write_text("key: [unclosed", encoding="utf-8")

    result = load_config_file(str(tmp_path))
    assert result is None
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_load_config_file_returns_none_on_non_mapping_yaml(tmp_path, capsys):
    """A YAML file that is not a mapping (e.g. a list) returns None with warning."""
    config_path = tmp_path / ".primpact.yml"
    config_path.write_text("- item1\n- item2\n", encoding="utf-8")

    result = load_config_file(str(tmp_path))
    assert result is None
    captured = capsys.readouterr()
    assert "Warning" in captured.err


# ---------------------------------------------------------------------------
# _parse_config — field-level tests
# ---------------------------------------------------------------------------


def test_parse_config_high_sensitivity_modules():
    raw = {"high_sensitivity_modules": ["src/auth/", "src/payments/"]}
    result = _parse_config(raw)
    assert result.high_sensitivity_modules == ["src/auth/", "src/payments/"]


def test_parse_config_suppressed_signals():
    raw = {
        "suppressed_signals": [
            {"signal_type": "shell_invoke", "path_prefix": "tools/", "reason": "build"},
            {"signal_type": "credential", "path_prefix": "scripts/"},
        ]
    }
    result = _parse_config(raw)
    assert len(result.suppressed_signals) == 2
    assert result.suppressed_signals[0] == SuppressedSignal("shell_invoke", "tools/", "build")
    assert result.suppressed_signals[1] == SuppressedSignal("credential", "scripts/", "")


def test_parse_config_suppressed_signals_skips_incomplete_entries():
    """Entries missing signal_type or path_prefix are silently skipped."""
    raw = {
        "suppressed_signals": [
            {"signal_type": "shell_invoke"},  # missing path_prefix
            {"path_prefix": "tools/"},        # missing signal_type
            "not_a_dict",
        ]
    }
    result = _parse_config(raw)
    assert result.suppressed_signals == []


def test_parse_config_blast_radius_depth():
    raw = {"blast_radius_depth": {"src/utils/": 2, "src/core/": 1}}
    result = _parse_config(raw)
    assert result.blast_radius_depth == {"src/utils/": 2, "src/core/": 1}


def test_parse_config_blast_radius_depth_invalid_value_skipped():
    raw = {"blast_radius_depth": {"src/utils/": "not_an_int"}}
    result = _parse_config(raw)
    assert result.blast_radius_depth == {}


def test_parse_config_fail_on_severity_valid():
    for value in ("none", "low", "medium", "high"):
        result = _parse_config({"fail_on_severity": value})
        # "none" maps to None (falsy cleared), others remain as-is
        if value == "none":
            assert result.fail_on_severity is None
        else:
            assert result.fail_on_severity == value


def test_parse_config_fail_on_severity_invalid_value_becomes_none():
    result = _parse_config({"fail_on_severity": "critical"})
    assert result.fail_on_severity is None


def test_parse_config_anomaly_thresholds():
    raw = {"anomaly_thresholds": {"shell_invoke": "high", "credential": "medium"}}
    result = _parse_config(raw)
    assert result.anomaly_thresholds == {"shell_invoke": "high", "credential": "medium"}


def test_parse_config_ignores_unknown_keys():
    """Unknown keys do not cause errors — forward-compatibility."""
    raw = {
        "high_sensitivity_modules": ["src/auth/"],
        "unknown_future_key": "some_value",
        "another_unknown": {"nested": True},
    }
    result = _parse_config(raw)
    assert result.high_sensitivity_modules == ["src/auth/"]


def test_parse_config_empty_dict_gives_defaults():
    result = _parse_config({})
    assert result.high_sensitivity_modules == []
    assert result.suppressed_signals == []
    assert result.blast_radius_depth == {}
    assert result.fail_on_severity is None
    assert result.anomaly_thresholds == {}


# ---------------------------------------------------------------------------
# _apply_suppressions (in analyzer.py)
# ---------------------------------------------------------------------------


def test_apply_suppressions_removes_matching_signal():
    """A signal matching both signal_type and path_prefix is removed."""
    from pr_impact.analyzer import _apply_suppressions
    from pr_impact.models import SecuritySignal, SourceLocation

    signal = SecuritySignal(
        description="shell call",
        location=SourceLocation(file="tools/build.py", line=10),
        signal_type="shell_invoke",
        severity="high",
        why_unusual="unexpected",
        suggested_action="review",
    )
    suppressions = [SuppressedSignal(signal_type="shell_invoke", path_prefix="tools/", reason="ok")]

    result = _apply_suppressions([signal], suppressions)
    assert result == []


def test_apply_suppressions_keeps_non_matching_signal_wrong_type():
    """Signal with a different signal_type is kept even if path matches."""
    from pr_impact.analyzer import _apply_suppressions
    from pr_impact.models import SecuritySignal, SourceLocation

    signal = SecuritySignal(
        description="credential found",
        location=SourceLocation(file="tools/build.py", line=5),
        signal_type="credential",
        severity="high",
        why_unusual="found secret",
        suggested_action="rotate",
    )
    suppressions = [SuppressedSignal(signal_type="shell_invoke", path_prefix="tools/", reason="ok")]

    result = _apply_suppressions([signal], suppressions)
    assert result == [signal]


def test_apply_suppressions_keeps_non_matching_signal_wrong_path():
    """Signal in a different path is kept even if signal_type matches."""
    from pr_impact.analyzer import _apply_suppressions
    from pr_impact.models import SecuritySignal, SourceLocation

    signal = SecuritySignal(
        description="shell call",
        location=SourceLocation(file="src/main.py", line=20),
        signal_type="shell_invoke",
        severity="medium",
        why_unusual="unexpected",
        suggested_action="review",
    )
    suppressions = [SuppressedSignal(signal_type="shell_invoke", path_prefix="tools/", reason="ok")]

    result = _apply_suppressions([signal], suppressions)
    assert result == [signal]


def test_apply_suppressions_empty_inputs():
    from pr_impact.analyzer import _apply_suppressions
    assert _apply_suppressions([], []) == []


def test_apply_suppressions_mixed():
    """Mixed list: one suppressed, one kept."""
    from pr_impact.analyzer import _apply_suppressions
    from pr_impact.models import SecuritySignal, SourceLocation

    suppressed_sig = SecuritySignal(
        description="shell call",
        location=SourceLocation(file="tools/build.py", line=10),
        signal_type="shell_invoke",
        severity="high",
        why_unusual="unexpected",
        suggested_action="review",
    )
    kept_sig = SecuritySignal(
        description="credential found",
        location=SourceLocation(file="src/auth.py", line=5),
        signal_type="credential",
        severity="high",
        why_unusual="found secret",
        suggested_action="rotate",
    )
    suppressions = [SuppressedSignal(signal_type="shell_invoke", path_prefix="tools/", reason="ok")]

    result = _apply_suppressions([suppressed_sig, kept_sig], suppressions)
    assert result == [kept_sig]
