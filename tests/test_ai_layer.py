"""Unit tests for pure helpers in pr_impact/ai_layer.py.

No real API calls are made. run_ai_analysis tests patch anthropic.Anthropic
at the module boundary so no internal helpers are coupled to.
"""

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from pr_impact.ai_layer import (
    _build_blast_radius_signatures,
    _build_diffs_context,
    _build_security_signals_context,
    _call_claude,
    _extract_signatures,
    _find_neighbouring_signatures,
    _find_test_files,
    _log_response,
    _parse_json_safe,
    run_ai_analysis,
)
from pr_impact.models import BlastRadiusEntry, ChangedFile, SecuritySignal
from tests.helpers import make_file


def _make_security_signal(**kwargs) -> SecuritySignal:
    defaults = dict(
        description="New shell invoke",
        file_path="src/auth.py",
        line_number=10,
        signal_type="shell_invoke",
        severity="high",
        why_unusual="No prior shell calls.",
        suggested_action="Confirm intent.",
    )
    defaults.update(kwargs)
    return SecuritySignal(**defaults)

# _DIFF_CHAR_LIMIT = 8_000 tokens * 4 chars/token = 32_000 chars
_LIMIT = 32_000


def _mock_client(*responses):
    """Return a mock Anthropic client whose messages.create returns TextBlock responses.

    Each element of *responses is either a str (JSON to return) or an Exception
    subclass instance (to raise). The retry loop in _call_claude calls
    messages.create up to twice per logical API call, so pass two consecutive
    exceptions to simulate a fully-failed call.
    """
    mock_client = MagicMock()
    side_effects = []
    for resp in responses:
        if isinstance(resp, Exception):
            side_effects.append(resp)
        else:
            block = anthropic.types.TextBlock(type="text", text=resp)
            msg = MagicMock()
            msg.content = [block]
            side_effects.append(msg)
    mock_client.messages.create.side_effect = side_effects
    return mock_client


# ---------------------------------------------------------------------------
# _parse_json_safe
# ---------------------------------------------------------------------------


def test_parse_clean_json_object():
    assert _parse_json_safe('{"key": "value"}') == {"key": "value"}


def test_parse_json_in_json_fence():
    raw = '```json\n{"k": 1}\n```'
    assert _parse_json_safe(raw) == {"k": 1}


def test_parse_json_in_plain_fence():
    raw = '```\n{"k": 1}\n```'
    assert _parse_json_safe(raw) == {"k": 1}


def test_parse_json_after_prose():
    raw = 'Here is the result:\n{"k": 1}'
    assert _parse_json_safe(raw) == {"k": 1}


def test_parse_fully_malformed_returns_empty_dict():
    assert _parse_json_safe("not json at all") == {}


def test_parse_empty_string_returns_empty_dict():
    assert _parse_json_safe("") == {}


def test_parse_json_with_nested_objects():
    raw = '{"outer": {"inner": 42}}'
    result = _parse_json_safe(raw)
    assert result["outer"]["inner"] == 42


def test_parse_json_with_array_value():
    raw = '{"items": [1, 2, 3]}'
    result = _parse_json_safe(raw)
    assert result["items"] == [1, 2, 3]


def test_parse_prose_with_no_braces_returns_empty_dict():
    assert _parse_json_safe("no braces anywhere here") == {}


def test_parse_fence_with_extra_whitespace():
    raw = '```json\n\n  {"k": 1}\n\n```'
    result = _parse_json_safe(raw)
    assert result == {"k": 1}


def test_parse_json_unicode_content():
    raw = '{"msg": "héllo wörld"}'
    assert _parse_json_safe(raw)["msg"] == "héllo wörld"


# ---------------------------------------------------------------------------
# _build_diffs_context
# ---------------------------------------------------------------------------


def _make_diff_file(path: str, diff: str) -> ChangedFile:
    return make_file(path=path, diff=diff)


def test_single_file_within_budget_passes_through():
    diff = "some diff content"
    f = _make_diff_file("a.py", diff)
    ctx = _build_diffs_context([f])
    assert "### a.py" in ctx
    assert diff in ctx
    assert "[truncated]" not in ctx


def test_two_files_within_budget_joined():
    f1 = _make_diff_file("a.py", "diff a")
    f2 = _make_diff_file("b.py", "diff b")
    ctx = _build_diffs_context([f1, f2])
    assert "### a.py" in ctx
    assert "### b.py" in ctx
    assert "diff a" in ctx
    assert "diff b" in ctx


