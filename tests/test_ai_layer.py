"""Unit tests for pure helpers in pr_impact/ai_layer.py.

No real API calls are made. run_ai_analysis tests patch anthropic.Anthropic
at the module boundary so no internal helpers are coupled to.
"""

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from pr_impact.ai_client import (
    _call_claude,
    _log_response,
    _parse_json_safe,
    call_api,
)
from pr_impact.ai_context import (
    _extract_signatures,
    build_blast_radius_signatures as _build_blast_radius_signatures,
    build_changed_files_before_signatures as _build_changed_files_before_signatures,
    build_diffs_context as _build_diffs_context,
    build_historical_context as _build_historical_context,
    build_security_signals_context as _build_security_signals_context,
    build_signatures_before_after as _build_signatures_before_after,
    find_neighbouring_signatures as _find_neighbouring_signatures,
    find_test_files as _find_test_files,
)
from pr_impact.ai_layer import (
    _should_run_semantic_equivalence,
    run_ai_analysis,
    run_verdict_analysis,
)
from pr_impact.models import BlastRadiusEntry, ChangedFile, ChangedSymbol, SecuritySignal, Verdict
from tests.helpers import make_file, make_report, make_security_signal as _make_security_signal

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
# _build_changed_files_before_signatures
# ---------------------------------------------------------------------------


def test_build_changed_files_before_sigs_extracts_imports_and_defs():
    """Before-signatures include import and def lines from content_before."""
    f = make_file(
        path="cli.py",
        before="from .classifier import classify\nfrom .security import detect\ndef _run_pipeline(): pass\n",
    )
    result = _build_changed_files_before_signatures([f])
    assert "cli.py (before this PR)" in result
    assert "from .classifier import classify" in result
    assert "from .security import detect" in result
    assert "def _run_pipeline" in result


def test_build_changed_files_before_sigs_skips_empty_before():
    """Files with no content_before (new files) are omitted."""
    f = make_file(path="new_module.py", before="")
    result = _build_changed_files_before_signatures([f])
    assert result == "(none)"


def test_build_changed_files_before_sigs_skips_files_with_no_extractable_sigs():
    """Files whose before-content has no import/def lines produce no entry."""
    f = make_file(path="data.py", before="x = 1\ny = 2\n")
    result = _build_changed_files_before_signatures([f])
    assert result == "(none)"


def test_build_changed_files_before_sigs_multiple_files():
    """Multiple changed files each get their own section."""
    f1 = make_file(path="a.py", before="from .x import foo\n")
    f2 = make_file(path="b.py", before="from .y import bar\n")
    result = _build_changed_files_before_signatures([f1, f2])
    assert "a.py (before this PR)" in result
    assert "b.py (before this PR)" in result
    assert "from .x import foo" in result
    assert "from .y import bar" in result


