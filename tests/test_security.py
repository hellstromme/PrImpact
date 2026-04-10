"""Unit tests for pr_impact/security.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pr_impact.security import (
    _added_lines,
    _is_infra_file,
    _is_typosquat,
    _levenshtein,
    _osv_check,
    _parse_new_packages,
    check_dependency_integrity,
    detect_pattern_signals,
)
from tests.helpers import make_file


# ---------------------------------------------------------------------------
# _levenshtein
# ---------------------------------------------------------------------------


def test_levenshtein_identical():
    assert _levenshtein("requests", "requests") == 0


def test_levenshtein_one_insertion():
    assert _levenshtein("reqests", "requests") == 1


def test_levenshtein_one_substitution():
    assert _levenshtein("requasts", "requests") == 1


def test_levenshtein_empty_strings():
    assert _levenshtein("", "") == 0


def test_levenshtein_one_empty():
    assert _levenshtein("abc", "") == 3


def test_levenshtein_symmetric():
    assert _levenshtein("abc", "xyz") == _levenshtein("xyz", "abc")


# ---------------------------------------------------------------------------
# _is_typosquat
# ---------------------------------------------------------------------------


def test_typosquat_exact_match_not_flagged():
    assert _is_typosquat("requests", "pypi") is None


def test_typosquat_close_name_flagged():
    similar = _is_typosquat("requets", "pypi")
    assert similar == "requests"


def test_typosquat_two_edits_flagged():
    similar = _is_typosquat("requasts", "pypi")
    assert similar == "requests"


def test_typosquat_three_edits_not_flagged():
    # "reqxxxts" is 3 substitutions from "requests" (u→x, e→x, s→x)
    result = _is_typosquat("reqxxxts", "pypi")
    assert result is None


def test_typosquat_npm_ecosystem():
    similar = _is_typosquat("lodsh", "npm")
    assert similar == "lodash"


def test_typosquat_unknown_ecosystem_returns_none():
    assert _is_typosquat("anything", "unknown_ecosystem") is None


# ---------------------------------------------------------------------------
# _is_infra_file
# ---------------------------------------------------------------------------


def test_infra_file_build_dir():
    assert _is_infra_file("build/compile.py") is True


def test_infra_file_scripts_dir():
    assert _is_infra_file("scripts/deploy.sh") is True


def test_infra_file_regular_module():
    assert _is_infra_file("src/auth/session.py") is False


def test_infra_file_tools_dir():
    assert _is_infra_file("tools/generate.py") is True


# ---------------------------------------------------------------------------
# _added_lines
# ---------------------------------------------------------------------------


def test_added_lines_basic():
    diff = "@@ -1,3 +1,4 @@\n context\n+added line\n context2\n"
    lines = _added_lines(diff)
    assert any("added line" in text for _, text in lines)


def test_added_lines_excludes_header():
    diff = "@@ -1 +1 @@\n+++ b/file.py\n+real addition\n"
    lines = _added_lines(diff)
    assert all("++" not in text for _, text in lines)


def test_added_lines_ignores_removed():
    diff = "@@ -1 +1 @@\n-removed line\n+added line\n"
    lines = _added_lines(diff)
    assert all("removed" not in text for _, text in lines)


def test_added_lines_empty_diff():
    assert _added_lines("") == []


# ---------------------------------------------------------------------------
# detect_pattern_signals — network calls
# ---------------------------------------------------------------------------


def _make_diff(added: str) -> str:
    return f"@@ -1,1 +1,2 @@\n context\n+{added}\n"


def test_detects_requests_get():
    f = make_file("src/payment.py", diff=_make_diff("response = requests.get('http://api.example.com')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_hardcoded_ip():
    f = make_file("src/auth.py", diff=_make_diff('url = "http://203.0.113.42/endpoint"'))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_eval():
    f = make_file("src/handler.py", diff=_make_diff("result = eval(user_input)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "dynamic_exec" for s in signals)


def test_detects_subprocess_shell_true():
    f = make_file("src/api.py", diff=_make_diff("subprocess.run(cmd, shell=True)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "dynamic_exec" for s in signals)


def test_detects_os_system():
    f = make_file("src/auth.py", diff=_make_diff('os.system("rm -rf /tmp/cache")'))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "shell_invoke" for s in signals)


def test_detects_base64_decode():
    f = make_file("src/utils.py", diff=_make_diff("data = base64.b64decode(encoded)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "encoded_payload" for s in signals)


def test_detects_credential_pattern():
    f = make_file("src/config.py", diff=_make_diff('api_key = "sk-abc123xyz456def789"'))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "credential" for s in signals)


# ---------------------------------------------------------------------------
# detect_pattern_signals — severity rules
# ---------------------------------------------------------------------------


def test_pattern_existing_in_before_downgrades_to_low():
    """Same pattern already in file before → severity LOW."""
    f = make_file(
        "src/api.py",
        diff=_make_diff("subprocess.run(['ls'])"),
        before="import subprocess\nsubprocess.run(['make'])\n",
    )
    signals = detect_pattern_signals([f])
    shell_signals = [s for s in signals if s.signal_type == "shell_invoke"]
    assert shell_signals
    assert all(s.severity == "low" for s in shell_signals)


def test_infra_file_shell_invoke_downgraded():
    """Shell invoke in a scripts/ dir is downgraded."""
    f = make_file("scripts/build.py", diff=_make_diff("subprocess.run(['make', 'all'])"))
    signals = detect_pattern_signals([f])
    shell_signals = [s for s in signals if s.signal_type == "shell_invoke"]
    assert shell_signals
    # medium → low (infra downgrade)
    assert all(s.severity in ("low", "medium") for s in shell_signals)
    # Must not be high
    assert all(s.severity != "high" for s in shell_signals)


def test_non_infra_file_shell_invoke_not_high():
    """subprocess.run baseline severity is medium (not high) for non-infra."""
    f = make_file("src/payment.py", diff=_make_diff("subprocess.run(['ls'])"))
    signals = detect_pattern_signals([f])
    shell_signals = [s for s in signals if s.signal_type == "shell_invoke"]
    assert shell_signals
    # The baseline for subprocess.run is medium (not high)
    assert any(s.severity == "medium" for s in shell_signals)


def test_empty_diff_no_signals():
    f = make_file("src/module.py", diff="")
    signals = detect_pattern_signals([f])
    assert signals == []


def test_exception_in_file_returns_empty():
    """detect_pattern_signals must not raise — returns [] on broken input."""
    bad_file = MagicMock()
    bad_file.diff = None  # will cause iteration to fail
    signals = detect_pattern_signals([bad_file])
    assert signals == []


# ---------------------------------------------------------------------------
# _parse_new_packages
# ---------------------------------------------------------------------------


def test_parse_requirements_txt():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+malicious-pkg==1.0.0\n"
    pkgs = _parse_new_packages(diff, "pypi")
    assert "malicious-pkg" in pkgs


def test_parse_package_json_dependency():
    diff = '@@ -1 +1,2 @@\n "lodash": "^4.17.21",\n+"evil-pkg": "1.0.0",\n'
    pkgs = _parse_new_packages(diff, "npm")
    assert "evil-pkg" in pkgs


def test_parse_gemfile():
    diff = "@@ -1 +1,2 @@\n gem 'rails'\n+gem 'sus-gem'\n"
    pkgs = _parse_new_packages(diff, "rubygems")
    assert "sus-gem" in pkgs


def test_parse_empty_diff():
    assert _parse_new_packages("", "pypi") == []


def test_parse_unknown_ecosystem():
    assert _parse_new_packages("+whatever\n", "unknown") == []


# ---------------------------------------------------------------------------
# _osv_check
# ---------------------------------------------------------------------------


def test_osv_check_returns_vuln_ids(monkeypatch):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"vulns": [{"id": "GHSA-1234"}]}).encode()
    mock_response.__enter__ = lambda self: self
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pr_impact.security.urllib.request.urlopen", return_value=mock_response):
        result = _osv_check("requests", "pypi")
    assert "GHSA-1234" in result


def test_osv_check_returns_empty_on_no_vulns(monkeypatch):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({}).encode()
    mock_response.__enter__ = lambda self: self
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pr_impact.security.urllib.request.urlopen", return_value=mock_response):
        result = _osv_check("safe-package", "pypi")
    assert result == []


def test_osv_check_returns_empty_on_network_error():
    with patch("pr_impact.security.urllib.request.urlopen", side_effect=OSError("timeout")):
        result = _osv_check("any-package", "pypi")
    assert result == []


def test_osv_check_unknown_ecosystem():
    result = _osv_check("pkg", "unknown_ecosystem")
    assert result == []


# ---------------------------------------------------------------------------
# check_dependency_integrity — integration
# ---------------------------------------------------------------------------


def test_check_typosquat_detected():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+requets==1.0.0\n"
    f = make_file("requirements.txt", diff=diff)
    issues = check_dependency_integrity([f])
    assert any(i.issue_type == "typosquat" and "requets" in i.package_name for i in issues)
    assert any(i.severity == "high" for i in issues)


def test_check_non_manifest_file_ignored():
    f = make_file("src/main.py", diff="+requests.get('http://example.com')\n")
    issues = check_dependency_integrity([f])
    assert issues == []


def test_check_version_change_detected():
    diff = "@@ -1 +1 @@\n-requests==2.27.0\n+requests==2.28.1\n"
    f = make_file("requirements.txt", diff=diff)
    with patch("pr_impact.security._osv_check", return_value=[]):
        issues = check_dependency_integrity([f])
    version_issues = [i for i in issues if i.issue_type == "version_change"]
    assert version_issues
    assert version_issues[0].package_name == "requests"


def test_check_exception_returns_empty():
    """check_dependency_integrity must not raise on bad input."""
    bad_file = MagicMock()
    bad_file.path = "requirements.txt"
    bad_file.diff = None  # will cause iteration error
    issues = check_dependency_integrity([bad_file])
    assert issues == []


def test_check_requirements_txt_variant():
    """requirements-dev.txt should be treated as pypi ecosystem."""
    diff = "@@ -1 +1,2 @@\n pytest==7.0.0\n+requets==1.0.0\n"
    f = make_file("requirements-dev.txt", diff=diff)
    issues = check_dependency_integrity([f])
    assert any(i.issue_type == "typosquat" for i in issues)