def test_single_file_over_budget_truncated():
    big_diff = "x" * (_LIMIT + 1000)
    f = _make_diff_file("a.py", big_diff)
    ctx = _build_diffs_context([f])
    assert "[truncated]" in ctx
    # Output should not greatly exceed the limit
    assert len(ctx) <= _LIMIT + 100


def test_multiple_files_over_budget_each_has_truncation_marker():
    # Each diff is larger than per_file (= LIMIT // 2) so each gets truncated
    big_diff = "x" * _LIMIT
    f1 = _make_diff_file("a.py", big_diff)
    f2 = _make_diff_file("b.py", big_diff)
    ctx = _build_diffs_context([f1, f2])
    assert "[truncated]" in ctx


def test_single_file_within_budget_no_truncation_marker():
    diff = "y" * (_LIMIT // 2)
    f = _make_diff_file("a.py", diff)
    ctx = _build_diffs_context([f])
    assert "[truncated]" not in ctx


def test_path_header_included_per_file():
    f1 = _make_diff_file("src/a.py", "diff a")
    f2 = _make_diff_file("src/b.py", "diff b")
    ctx = _build_diffs_context([f1, f2])
    assert ctx.count("###") == 2


def test_empty_file_list_returns_empty_string():
    assert _build_diffs_context([]) == ""


def test_single_file_exactly_at_limit_is_not_truncated():
    # The assembled string is "### a.py\n" + diff, so the diff budget is
    # _LIMIT minus the header length to keep the total within _DIFF_CHAR_LIMIT.
    header = "### a.py\n"
    diff = "x" * (_LIMIT - len(header))
    f = _make_diff_file("a.py", diff)
    ctx = _build_diffs_context([f])
    assert "[truncated]" not in ctx
    assert diff in ctx


def test_multiple_files_greedy_uses_full_budget():
    """Three files each slightly over equal-split quota: greedy includes the first two
    in full and only truncates the third, while floor-division would truncate all three."""
    # per-file floor div = 32_000 // 3 = 10_666 < 11_000 → all three truncated under old approach
    # greedy: f1(11_000) + f2(11_000) = 22_000 → remaining 10_000 → f3 truncated
    f1 = _make_diff_file("a.py", "a" * 11_000)
    f2 = _make_diff_file("b.py", "b" * 11_000)
    f3 = _make_diff_file("c.py", "c" * 11_000)
    ctx = _build_diffs_context([f1, f2, f3])
    assert "a" * 11_000 in ctx   # f1 fully included
    assert "b" * 11_000 in ctx   # f2 fully included
    assert "[truncated]" in ctx  # f3 truncated


# ---------------------------------------------------------------------------
# _extract_signatures
# ---------------------------------------------------------------------------


def test_python_keeps_def_line():
    content = "def foo(x):\n    return x + 1\n"
    sigs = _extract_signatures(content, "python")
    assert "def foo(x):" in sigs


def test_python_keeps_async_def_line():
    content = "async def bar():\n    pass\n"
    sigs = _extract_signatures(content, "python")
    assert "async def bar():" in sigs


def test_python_keeps_class_line():
    content = "class MyClass:\n    pass\n"
    sigs = _extract_signatures(content, "python")
    assert "class MyClass:" in sigs


def test_python_keeps_decorator_line():
    content = "@property\ndef foo(self):\n    return self._x\n"
    sigs = _extract_signatures(content, "python")
    assert "@property" in sigs


def test_python_keeps_import_line():
    content = "import os\ndef foo(): pass\n"
    sigs = _extract_signatures(content, "python")
    assert "import os" in sigs


def test_python_keeps_from_import_line():
    content = "from pathlib import Path\ndef foo(): pass\n"
    sigs = _extract_signatures(content, "python")
    assert "from pathlib import Path" in sigs


def test_python_strips_body_lines():
    content = "def foo(x):\n    return x + 1\n"
    sigs = _extract_signatures(content, "python")
    assert "return x + 1" not in sigs


def test_python_strips_blank_lines():
    content = "def foo():\n    pass\n\ndef bar():\n    pass\n"
    sigs = _extract_signatures(content, "python")
    assert "\n\n" not in sigs


def test_python_strips_comment_only_lines():
    content = "# This is a comment\ndef foo(): pass\n"
    sigs = _extract_signatures(content, "python")
    assert "# This is a comment" not in sigs


def test_python_keeps_method_def_line():
    """Methods (indented defs) ARE kept in signatures for AI context."""
    content = "class Foo:\n    def method(self, x):\n        return x\n"
    sigs = _extract_signatures(content, "python")
    assert "def method(self, x):" in sigs


def test_typescript_keeps_export_function():
    content = "export function foo(x: number): string {\n  return x.toString();\n}\n"
    sigs = _extract_signatures(content, "typescript")
    assert "export function foo" in sigs


def test_typescript_keeps_import_line():
    content = "import { bar } from './bar';\nexport function foo() {}\n"
    sigs = _extract_signatures(content, "typescript")
    assert "import { bar } from './bar';" in sigs


def test_typescript_keeps_class_line():
    content = "class Bar extends Base {\n  constructor() {}\n}\n"
    sigs = _extract_signatures(content, "typescript")
    assert "class Bar extends Base {" in sigs


def test_typescript_strips_non_declaration_body_lines():
    content = "export function foo() {\n  return 'hello';\n}\n"
    sigs = _extract_signatures(content, "typescript")
    assert "return 'hello';" not in sigs


def test_typescript_keeps_export_keyword_lines():
    content = "export { foo, bar } from './utils';\n"
    sigs = _extract_signatures(content, "typescript")
    assert "export { foo, bar }" in sigs


def test_javascript_uses_same_pattern_as_typescript():
    content = "import React from 'react';\nfunction App() {\n  return null;\n}\n"
    sigs_js = _extract_signatures(content, "javascript")
    sigs_ts = _extract_signatures(content, "typescript")
    assert sigs_js == sigs_ts


def test_empty_content_returns_empty_string():
    assert _extract_signatures("", "python") == ""
    assert _extract_signatures("", "typescript") == ""


# ---------------------------------------------------------------------------
# _build_blast_radius_signatures
# ---------------------------------------------------------------------------


def test_blast_radius_sigs_unreadable_file_returns_none(tmp_path):
    entry = BlastRadiusEntry(path="ghost.py", distance=1, imported_symbols=[], churn_score=None)
    result = _build_blast_radius_signatures([entry], str(tmp_path))
    assert result == "(none)"


def test_blast_radius_sigs_extracts_from_readable_file(tmp_path):
    (tmp_path / "mod.py").write_text("def foo(): pass\n")
    entry = BlastRadiusEntry(path="mod.py", distance=1, imported_symbols=[], churn_score=None)
    result = _build_blast_radius_signatures([entry], str(tmp_path))
    assert "mod.py" in result
    assert "def foo" in result


def test_blast_radius_sigs_skips_entries_beyond_max_distance(tmp_path):
    (tmp_path / "far.py").write_text("def distant(): pass\n")
    entry = BlastRadiusEntry(path="far.py", distance=3, imported_symbols=[], churn_score=None)
    result = _build_blast_radius_signatures([entry], str(tmp_path), max_distance=2)
    assert "far.py" not in result


def test_blast_radius_sigs_empty_list_returns_none():
    assert _build_blast_radius_signatures([], "/any/path") == "(none)"


# ---------------------------------------------------------------------------
# _find_neighbouring_signatures
# ---------------------------------------------------------------------------


def test_neighbouring_sigs_invalid_repo_path_returns_none():
    f = make_file(path="mod.py")
    result = _find_neighbouring_signatures([f], "/no/such/path")
    assert result == "(none)"


def test_neighbouring_sigs_no_neighbours_returns_none(tmp_path):
    # Only the changed file itself in the dir — no neighbours to find
    (tmp_path / "mod.py").write_text("def foo(): pass\n")
    f = make_file(path="mod.py")
    result = _find_neighbouring_signatures([f], str(tmp_path))
    assert result == "(none)"


def test_neighbouring_sigs_finds_sibling_file(tmp_path):
    (tmp_path / "mod.py").write_text("def foo(): pass\n")
    (tmp_path / "sibling.py").write_text("def bar(): pass\n")
    f = make_file(path="mod.py")
    result = _find_neighbouring_signatures([f], str(tmp_path))
    assert "sibling.py" in result
    assert "def bar" in result


def test_neighbouring_sigs_ignores_unknown_language_files(tmp_path):
    (tmp_path / "mod.py").write_text("def foo(): pass\n")
    (tmp_path / "README.md").write_text("# hello\n")
    f = make_file(path="mod.py")
    result = _find_neighbouring_signatures([f], str(tmp_path))
    assert "README.md" not in result


# ---------------------------------------------------------------------------
# run_ai_analysis — full pipeline and error paths
# ---------------------------------------------------------------------------


def test_run_ai_analysis_raises_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        run_ai_analysis([], [], str(tmp_path))


def test_run_ai_analysis_returns_populated_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({
        "summary": "All good",
        "decisions": [{"description": "d", "rationale": "r", "risk": "rk"}],
        "assumptions": [{"description": "a", "location": "loc", "risk": "rk"}],
    })
    r2 = json.dumps({"anomalies": [{"description": "x", "location": "y", "severity": "high"}]})
    r3 = json.dumps({"test_gaps": [{"behaviour": "b", "location": "l"}]})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert result.summary == "All good"
    assert len(result.decisions) == 1
    assert result.decisions[0].description == "d"
    assert len(result.assumptions) == 1
    assert len(result.anomalies) == 1
    assert result.anomalies[0].severity == "high"
    assert len(result.test_gaps) == 1
    assert result.test_gaps[0].behaviour == "b"


def test_run_ai_analysis_partial_result_on_call_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r3 = json.dumps({"test_gaps": []})
    # _call_claude retries once: two consecutive raises = fully-failed call 2
    client = _mock_client(r1, RuntimeError("timeout"), RuntimeError("timeout"), r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert result.summary == "ok"
    assert result.anomalies == []


def test_run_ai_analysis_missing_response_fields_use_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "s"})
    r2 = json.dumps({})
    r3 = json.dumps({})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert result.summary == "s"
    assert result.decisions == []
    assert result.assumptions == []
    assert result.anomalies == []
    assert result.test_gaps == []