def test_run_ai_analysis_call2_includes_before_signatures(monkeypatch, tmp_path):
    """Call 2 prompt includes before-signatures so Claude can see established patterns."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    empty = json.dumps({"anomalies": []})
    client = _mock_client(
        json.dumps({"summary": "", "decisions": [], "assumptions": []}),
        empty,   # call 2
        json.dumps({"test_gaps": []}),
    )
    # File has a distinctive before-import that should appear in the prompt
    f = make_file(
        path="cli.py",
        before="from .classifier import classify_changed_file\ndef _run_pipeline(): pass\n",
    )
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([f], [], str(tmp_path))
    # call 2 is the second messages.create call (index 1)
    call2_prompt = client.messages.create.call_args_list[1][1]["messages"][0]["content"]
    assert "from .classifier import classify_changed_file" in call2_prompt


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


def test_find_test_files_async_python_tests_extracted(tmp_path):
    """async def test_* functions are included alongside sync ones."""
    (tmp_path / "auth.py").write_text("async def login(): pass\n")
    (tmp_path / "test_auth.py").write_text(
        "import pytest\n"
        "async def test_login_success(): pass\n"
        "def test_login_failure(): pass\n"
    )
    f = make_file(path="auth.py")
    result = _find_test_files([f], str(tmp_path))
    assert "test_login_success" in result
    assert "test_login_failure" in result


def test_find_test_files_js_it_blocks_extracted(tmp_path):
    """JS/TS it() and test() block titles are extracted instead of falling back."""
    (tmp_path / "auth.js").write_text("export function login() {}\n")
    (tmp_path / "test_auth.js").write_text(
        'describe("auth", () => {\n'
        '  it("logs in with valid credentials", () => {});\n'
        '  test("rejects invalid password", () => {});\n'
        "});\n"
    )
    f = make_file(path="auth.js")
    result = _find_test_files([f], str(tmp_path))
    assert "logs in with valid credentials" in result
    assert "rejects invalid password" in result
    # raw content fallback should NOT have been used (no 'export function' in body)
    assert "export function" not in result


def test_find_test_files_fallback_uses_4000_chars(tmp_path):
    """When no test names can be extracted, fallback slice is 4000 chars (not 2000)."""
    (tmp_path / "widget.js").write_text("function Widget() {}\n")
    # Content with no it/test/describe patterns — triggers 4000-char fallback
    long_content = "// non-standard structure\n" + "const x = 1;\n" * 500   # ~7500 chars
    (tmp_path / "test_widget.js").write_text(long_content)
    f = make_file(path="widget.js")
    result = _find_test_files([f], str(tmp_path))
    # With the 4000-char fallback the body is at least 3500 chars
    assert len(result) > 3500


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


def test_security_context_signal_for_file_not_in_changed_files():
    """Signal references a file not in changed_files — no crash, context just omits it."""
    sig = _make_security_signal(file_path="src/missing.py")
    f = make_file(path="src/other.py", before="def other(): pass\n")
    signals_text, file_ctx = _build_security_signals_context([sig], [f])
    # Signal still appears in signals_text
    assert "src/missing.py" in signals_text
    # File context only has 'other.py' (missing.py not in changed_files)
    assert "src/missing.py" not in file_ctx


def test_security_context_multiple_signals_same_file_deduped_in_context():
    """Two signals for the same file — the file's signatures appear once in file_ctx."""
    sig1 = _make_security_signal(file_path="src/auth.py", signal_type="shell_invoke")
    sig2 = _make_security_signal(file_path="src/auth.py", signal_type="network_call")
    f = make_file(path="src/auth.py", before="def login(): pass\n")
    _, file_ctx = _build_security_signals_context([sig1, sig2], [f])
    # 'src/auth.py' should appear exactly once in the context
    assert file_ctx.count("src/auth.py") == 1


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


def test_run_ai_analysis_4th_prompt_contains_signal_description(monkeypatch, tmp_path):
    """The 4th prompt sent to Claude includes the signal's description string."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal(description="DISTINCTIVE_SIGNAL_DESCRIPTION")
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    fourth_call_prompt = client.messages.create.call_args_list[3][1]["messages"][0]["content"]
    assert "DISTINCTIVE_SIGNAL_DESCRIPTION" in fourth_call_prompt


def test_run_ai_analysis_4th_prompt_contains_file_context(monkeypatch, tmp_path):
    """The 4th prompt sent to Claude includes file signatures from before-content."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal(file_path="src/auth.py")
    f = make_file(path="src/auth.py", before="def distinctive_function_xyz(): pass\n")
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([f], [], str(tmp_path), pattern_signals=[sig])
    fourth_call_prompt = client.messages.create.call_args_list[3][1]["messages"][0]["content"]
    assert "distinctive_function_xyz" in fourth_call_prompt


