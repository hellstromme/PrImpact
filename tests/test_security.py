"""Unit tests for pr_impact/security.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pr_impact.security import (
    _added_lines,
    _detect_version_changes,
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
    issues = check_dependency_integrity([f])
    version_issues = [i for i in issues if i.issue_type == "version_change"]
    assert version_issues
    assert version_issues[0].package_name == "requests"


def test_check_osv_not_called_by_default():
    """OSV check must not fire unless osv_check=True."""
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+newpkg==1.0.0\n"
    f = make_file("requirements.txt", diff=diff)
    with patch("pr_impact.security._osv_check") as mock_osv:
        check_dependency_integrity([f], osv_check=False)
    mock_osv.assert_not_called()


def test_check_osv_called_when_flag_set():
    """OSV check fires for new packages when osv_check=True."""
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+newpkg==1.0.0\n"
    f = make_file("requirements.txt", diff=diff)
    with patch("pr_impact.security._osv_check", return_value=[]) as mock_osv:
        check_dependency_integrity([f], osv_check=True)
    mock_osv.assert_called()


def test_check_exception_per_file_skipped():
    """A bad file is skipped; others still processed."""
    bad_file = MagicMock()
    bad_file.path = "requirements.txt"
    bad_file.diff = None  # will cause iteration error
    good = make_file("requirements.txt", diff="@@ -1 +1,2 @@\n requests==2.28.0\n+requets==1.0.0\n")
    issues = check_dependency_integrity([bad_file, good])
    # good file still processed → typosquat detected
    assert any(i.issue_type == "typosquat" for i in issues)


def test_check_requirements_txt_variant():
    """requirements-dev.txt should be treated as pypi ecosystem."""
    diff = "@@ -1 +1,2 @@\n pytest==7.0.0\n+requets==1.0.0\n"
    f = make_file("requirements-dev.txt", diff=diff)
    issues = check_dependency_integrity([f])
    assert any(i.issue_type == "typosquat" for i in issues)


def test_check_vulnerability_detected_when_osv_returns_results():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+newpkg==1.0.0\n"
    f = make_file("requirements.txt", diff=diff)
    with patch("pr_impact.security._osv_check", return_value=["GHSA-1234"]):
        issues = check_dependency_integrity([f], osv_check=True)
    vuln_issues = [i for i in issues if i.issue_type == "vulnerability"]
    assert vuln_issues
    assert "GHSA-1234" in vuln_issues[0].description
    assert vuln_issues[0].severity == "high"


def test_check_version_change_has_low_severity():
    diff = "@@ -1 +1 @@\n-requests==2.27.0\n+requests==2.28.1\n"
    f = make_file("requirements.txt", diff=diff)
    issues = check_dependency_integrity([f])
    version_issues = [i for i in issues if i.issue_type == "version_change"]
    assert version_issues
    assert all(i.severity == "low" for i in version_issues)


# ---------------------------------------------------------------------------
# detect_pattern_signals — language-specific patterns
# ---------------------------------------------------------------------------


def test_detects_js_fetch_call():
    f = make_file("src/api.js", diff=_make_diff("const res = fetch('https://api.example.com')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_axios_call():
    f = make_file("src/client.ts", diff=_make_diff("axios.post('/endpoint', data)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_ruby_net_http():
    f = make_file("lib/client.rb", diff=_make_diff("response = Net::HTTP.get(uri)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_go_http_get():
    f = make_file("pkg/client.go", diff=_make_diff('resp, err := http.Get("https://api.example.com")'))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "network_call" for s in signals)


def test_detects_credential_case_insensitive():
    f = make_file("src/config.py", diff=_make_diff('API_KEY = "sk-very-long-secret-value"'))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "credential" for s in signals)


def test_detects_atob_encoded_payload():
    f = make_file("src/utils.js", diff=_make_diff("const data = atob(encodedString)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "encoded_payload" for s in signals)


def test_detects_hex_buffer():
    f = make_file("src/crypto.js", diff=_make_diff("Buffer.from(payload, 'hex')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "encoded_payload" for s in signals)


def test_detects_new_function_dynamic_exec():
    f = make_file("src/handler.js", diff=_make_diff("const fn = new Function('return 42')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "dynamic_exec" for s in signals)


def test_detects_compile_exec_dynamic():
    f = make_file("src/plugin.py", diff=_make_diff("code = compile(src, '<string>', 'exec')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "dynamic_exec" for s in signals)


def test_detects_child_process_exec():
    f = make_file("src/runner.js", diff=_make_diff("child_process.exec(cmd, callback)"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "shell_invoke" for s in signals)


def test_detects_python_socket_suspicious_import():
    f = make_file("src/auth.py", diff=_make_diff("import socket"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "suspicious_import" for s in signals)


def test_detects_js_child_process_require():
    f = make_file("src/handler.js", diff=_make_diff("const { exec } = require('child_process')"))
    signals = detect_pattern_signals([f])
    assert any(s.signal_type == "suspicious_import" for s in signals)


def test_why_unusual_mentions_existing_pattern_when_present_before():
    f = make_file(
        "src/api.py",
        diff=_make_diff("subprocess.run(['ls'])"),
        before="import subprocess\nsubprocess.run(['make'])\n",
    )
    signals = detect_pattern_signals([f])
    shell_signals = [s for s in signals if s.signal_type == "shell_invoke"]
    assert shell_signals
    assert "before" in shell_signals[0].why_unusual.lower() or "exists" in shell_signals[0].why_unusual.lower()


def test_detect_signals_per_file_exception_skipped():
    """A bad file is skipped; valid files are still processed."""
    bad_file = MagicMock()
    bad_file.path = "src/bad.py"
    bad_file.diff = None  # causes _added_lines to fail
    good = make_file("src/auth.py", diff=_make_diff("os.system('whoami')"))
    signals = detect_pattern_signals([bad_file, good])
    assert any(s.signal_type == "shell_invoke" for s in signals)


# ---------------------------------------------------------------------------
# _added_lines — line number tracking
# ---------------------------------------------------------------------------


def test_added_lines_line_numbers_start_from_chunk_header():
    diff = "@@ -10,3 +20,4 @@\n context\n+new line\n context\n"
    lines = _added_lines(diff)
    assert lines  # line numbers should reflect new-file numbering starting at 20
    # First context line = 20, +new line = 21
    assert lines[0][0] == 21


def test_added_lines_multiple_hunks_correct_numbering():
    diff = "@@ -1 +1 @@\n+first add\n@@ -10 +11 @@\n+second add\n"
    lines = _added_lines(diff)
    assert len(lines) == 2
    assert lines[0][0] == 1
    assert lines[1][0] == 11


def test_added_lines_context_lines_increment_counter():
    diff = "@@ -1,4 +1,4 @@\n context\n context\n+added here\n context\n"
    lines = _added_lines(diff)
    assert lines
    # 2 context lines before the addition: start at 1, after 2 context lines = line 3
    assert lines[0][0] == 3


# ---------------------------------------------------------------------------
# _parse_new_packages — format variants
# ---------------------------------------------------------------------------


def test_parse_pyproject_toml_dependency():
    diff = '@@ -1 +1,2 @@\n [project]\n+evil-lib = ">=1.0"\n'
    pkgs = _parse_new_packages(diff, "pypi")
    assert "evil-lib" in pkgs


def test_parse_go_mod_require():
    diff = "@@ -1 +1,2 @@\n module example.com\n+\tgithub.com/evil/pkg v1.2.3\n"
    pkgs = _parse_new_packages(diff, "go")
    assert any("evil" in p for p in pkgs)


def test_parse_skips_comment_lines():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+# this is a comment\n"
    pkgs = _parse_new_packages(diff, "pypi")
    assert "# this is a comment" not in pkgs
    assert not any(p.startswith("#") for p in pkgs)


def test_parse_filters_very_short_package_names():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+a==1.0\n"
    pkgs = _parse_new_packages(diff, "pypi")
    # "a" has length 1, should be filtered (length < 2)
    assert "a" not in pkgs


# ---------------------------------------------------------------------------
# _detect_version_changes
# ---------------------------------------------------------------------------


def test_detect_version_changes_returns_sorted_list():
    diff = "@@ -1,3 +1,3 @@\n-requests==2.27.0\n+requests==2.28.1\n-flask==2.0.0\n+flask==2.1.0\n"
    result = _detect_version_changes(diff, "pypi")
    assert result == sorted(result)
    assert "requests" in result
    assert "flask" in result


def test_detect_version_changes_ignores_unchanged_packages():
    diff = "@@ -1,2 +1,2 @@\n-requests==2.27.0\n+requests==2.28.1\n numpy==1.24.0\n"
    result = _detect_version_changes(diff, "pypi")
    assert "requests" in result
    assert "numpy" not in result  # unchanged (context line, not - or +)


def test_detect_version_changes_returns_empty_for_new_only_packages():
    diff = "@@ -1 +1,2 @@\n requests==2.28.0\n+newpkg==1.0.0\n"
    result = _detect_version_changes(diff, "pypi")
    # newpkg only in added lines, not in removed → not a version change
    assert "newpkg" not in result


def test_detect_version_changes_unknown_ecosystem():
    result = _detect_version_changes("+whatever\n", "unknown")
    assert result == []


# ---------------------------------------------------------------------------
# _osv_check — error paths
# ---------------------------------------------------------------------------


def test_osv_check_returns_empty_on_malformed_json():
    mock_response = MagicMock()
    mock_response.read.return_value = b"not valid json {"
    mock_response.__enter__ = lambda self: self
    mock_response.__exit__ = MagicMock(return_value=False)
    with patch("pr_impact.security.urllib.request.urlopen", return_value=mock_response):
        result = _osv_check("any-pkg", "pypi")
    assert result == []


def test_osv_check_returns_empty_on_http_error():
    import urllib.error
    with patch("pr_impact.security.urllib.request.urlopen",
               side_effect=urllib.error.HTTPError(None, 500, "Server Error", {}, None)):
        result = _osv_check("any-pkg", "pypi")
    assert result == []