def test_run_ai_analysis_non_dict_items_in_lists_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({
        "summary": "",
        "decisions": ["not a dict", {"description": "d", "rationale": "r", "risk": "rk"}],
        "assumptions": [],
    })
    r2 = json.dumps({"anomalies": [42, {"description": "x", "location": "y", "severity": "low"}]})
    r3 = json.dumps({"test_gaps": [None, {"behaviour": "b", "location": "l"}]})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert len(result.decisions) == 1
    assert len(result.anomalies) == 1
    assert len(result.test_gaps) == 1


def test_run_ai_analysis_missing_nested_fields_default_to_empty_string(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "", "decisions": [{}], "assumptions": [{}]})
    r2 = json.dumps({"anomalies": [{}]})
    r3 = json.dumps({"test_gaps": [{}]})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert result.decisions[0].description == ""
    assert result.decisions[0].rationale == ""
    assert result.decisions[0].risk == ""
    assert result.anomalies[0].severity == "low"
    assert result.test_gaps[0].behaviour == ""
    assert result.test_gaps[0].location == ""


def test_run_ai_analysis_call1_failure_prints_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(RuntimeError("call1 fail"), RuntimeError("call1 fail"), r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert "call1 fail" in capsys.readouterr().err
    assert result.summary == ""


def test_run_ai_analysis_call2_failure_prints_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, RuntimeError("call2 fail"), RuntimeError("call2 fail"), r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert "call2 fail" in capsys.readouterr().err
    assert result.anomalies == []


def test_run_ai_analysis_call3_failure_prints_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    client = _mock_client(r1, r2, RuntimeError("call3 fail"), RuntimeError("call3 fail"))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert "call3 fail" in capsys.readouterr().err
    assert result.test_gaps == []


# ---------------------------------------------------------------------------
# _log_response
# ---------------------------------------------------------------------------


def test_log_response_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    _log_response("test_label", "response content")
    out_file = tmp_path / "primpact_test_label.txt"
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "response content"


def test_log_response_silently_ignores_write_failure(tmp_path):
    # Pass a path that can't be opened (directory name as label)
    # Should not raise even if the write fails
    _log_response("label\x00invalid", "content")  # null byte makes path invalid on most OSes


# ---------------------------------------------------------------------------
# _find_test_files
# ---------------------------------------------------------------------------


def test_find_test_files_finds_matching_test(tmp_path):
    (tmp_path / "models.py").write_text("class Foo: pass\n")
    (tmp_path / "test_models.py").write_text("def test_foo(): pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert "test_models.py" in result
    assert "test_foo" in result


def test_find_test_files_ignores_non_test_files(tmp_path):
    (tmp_path / "models.py").write_text("class Foo: pass\n")
    (tmp_path / "helpers.py").write_text("def helper(): pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert "helpers.py" not in result


def test_find_test_files_no_tests_returns_placeholder(tmp_path):
    (tmp_path / "models.py").write_text("class Foo: pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert result == "(no test files found)"


def test_find_test_files_looks_in_tests_subdir(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_models.py").write_text("def test_foo(): pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert "test_models.py" in result


# ---------------------------------------------------------------------------
# _find_neighbouring_signatures: max_per_dir cap
# ---------------------------------------------------------------------------


def test_neighbouring_sigs_respects_max_per_dir_cap(tmp_path):
    # Write 6 Python files + 1 changed file; max_per_dir defaults to 5
    for i in range(6):
        (tmp_path / f"mod_{i}.py").write_text(f"def func_{i}(): pass\n")
    (tmp_path / "changed.py").write_text("def changed(): pass\n")
    f = make_file(path="changed.py")
    result = _find_neighbouring_signatures([f], str(tmp_path), max_per_dir=5)
    # Should include at most 5 neighbours, not 6
    assert result.count("###") <= 5


def test_neighbouring_sigs_second_file_in_same_dir_skips_when_capped(tmp_path):
    """Second changed file in same dir hits the count >= max_per_dir guard."""
    (tmp_path / "a.py").write_text("def a(): pass\n")
    (tmp_path / "b.py").write_text("def b(): pass\n")
    (tmp_path / "neighbour.py").write_text("def neighbour(): pass\n")
    f1 = make_file(path="a.py")
    f2 = make_file(path="b.py")
    result = _find_neighbouring_signatures([f1, f2], str(tmp_path), max_per_dir=1)
    # Only 1 neighbour found (from f1's scan); f2's outer-loop check fires and skips
    assert result.count("###") == 1


def test_neighbouring_sigs_skips_empty_neighbour_files(tmp_path):
    """Files with no content hit the 'if not content: continue' branch."""
    (tmp_path / "changed.py").write_text("def changed(): pass\n")
    (tmp_path / "empty.py").write_text("")
    (tmp_path / "real.py").write_text("def real(): pass\n")
    f = make_file(path="changed.py")
    result = _find_neighbouring_signatures([f], str(tmp_path))
    assert "empty.py" not in result
    assert "real.py" in result


# ---------------------------------------------------------------------------
# _call_claude: retry and re-raise
# ---------------------------------------------------------------------------


def test_call_claude_raises_after_two_consecutive_failures():
    """Both attempts fail — the second exception is re-raised."""
    client = _mock_client(RuntimeError("attempt 0"), RuntimeError("attempt 1"))
    with pytest.raises(RuntimeError, match="attempt 1"):
        _call_claude(client, "any prompt")


def test_call_claude_succeeds_on_second_attempt():
    r = json.dumps({"ok": True})
    client = _mock_client(RuntimeError("transient"), r)
    result = _call_claude(client, "prompt")
    assert result == r


def test_call_claude_raises_on_unexpected_block_type():
    """Response with a non-TextBlock triggers ValueError (unexpected block type path)."""
    mock_client = MagicMock()
    bad_block = MagicMock(spec=[])  # not an anthropic.types.TextBlock
    msg = MagicMock()
    msg.content = [bad_block]
    mock_client.messages.create.return_value = msg
    with pytest.raises(ValueError):
        _call_claude(mock_client, "prompt")


# ---------------------------------------------------------------------------
# _parse_json_safe: regex fallback also fails to parse
# ---------------------------------------------------------------------------


def test_parse_json_fallback_finds_braces_but_still_invalid():
    """Regex finds {...} but the content inside is not valid JSON (regex fallback path)."""
    raw = "{ invalid: json content }"
    assert _parse_json_safe(raw) == {}


# ---------------------------------------------------------------------------
# _find_test_files: dedup and stem-mismatch branches
# ---------------------------------------------------------------------------


def test_find_test_files_dedup_across_changed_files(tmp_path):
    """Same test file found via two different source files; second hit is deduped."""
    (tmp_path / "test_a.py").write_text("def test_func(): pass\n")
    # a.py has stem "a" → test_a.py matches; b.py also scans same dir → test_a.py already in found
    f1 = make_file(path="a.py")
    f2 = make_file(path="b.py")
    result = _find_test_files([f1, f2], str(tmp_path))
    assert result.count("test_a.py") == 1  # not duplicated


def test_find_test_files_skips_unrelated_test_files(tmp_path):
    """test_utils.py doesn't contain the stem 'models' — hits stem-mismatch continue."""
    (tmp_path / "test_utils.py").write_text("def test_helper(): pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert "test_utils.py" not in result


# ---------------------------------------------------------------------------
# _build_security_signals_context
# ---------------------------------------------------------------------------


def test_security_context_empty_signals_returns_none_placeholder():
    signals_text, _ = _build_security_signals_context([], [])
    assert signals_text == "(none)"


def test_security_context_signal_with_line_number_formatted():
    sig = _make_security_signal(line_number=42)
    signals_text, _ = _build_security_signals_context([sig], [])
    assert "line 42" in signals_text
    assert "HIGH" in signals_text
    assert "shell_invoke" in signals_text


def test_security_context_signal_without_line_number_omits_line_info():
    sig = _make_security_signal(line_number=None)
    signals_text, _ = _build_security_signals_context([sig], [])
    assert "line" not in signals_text


def test_security_context_extracts_signatures_from_content_before():
    f = make_file(path="src/auth.py", before="def login(): pass\n")
    sig = _make_security_signal(file_path="src/auth.py")
    _, file_ctx = _build_security_signals_context([sig], [f])
    assert "src/auth.py" in file_ctx
    assert "def login" in file_ctx


def test_security_context_no_prior_content_returns_placeholder():
    f = make_file(path="new_file.py", before="")
    _, file_ctx = _build_security_signals_context([_make_security_signal()], [f])
    assert file_ctx == "(no prior content)"


def test_security_context_multiple_signals_all_included():
    sigs = [
        _make_security_signal(signal_type="shell_invoke", severity="high"),
        _make_security_signal(signal_type="network_call", severity="medium"),
    ]
    signals_text, _ = _build_security_signals_context(sigs, [])
    assert "shell_invoke" in signals_text
    assert "network_call" in signals_text


# ---------------------------------------------------------------------------
# run_ai_analysis — 4th call (security signals)
# ---------------------------------------------------------------------------


def test_run_ai_analysis_skips_4th_call_when_pattern_signals_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=None)
    assert client.messages.create.call_count == 3
    assert result.security_signals == []


def test_run_ai_analysis_skips_4th_call_when_pattern_signals_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[])
    assert client.messages.create.call_count == 3
    assert result.security_signals == []


def test_run_ai_analysis_makes_4th_call_when_signals_present(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([{
        "description": "AI-enriched signal",
        "file_path": "src/auth.py",
        "line_number": 10,
        "signal_type": "shell_invoke",
        "severity": "high",
        "why_unusual": "No prior shells.",
        "suggested_action": "Ask author.",
    }])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert client.messages.create.call_count == 4
    assert len(result.security_signals) == 1
    assert result.security_signals[0].description == "AI-enriched signal"
    assert result.security_signals[0].severity == "high"


def test_run_ai_analysis_parses_signals_from_dict_with_signals_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps({"signals": [{
        "description": "wrapped", "file_path": "f.py", "line_number": 1,
        "signal_type": "network_call", "severity": "medium",
        "why_unusual": "unusual", "suggested_action": "check",
    }]})
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert len(result.security_signals) == 1
    assert result.security_signals[0].description == "wrapped"


def test_run_ai_analysis_parses_signals_from_dict_with_security_signals_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps({"security_signals": [{
        "description": "nested", "file_path": "f.py", "line_number": 5,
        "signal_type": "credential", "severity": "high",
        "why_unusual": "unusual", "suggested_action": "check",
    }]})
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert len(result.security_signals) == 1
    assert result.security_signals[0].signal_type == "credential"


def test_run_ai_analysis_falls_back_to_raw_signals_on_empty_response(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([])  # empty list → fall back
    client = _mock_client(r1, r2, r3, r4)
    raw_sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[raw_sig])
    assert result.security_signals == [raw_sig]


def test_run_ai_analysis_falls_back_to_raw_signals_on_unexpected_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps({})  # dict with no signals key → fall back
    client = _mock_client(r1, r2, r3, r4)
    raw_sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[raw_sig])
    assert result.security_signals == [raw_sig]


def test_run_ai_analysis_non_int_line_number_becomes_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([{
        "description": "sig", "file_path": "f.py",
        "line_number": "not-an-int",  # non-int → should become None
        "signal_type": "eval", "severity": "high",
        "why_unusual": "u", "suggested_action": "s",
    }])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert result.security_signals[0].line_number is None