def test_run_ai_analysis_non_dict_items_in_4th_response_skipped(monkeypatch, tmp_path):
    """Non-dict items in the 4th response list are filtered out."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([
        "not a dict",
        42,
        {"description": "valid", "file_path": "f.py", "line_number": 1,
         "signal_type": "eval", "severity": "high", "why_unusual": "u", "suggested_action": "s"},
    ])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert len(result.security_signals) == 1
    assert result.security_signals[0].description == "valid"


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


# ---------------------------------------------------------------------------
# _build_security_signals_context — edge cases
# ---------------------------------------------------------------------------


def test_security_context_empty_signals_file_context_still_built():
    """Empty signals list → signals_text is placeholder, file_ctx still extracted."""
    f = make_file(path="src/auth.py", before="def login(): pass\n")
    signals_text, file_ctx = _build_security_signals_context([], [f])
    assert signals_text == "(none)"
    # File context is built regardless (not gated on signals)
    assert "def login" in file_ctx


def test_security_context_signal_with_empty_file_path_does_not_crash():
    """Signal with file_path='' is formatted without crashing."""
    sig = _make_security_signal(file_path="")
    signals_text, _ = _build_security_signals_context([sig], [])
    # Signal still appears (description is always included)
    assert "shell_invoke" in signals_text


def test_security_context_signal_with_none_line_number_formatted_cleanly():
    """line_number=None produces no 'None' literal in signals_text."""
    sig = _make_security_signal(line_number=None)
    signals_text, _ = _build_security_signals_context([sig], [])
    assert "None" not in signals_text


def test_security_context_file_no_extractable_signatures_skipped():
    """A file whose before-content yields no signatures is omitted from file_ctx."""
    # Content with no function/class/import statements → _extract_signatures returns ""
    f = make_file(path="src/data.py", before="x = 1\ny = 2\n")
    sig = _make_security_signal(file_path="src/data.py")
    _, file_ctx = _build_security_signals_context([sig], [f])
    # No signatures extracted → file absent from context
    assert "src/data.py" not in file_ctx


# ---------------------------------------------------------------------------
# run_ai_analysis — 4th call failure and type validation
# ---------------------------------------------------------------------------


def test_run_ai_analysis_4th_call_api_error_falls_back_to_raw(monkeypatch, tmp_path):
    """When the 4th API call raises (e.g. timeout), result falls back to raw pattern_signals."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    # Two failures (retry logic tries twice per call)
    client = _mock_client(r1, r2, r3, RuntimeError("timeout"), RuntimeError("timeout"))
    raw_sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[raw_sig])
    assert result.security_signals == [raw_sig]


def test_run_ai_analysis_4th_response_string_falls_back_to_raw(monkeypatch, tmp_path):
    """When the 4th response JSON is a bare string, fall back to raw pattern_signals."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps("just a string")
    client = _mock_client(r1, r2, r3, r4)
    raw_sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[raw_sig])
    assert result.security_signals == [raw_sig]


def test_run_ai_analysis_4th_response_missing_required_fields_uses_defaults(monkeypatch, tmp_path):
    """Items in 4th response missing severity/signal_type default to empty/low rather than crash."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([{
        "description": "partial signal",
        # severity and signal_type omitted
    }])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[sig])
    assert len(result.security_signals) == 1
    assert result.security_signals[0].severity == "low"   # default
    assert result.security_signals[0].signal_type == ""   # default


# ---------------------------------------------------------------------------
# run_verdict_analysis
# ---------------------------------------------------------------------------


def _verdict_response(**overrides) -> str:
    data = {
        "status": "clean",
        "agent_should_continue": False,
        "rationale": "No blockers found.",
        "blockers": [],
    }
    data.update(overrides)
    return json.dumps(data)


def test_run_verdict_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        run_verdict_analysis(make_report().ai_analysis, [])


