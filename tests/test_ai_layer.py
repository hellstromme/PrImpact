"""Unit tests for pure helpers in pr_impact/ai_layer.py.

No API calls are made. All tested functions are deterministic string processors.
"""

from pr_impact.ai_layer import _build_diffs_context, _extract_signatures, _parse_json_safe
from pr_impact.models import ChangedFile
from tests.conftest import make_file

# _DIFF_CHAR_LIMIT = 8_000 tokens * 4 chars/token = 32_000 chars
_LIMIT = 32_000


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