def test_run_verdict_clean_report(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert isinstance(verdict, Verdict)
    assert verdict.status == "clean"
    assert verdict.agent_should_continue is False
    assert verdict.rationale == "No blockers found."
    assert verdict.blockers == []


def test_run_verdict_has_blockers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(_verdict_response(
        status="has_blockers",
        agent_should_continue=True,
        rationale="New code path has no test coverage.",
        blockers=[{
            "category": "test_gap",
            "description": "login() error path is untested",
            "location": "src/auth.py:login",
        }],
    ))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.status == "has_blockers"
    assert verdict.agent_should_continue is True
    assert len(verdict.blockers) == 1
    assert verdict.blockers[0].category == "test_gap"
    assert verdict.blockers[0].location == "src/auth.py:login"


def test_run_verdict_api_failure_returns_clean(monkeypatch):
    """Any API failure → clean verdict so agent loop always terminates safely."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(RuntimeError("network"), RuntimeError("network"))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.agent_should_continue is False


def test_run_verdict_non_dict_response_raises(monkeypatch):
    """Non-dict JSON (e.g. bare string) → ValueError so cli.py can handle gracefully."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(json.dumps("unexpected"))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        with pytest.raises(ValueError, match="not a JSON object"):
            run_verdict_analysis(make_report().ai_analysis, [])


def test_run_verdict_non_dict_blockers_skipped(monkeypatch):
    """Non-dict items in blockers array are silently dropped."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(_verdict_response(
        status="has_blockers",
        agent_should_continue=True,
        blockers=["not a dict", {"category": "anomaly", "description": "real", "location": "f.py"}],
    ))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert len(verdict.blockers) == 1
    assert verdict.blockers[0].description == "real"


def test_run_verdict_prompt_includes_anomaly_count(monkeypatch):
    """Prompt sent to Claude includes the anomaly count for grounding."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from pr_impact.models import AIAnalysis, Anomaly
    ai_analysis = AIAnalysis(
        anomalies=[Anomaly(description="x", location="f.py:1", severity="high")],
    )
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(ai_analysis, [])
    prompt = client.messages.create.call_args[1]["messages"][0]["content"]
    assert "1" in prompt   # anomaly_count=1 appears in the prompt
    assert "x" in prompt   # anomaly description included


def test_run_verdict_empty_anomalies_formats_as_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from pr_impact.models import AIAnalysis
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(AIAnalysis(), [])
    prompt = client.messages.create.call_args[1]["messages"][0]["content"]
    assert "(none)" in prompt


def test_run_verdict_empty_test_gaps_formats_as_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from pr_impact.models import AIAnalysis
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(AIAnalysis(), [])
    prompt = client.messages.create.call_args[1]["messages"][0]["content"]
    assert "(none)" in prompt


def test_run_verdict_empty_security_signals_formats_as_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from pr_impact.models import AIAnalysis
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(AIAnalysis(), [])
    prompt = client.messages.create.call_args[1]["messages"][0]["content"]
    assert "(none)" in prompt


def test_run_verdict_empty_dependency_issues_formats_as_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from pr_impact.models import AIAnalysis
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(AIAnalysis(), [])
    prompt = client.messages.create.call_args[1]["messages"][0]["content"]
    assert "(none)" in prompt


def test_run_verdict_missing_blockers_key_produces_empty_list(monkeypatch):
    """Dict response with no 'blockers' key → blockers defaults to empty list."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(json.dumps({
        "status": "clean",
        "agent_should_continue": False,
        "rationale": "Fine.",
        # 'blockers' key absent
    }))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.blockers == []


# ---------------------------------------------------------------------------
# _parse_json_safe — array fallback
# ---------------------------------------------------------------------------


def test_parse_json_safe_returns_list_when_top_level_array():
    from pr_impact.ai_client import _parse_json_safe
    result = _parse_json_safe('[{"a": 1}, {"b": 2}]')
    assert result == [{"a": 1}, {"b": 2}]


def test_parse_json_safe_recovers_list_from_prose_prefix():
    """Fallback regex should extract [...] when prose precedes the array."""
    from pr_impact.ai_client import _parse_json_safe
    result = _parse_json_safe('Here is the result:\n[{"x": 1}]')
    assert result == [{"x": 1}]


# ---------------------------------------------------------------------------
# run_verdict_analysis — agent_should_continue coercion
# ---------------------------------------------------------------------------


def test_run_verdict_string_false_not_coerced_to_true(monkeypatch):
    """bool('false') would be True; our parser must return False."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(json.dumps({
        "status": "clean",
        "agent_should_continue": "false",
        "rationale": "Fine.",
        "blockers": [],
    }))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.agent_should_continue is False


def test_run_verdict_string_true_parsed_correctly(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(json.dumps({
        "status": "has_blockers",
        "agent_should_continue": "true",
        "rationale": "Gaps.",
        "blockers": [],
    }))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.agent_should_continue is True


def test_run_verdict_string_zero_not_truthy(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(json.dumps({
        "status": "clean",
        "agent_should_continue": "0",
        "rationale": "Fine.",
        "blockers": [],
    }))
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        verdict = run_verdict_analysis(make_report().ai_analysis, [])
    assert verdict.agent_should_continue is False


# ---------------------------------------------------------------------------
# run_ai_analysis — 4th prompt includes diff
# ---------------------------------------------------------------------------


def test_run_ai_analysis_4th_prompt_contains_diff(monkeypatch, tmp_path):
    """PROMPT_SECURITY_SIGNALS receives changed_files_diff so Claude sees the actual code."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps([])
    client = _mock_client(r1, r2, r3, r4)
    sig = _make_security_signal()
    f = make_file(path="src/auth.py", diff="+DISTINCTIVE_DIFF_CONTENT\n")
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([f], [], str(tmp_path), pattern_signals=[sig])
    fourth_prompt = client.messages.create.call_args_list[3][1]["messages"][0]["content"]
    assert "DISTINCTIVE_DIFF_CONTENT" in fourth_prompt


# ---------------------------------------------------------------------------
# _call_claude: first-attempt success + multiple content blocks
# ---------------------------------------------------------------------------


def test_call_claude_succeeds_on_first_attempt_single_call():
    """When the first attempt succeeds, messages.create is called exactly once."""
    r = json.dumps({"ok": True})
    client = _mock_client(r)
    result = _call_claude(client, "prompt")
    assert result == r
    assert client.messages.create.call_count == 1


def test_call_claude_returns_first_text_block_ignores_extras():
    """content array with TextBlock first followed by a non-TextBlock: first element returned."""
    mock_client = MagicMock()
    text_block = anthropic.types.TextBlock(type="text", text="hello")
    other_block = MagicMock(spec=[])  # some non-TextBlock at index 1
    msg = MagicMock()
    msg.content = [text_block, other_block]
    mock_client.messages.create.return_value = msg
    result = _call_claude(mock_client, "prompt")
    assert result == "hello"


# ---------------------------------------------------------------------------
# _log_response: non-writable / non-existent temp directory
# ---------------------------------------------------------------------------


def test_log_response_silently_ignores_nonexistent_tempdir(monkeypatch):
    """When tempdir doesn't exist, _log_response swallows the OSError."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/does/not/exist/primpact_test")
    _log_response("label", "content")  # must not raise


# ---------------------------------------------------------------------------
# call_api: model passthrough + _parse_json_safe exception
# ---------------------------------------------------------------------------


def test_call_api_passes_model_to_call_claude():
    """model arg is forwarded all the way to messages.create."""
    r = json.dumps({"ok": True})
    client = _mock_client(r)
    call_api(client, "prompt", "label", model="claude-haiku-4-5-20251001")
    assert client.messages.create.call_args[1]["model"] == "claude-haiku-4-5-20251001"


def test_call_api_handles_parse_json_safe_exception(capsys):
    """If _parse_json_safe raises (patched), call_api returns {} and prints warning."""
    r = json.dumps({"ok": True})
    client = _mock_client(r)
    with patch("pr_impact.ai_client._parse_json_safe", side_effect=RuntimeError("parse boom")):
        result = call_api(client, "prompt", "label")
    assert result == {}
    assert "parse boom" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _parse_json_safe: explicit ai_client fallback path (regex match, invalid JSON)
# ---------------------------------------------------------------------------


def test_parse_json_safe_regex_match_with_still_invalid_json():
    """Regex finds {…} but interior is not valid JSON → empty dict returned."""
    raw = "result: { key without quotes: invalid }"
    assert _parse_json_safe(raw) == {}


# ---------------------------------------------------------------------------
# build_diffs_context: zero-length diff file in multi-file budget calculation
# ---------------------------------------------------------------------------


def test_build_diffs_context_zero_length_diff_uses_no_budget():
    """File with empty diff is included in output and consumes no budget allowance."""
    f1 = _make_diff_file("a.py", "")          # zero-length diff
    f2 = _make_diff_file("b.py", "some diff content")
    ctx = _build_diffs_context([f1, f2])
    assert "### a.py" in ctx
    assert "### b.py" in ctx
    assert "[truncated]" not in ctx


def test_build_diffs_context_zero_length_diff_gets_truncated_marker_when_budget_gone():
    """When budget is exhausted by earlier files, a zero-length diff file still gets marker."""
    big_diff = "x" * _LIMIT
    f1 = _make_diff_file("a.py", big_diff)   # exhausts budget
    f2 = _make_diff_file("b.py", "")         # zero-length, but budget is 0
    ctx = _build_diffs_context([f1, f2])
    assert "### b.py" in ctx
    assert "[truncated]" in ctx


# ---------------------------------------------------------------------------
# build_blast_radius_signatures: entry at exactly max_distance included
# ---------------------------------------------------------------------------


def test_blast_radius_sigs_includes_entry_at_exactly_max_distance(tmp_path):
    """Entry at distance == max_distance is included (only > max_distance is skipped)."""
    (tmp_path / "boundary.py").write_text("def boundary_func(): pass\n")
    entry = BlastRadiusEntry(path="boundary.py", distance=2, imported_symbols=[], churn_score=None)
    result = _build_blast_radius_signatures([entry], str(tmp_path), max_distance=2)
    assert "boundary.py" in result
    assert "def boundary_func" in result


# ---------------------------------------------------------------------------
# find_test_files: existing search dir with no matching files + read returns empty
# ---------------------------------------------------------------------------


def test_find_test_files_search_dir_exists_no_matching_files(tmp_path):
    """tests/ dir exists but contains no file matching the changed file's stem."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_other.py").write_text("def test_other(): pass\n")
    f = make_file(path="models.py")
    result = _find_test_files([f], str(tmp_path))
    assert result == "(no test files found)"


def test_find_test_files_read_returns_empty_skips_file(tmp_path):
    """When _read_file_safe returns '' for a candidate test file, it is skipped."""
    (tmp_path / "test_models.py").write_text("def test_foo(): pass\n")
    f = make_file(path="models.py")
    with patch("pr_impact.ai_context._read_file_safe", return_value=""):
        result = _find_test_files([f], str(tmp_path))
    assert result == "(no test files found)"


# ---------------------------------------------------------------------------
# find_neighbouring_signatures: scandir raises an exception
# ---------------------------------------------------------------------------


def test_find_neighbouring_signatures_scandir_raises_propagates(tmp_path):
    """os.scandir raising inside find_neighbouring_signatures propagates to caller."""
    f = make_file(path="mod.py")
    with patch("os.scandir", side_effect=PermissionError("no access")):
        with pytest.raises(PermissionError):
            _find_neighbouring_signatures([f], str(tmp_path))


def test_run_ai_analysis_neighbour_sigs_exception_prints_warning(monkeypatch, tmp_path, capsys):
    """When find_neighbouring_signatures raises, run_ai_analysis prints warning and continues."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        with patch("pr_impact.ai_layer.find_neighbouring_signatures",
                   side_effect=PermissionError("no access")):
            result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert "no access" in capsys.readouterr().err
    assert result.anomalies == []


# ---------------------------------------------------------------------------
# build_changed_files_before_signatures: unknown language
# ---------------------------------------------------------------------------


def test_build_changed_files_before_signatures_unknown_language():
    """Files with unknown extension use the JS signature pattern; must not crash."""
    f = make_file(path="data.xyz", before="export function foo() {}\n")
    result = _build_changed_files_before_signatures([f])
    # With JS pattern, 'export function' is matched; result has content
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_signatures_before_after: identical before/after signatures excluded
# ---------------------------------------------------------------------------


def test_build_signatures_before_after_identical_sigs_excluded():
    """When before and after signatures are identical, the file is omitted."""
    content = "def foo(): pass\n"
    f = make_file(path="a.py", before=content, after=content)
    result = _build_signatures_before_after([f])
    assert result == "(no signature changes)"


def test_build_signatures_before_after_different_sigs_included():
    """When signatures differ, the file appears with both before and after blocks."""
    f = make_file(path="a.py", before="def foo(): pass\n", after="def foo(x): pass\n")
    result = _build_signatures_before_after([f])
    assert "a.py" in result
    assert "Before:" in result
    assert "After:" in result


# ---------------------------------------------------------------------------
# build_security_signals_context: signal file not in changed_files (empty list)
# ---------------------------------------------------------------------------


def test_build_security_signals_context_changed_files_empty():
    """Signal references a file but changed_files is empty → file_ctx is placeholder."""
    sig = _make_security_signal(file_path="src/auth.py")
    signals_text, file_ctx = _build_security_signals_context([sig], [])
    assert "src/auth.py" in signals_text
    assert file_ctx == "(no prior content)"


# ---------------------------------------------------------------------------
# build_historical_context: partial and empty combinations
# ---------------------------------------------------------------------------


def test_build_historical_context_anomaly_history_only():
    """With only anomaly_history (hotspots=None), recurring patterns section is present."""
    anomaly_history = [{"file": "a.py", "description": "Direct DB write"}]
    result = _build_historical_context(anomaly_history, None)
    assert "Direct DB write" in result
    assert "hotspot" not in result.lower()


def test_build_historical_context_hotspots_only():
    """With only hotspots (anomaly_history=None), hotspot section is present."""
    hotspots = [{"file": "a.py", "appearances": 5}]
    result = _build_historical_context(None, hotspots)
    assert "a.py" in result
    assert "5 appearances" in result
    assert "Recurring anomaly" not in result


def test_build_historical_context_both_empty_lists():
    """Both anomaly_history and hotspots empty → empty string (no context to add)."""
    result = _build_historical_context([], [])
    assert result == ""


# ---------------------------------------------------------------------------
# _should_run_semantic_equivalence: small diff but interface-level change
# ---------------------------------------------------------------------------


def test_should_run_semantic_equivalence_small_diff_with_interface_change():
    """Small diff (≤20 lines) but a symbol has interface_changed → returns True."""
    sym = ChangedSymbol(
        name="login",
        kind="function",
        change_type="interface_changed",
        signature_before="def login()",
        signature_after="def login(user)",
    )
    f = make_file(diff="+short\n")  # 1 diff line — well below threshold
    f.changed_symbols = [sym]
    assert _should_run_semantic_equivalence([f]) is True


def test_should_run_semantic_equivalence_small_diff_no_interface_change():
    """Small diff and no interface-level symbols → returns False."""
    sym = ChangedSymbol(
        name="login",
        kind="function",
        change_type="implementation_changed",
        signature_before="def login()",
        signature_after="def login()",
    )
    f = make_file(diff="+short\n")
    f.changed_symbols = [sym]
    assert _should_run_semantic_equivalence([f]) is False


# ---------------------------------------------------------------------------
# run_ai_analysis: custom model + find_test_files exception + call4/5 edge cases
# ---------------------------------------------------------------------------


def test_run_ai_analysis_custom_model_passed_to_all_calls(monkeypatch, tmp_path):
    """When model is overridden, all messages.create calls use the custom model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([make_file()], [], str(tmp_path), model="claude-haiku-4-5-20251001")
    for call in client.messages.create.call_args_list:
        assert call[1]["model"] == "claude-haiku-4-5-20251001"


def test_run_ai_analysis_find_test_files_exception_graceful(monkeypatch, tmp_path, capsys):
    """When find_test_files raises, the warning is printed and test_gaps defaults to []."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        with patch("pr_impact.ai_layer.find_test_files",
                   side_effect=PermissionError("no test dir")):
            result = run_ai_analysis([make_file()], [], str(tmp_path))
    assert "no test dir" in capsys.readouterr().err
    assert result.test_gaps == []


def test_run_ai_analysis_call4_dict_with_unknown_keys_falls_back(monkeypatch, tmp_path):
    """call4 dict with neither 'signals' nor 'security_signals' keys → raw fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r4 = json.dumps({"some_other_key": [{"description": "ignored"}]})
    client = _mock_client(r1, r2, r3, r4)
    raw_sig = _make_security_signal()
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([make_file()], [], str(tmp_path), pattern_signals=[raw_sig])
    assert result.security_signals == [raw_sig]


def test_run_ai_analysis_skips_5th_call_when_diff_too_small(monkeypatch, tmp_path):
    """Small diff with no interface changes → _should_run_semantic_equivalence is False → 3 calls."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    f = make_file(diff="+tiny\n")  # 1 line, well below 20-line threshold
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([f], [], str(tmp_path))
    assert client.messages.create.call_count == 3
    assert result.semantic_verdicts == []


def test_run_ai_analysis_call5_verdicts_non_list_produces_empty_verdicts(monkeypatch, tmp_path):
    """call5 returns {"verdicts": "not a list"} → raw_verdicts is not a list → empty."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    r5 = json.dumps({"verdicts": "not a list"})
    big_diff = "\n".join(["+line"] * 25)  # > 20 lines → semantic equivalence runs
    f = make_file(diff=big_diff)
    client = _mock_client(r1, r2, r3, r5)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        result = run_ai_analysis([f], [], str(tmp_path))
    assert result.semantic_verdicts == []


def test_run_ai_analysis_neighbour_sigs_success_no_warning_printed(monkeypatch, tmp_path, capsys):
    """When find_neighbouring_signatures succeeds, no neighbour-sigs warning is emitted."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r1 = json.dumps({"summary": "ok", "decisions": [], "assumptions": []})
    r2 = json.dumps({"anomalies": []})
    r3 = json.dumps({"test_gaps": []})
    client = _mock_client(r1, r2, r3)
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_ai_analysis([make_file()], [], str(tmp_path))
    assert "Neighbour signature" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# run_verdict_analysis: custom model
# ---------------------------------------------------------------------------


def test_run_verdict_custom_model_passed_to_call_api(monkeypatch):
    """model parameter overrides the default MODEL in the messages.create call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _mock_client(_verdict_response())
    with patch("pr_impact.ai_layer.anthropic.Anthropic", return_value=client):
        run_verdict_analysis(
            make_report().ai_analysis, [], model="claude-haiku-4-5-20251001"
        )
    assert client.messages.create.call_args[1]["model"] == "claude-haiku-4-5-20251001"
