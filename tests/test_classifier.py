"""Unit tests for pr_impact/classifier.py."""

from unittest.mock import patch

from pr_impact.ast_extractor import ASTSymbol
from pr_impact.classifier import (
    _body_changed_symbols,
    _diff_changed_after_lines,
    classify_changed_file,
    get_interface_changes,
)
from pr_impact.models import ChangedSymbol
from tests.helpers import make_file

# ---------------------------------------------------------------------------
# File-level cases
# ---------------------------------------------------------------------------


def test_new_file_returns_new_file_symbol():
    f = make_file(path="new.py", before="", after="def foo(): pass\n")
    symbols = classify_changed_file(f)
    assert len(symbols) == 1
    assert symbols[0].change_type == "new_file"
    assert symbols[0].kind == "file"
    assert symbols[0].name == "new.py"


def test_deleted_file_returns_deleted_file_symbol():
    f = make_file(path="old.py", before="def foo(): pass\n", after="")
    symbols = classify_changed_file(f)
    assert len(symbols) == 1
    assert symbols[0].change_type == "deleted_file"
    assert symbols[0].kind == "file"


def test_both_empty_returns_empty_list():
    f = make_file(before="", after="")
    symbols = classify_changed_file(f)
    assert symbols == []


# ---------------------------------------------------------------------------
# Python: public / private symbol classification
# ---------------------------------------------------------------------------


def test_public_func_signature_change_is_interface_changed():
    before = "def foo(x):\n    return x\n"
    after = "def foo(x, y):\n    return x + y\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "foo")
    assert foo.change_type == "interface_changed"


def test_public_func_removed_is_interface_removed():
    before = "def foo(x):\n    return x\n\ndef keep():\n    pass\n"
    after = "def keep():\n    pass\n"
    diff = "-def foo(x):\n-    return x\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "foo")
    assert foo.change_type == "interface_removed"


def test_public_func_added_is_interface_added():
    before = "def keep():\n    pass\n"
    after = "def keep():\n    pass\n\ndef new_func(x):\n    return x\n"
    diff = "+def new_func(x):\n+    return x\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    new_func = next(s for s in symbols if s.name == "new_func")
    assert new_func.change_type == "interface_added"


def test_private_func_change_is_internal():
    """Regression: private (_-prefixed) signature changes must be 'internal'."""
    before = "def _helper(x):\n    return x\n"
    after = "def _helper(x, y):\n    return x + y\n"
    diff = "-def _helper(x):\n+def _helper(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    helper = next(s for s in symbols if s.name == "_helper")
    assert helper.change_type == "internal"


def test_private_func_removed_is_internal():
    """Regression: private removal must not surface as interface_removed."""
    before = "def _helper(x):\n    return x\n\ndef keep():\n    pass\n"
    after = "def keep():\n    pass\n"
    diff = "-def _helper(x):\n-    return x\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    names = [s.name for s in symbols]
    if "_helper" in names:
        helper = next(s for s in symbols if s.name == "_helper")
        assert helper.change_type == "internal"


def test_body_only_change_is_internal():
    """When only the body changes and the signature is identical, change is internal."""
    before = "def foo(x):\n    return x\n"
    after = "def foo(x):\n    return x * 2\n"
    # Diff only touches body lines, not the def line; hunk header gives us correct line numbers
    diff = "@@ -1,2 +1,2 @@\n def foo(x):\n-    return x\n+    return x * 2\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = [s for s in classify_changed_file(f) if s.name == "foo"]
    # body-change detection must surface foo even without its name on a diff line
    assert len(symbols) >= 1, "expected foo to be detected via body-change heuristic"
    for s in symbols:
        assert s.change_type == "internal"


def test_untouched_symbol_skipped():
    """A function whose name doesn't appear in the diff is not reported."""
    before = "def foo(x):\n    return x\n\ndef bar(y):\n    return y\n"
    after = "def foo(x, z):\n    return x + z\n\ndef bar(y):\n    return y\n"
    # Diff only touches foo, not bar
    diff = "-def foo(x):\n+def foo(x, z):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    names = [s.name for s in symbols if s.kind not in ("import",)]
    assert "bar" not in names


def test_async_def_signature_change_is_interface_changed():
    before = "async def fetch(url):\n    pass\n"
    after = "async def fetch(url, timeout=30):\n    pass\n"
    diff = "-async def fetch(url):\n+async def fetch(url, timeout=30):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    fetch = next(s for s in symbols if s.name == "fetch")
    assert fetch.change_type == "interface_changed"


def test_method_in_class_not_surfaced_as_top_level():
    """Methods (indented defs) are not extracted as top-level symbols."""
    before = "class MyClass:\n    def method(self, x):\n        return x\n"
    after = "class MyClass:\n    def method(self, x, y):\n        return x + y\n"
    diff = "-    def method(self, x):\n+    def method(self, x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    assert "method" not in [s.name for s in symbols]


# ---------------------------------------------------------------------------
# Python: __all__ gating
# ---------------------------------------------------------------------------


def test_previously_exported_function_removed_from_all_is_interface_changed():
    """A function that was exported before (no __all__) and whose signature changes is
    interface_changed even if __all__ is introduced without it.  The OR logic is correct:
    callers that depended on the previously-public foo will break."""
    before = "def foo(x):\n    return x\n"
    after = "__all__ = ['bar']\ndef foo(x, y):\n    return x + y\ndef bar(): pass\n"
    diff = "+__all__ = ['bar']\n-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo_syms = [s for s in symbols if s.name == "foo"]
    assert foo_syms, "expected foo to appear as a changed symbol"
    for s in foo_syms:
        assert s.change_type == "interface_changed"


def test_function_never_exported_via_all_is_internal():
    """A function absent from __all__ in both before and after is always internal."""
    before = "__all__ = ['bar']\ndef foo(x):\n    return x\ndef bar(): pass\n"
    after = "__all__ = ['bar']\ndef foo(x, y):\n    return x + y\ndef bar(): pass\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo_syms = [s for s in symbols if s.name == "foo"]
    assert foo_syms, "expected foo to appear as a changed symbol"
    for s in foo_syms:
        assert s.change_type == "internal"


def test_all_includes_function_as_interface_change():
    """A function listed in __all__ is exported and its change is interface_changed."""
    before = "__all__ = ['foo']\ndef foo(x):\n    return x\n"
    after = "__all__ = ['foo']\ndef foo(x, y):\n    return x + y\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "foo")
    assert foo.change_type == "interface_changed"


def test_no_all_means_non_underscore_names_are_exported():
    before = "def foo(x):\n    return x\n"
    after = "def foo(x, y):\n    return x + y\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "foo")
    assert foo.change_type == "interface_changed"


# ---------------------------------------------------------------------------
# Python: class vs function kind
# ---------------------------------------------------------------------------


def test_class_kind_detected():
    before = "class Foo:\n    pass\n"
    after = "class Foo(Base):\n    pass\n"
    diff = "-class Foo:\n+class Foo(Base):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "Foo")
    assert foo.kind == "class"


def test_function_kind_detected():
    before = "def bar(x):\n    return x\n"
    after = "def bar(x, y):\n    return x + y\n"
    diff = "-def bar(x):\n+def bar(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    bar = next(s for s in symbols if s.name == "bar")
    assert bar.kind == "function"


# ---------------------------------------------------------------------------
# TypeScript classification
# ---------------------------------------------------------------------------


def test_ts_exported_function_change_is_interface_changed():
    before = "export function foo(x: number): string { return x.toString(); }\n"
    after = "export function foo(x: string): string { return x; }\n"
    diff = "-export function foo(x: number): string {\n+export function foo(x: string): string {\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "foo")
    assert foo.change_type == "interface_changed"


def test_ts_non_exported_function_change_is_internal():
    before = "function foo(x: number): string { return x.toString(); }\n"
    after = "function foo(x: string): string { return x; }\n"
    diff = "-function foo(x: number): string {\n+function foo(x: string): string {\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo_syms = [s for s in symbols if s.name == "foo"]
    for s in foo_syms:
        assert s.change_type == "internal"


def test_ts_exported_class_change_is_interface_changed():
    before = "export class Bar {}\n"
    after = "export class Bar extends Base {}\n"
    diff = "-export class Bar {}\n+export class Bar extends Base {}\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    bar = next(s for s in symbols if s.name == "Bar")
    assert bar.change_type == "interface_changed"
    assert bar.kind == "class"


def test_ts_abstract_class_kind_detected():
    """'abstract class Foo' (no export) must be detected as kind='class'."""
    before = "abstract class Foo {}\n"
    after = "abstract class Foo { bar(): void {} }\n"
    diff = "-abstract class Foo {}\n+abstract class Foo { bar(): void {} }\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "Foo")
    assert foo.kind == "class"


def test_ts_exported_abstract_class_kind_detected():
    """'export abstract class Foo' must be detected as kind='class'."""
    before = "export abstract class Foo {}\n"
    after = "export abstract class Foo extends Base {}\n"
    diff = "-export abstract class Foo {}\n+export abstract class Foo extends Base {}\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo = next(s for s in symbols if s.name == "Foo")
    assert foo.kind == "class"
    assert foo.change_type == "interface_changed"


def test_mid_word_class_not_detected_as_class_kind():
    """A name containing 'class' mid-word (e.g. 'declassified') must not produce kind='class'."""
    before = "def declassified(x):\n    return x\n"
    after = "def declassified(x, y):\n    return x + y\n"
    diff = "-def declassified(x):\n+def declassified(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    sym = next(s for s in symbols if s.name == "declassified")
    assert sym.kind == "function"


def test_ts_exported_arrow_function_added_is_interface_added():
    before = "export const other = () => 'hello';\n"
    after = "export const other = () => 'hello';\nexport const fn = (x: number) => x * 2;\n"
    diff = "+export const fn = (x: number) => x * 2;\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    fn = next((s for s in symbols if s.name == "fn"), None)
    assert fn is not None
    assert fn.change_type == "interface_added"


# ---------------------------------------------------------------------------
# Import / dependency tracking
# ---------------------------------------------------------------------------


def test_import_added_python():
    before = "def foo(): pass\n"
    after = "import os\ndef foo(): pass\n"
    diff = "+import os\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    imp = next(s for s in symbols if s.change_type == "dependency_added")
    assert "import os" in imp.name
    assert imp.kind == "import"


def test_import_removed_python():
    before = "import os\ndef foo(): pass\n"
    after = "def foo(): pass\n"
    diff = "-import os\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    imp = next(s for s in symbols if s.change_type == "dependency_removed")
    assert "import os" in imp.name


def test_import_added_typescript():
    before = "export function foo() {}\n"
    after = "import { bar } from './bar';\nexport function foo() {}\n"
    diff = "+import { bar } from './bar';\n"
    f = make_file(language="typescript", before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    imp = next(s for s in symbols if s.change_type == "dependency_added")
    assert "import" in imp.name


def test_unchanged_import_not_emitted():
    before = "import os\ndef foo(x): return x\n"
    after = "import os\ndef foo(x, y): return x + y\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    dep_changes = [
        s for s in symbols if s.change_type in ("dependency_added", "dependency_removed")
    ]
    assert dep_changes == []


# ---------------------------------------------------------------------------
# get_interface_changes
# ---------------------------------------------------------------------------


def _make_file_with_symbols(path, symbols):
    f = make_file(path=path)
    f.changed_symbols = symbols
    return f


def test_interface_changed_symbol_included():
    sym = ChangedSymbol(
        name="foo",
        kind="function",
        change_type="interface_changed",
        signature_before="def foo(x)",
        signature_after="def foo(x, y)",
    )
    f = _make_file_with_symbols("a.py", [sym])
    result = get_interface_changes([f], {})
    assert len(result) == 1
    assert result[0].symbol == "foo"


def test_interface_removed_symbol_included():
    sym = ChangedSymbol(
        name="bar",
        kind="function",
        change_type="interface_removed",
        signature_before="def bar()",
        signature_after=None,
    )
    f = _make_file_with_symbols("a.py", [sym])
    result = get_interface_changes([f], {})
    assert any(r.symbol == "bar" for r in result)


def test_interface_added_excluded():
    sym = ChangedSymbol(
        name="new_fn",
        kind="function",
        change_type="interface_added",
        signature_before=None,
        signature_after="def new_fn()",
    )
    f = _make_file_with_symbols("a.py", [sym])
    result = get_interface_changes([f], {})
    assert result == []


def test_internal_symbol_excluded():
    sym = ChangedSymbol(
        name="impl",
        kind="function",
        change_type="internal",
        signature_before="def impl()",
        signature_after="def impl(x)",
    )
    f = _make_file_with_symbols("a.py", [sym])
    result = get_interface_changes([f], {})
    assert result == []


def test_callers_populated_from_reverse_graph():
    sym = ChangedSymbol(
        name="foo",
        kind="function",
        change_type="interface_changed",
        signature_before="def foo()",
        signature_after="def foo(x)",
    )
    f = _make_file_with_symbols("a.py", [sym])
    reverse_graph = {"a.py": ["b.py", "c.py"]}
    result = get_interface_changes([f], reverse_graph)
    assert result[0].callers == ["b.py", "c.py"]


def test_no_callers_when_file_not_in_reverse_graph():
    sym = ChangedSymbol(
        name="foo",
        kind="function",
        change_type="interface_changed",
        signature_before="def foo()",
        signature_after="def foo(x)",
    )
    f = _make_file_with_symbols("a.py", [sym])
    result = get_interface_changes([f], {})
    assert result[0].callers == []


def test_empty_changed_files_returns_empty():
    assert get_interface_changes([], {}) == []


def test_multiple_symbols_across_multiple_files():
    sym_a = ChangedSymbol(
        name="foo",
        kind="function",
        change_type="interface_changed",
        signature_before="def foo()",
        signature_after="def foo(x)",
    )
    sym_b = ChangedSymbol(
        name="Bar",
        kind="class",
        change_type="interface_removed",
        signature_before="class Bar",
        signature_after=None,
    )
    fa = _make_file_with_symbols("a.py", [sym_a])
    fb = _make_file_with_symbols("b.py", [sym_b])
    result = get_interface_changes([fa, fb], {})
    assert len(result) == 2
    files = {r.file for r in result}
    assert files == {"a.py", "b.py"}


# ---------------------------------------------------------------------------
# TypeScript class classification with prefixed keywords
# ---------------------------------------------------------------------------


def test_export_class_classify_kind_is_class():
    # Class signature changes in an existing file — before is non-empty to avoid new_file path
    before = "export class Foo {\n}\n"
    after = "export class Foo extends Base {\n}\n"
    diff = "-export class Foo {\n+export class Foo extends Base {\n"
    f = make_file(path="mod.ts", language="typescript", diff=diff, before=before, after=after)
    symbols = classify_changed_file(f)
    class_syms = [s for s in symbols if s.name == "Foo"]
    assert len(class_syms) == 1
    assert class_syms[0].kind == "class"


def test_abstract_class_classify_kind_is_class():
    # Change the class signature so "Bar" appears in the diff
    before = "abstract class Bar {\n  abstract method(): void;\n}\n"
    after = "abstract class Bar<T> {\n  abstract method(): void;\n}\n"
    diff = "-abstract class Bar {\n+abstract class Bar<T> {\n"
    f = make_file(path="mod.ts", language="typescript", diff=diff, before=before, after=after)
    symbols = classify_changed_file(f)
    class_syms = [s for s in symbols if s.name == "Bar"]
    assert len(class_syms) == 1
    assert class_syms[0].kind == "class"


# --- AST-first / regex-fallback path tests ---


def test_ast_path_python_signature_change_populates_fields():
    """When extract_symbols returns non-None for both before/after, the AST path is used."""
    sym_before = ASTSymbol(
        name="login",
        kind="function",
        is_exported=True,
        signature="def login():",
        params=[],
        decorators=[],
        return_type=None,
    )
    sym_after = ASTSymbol(
        name="login",
        kind="function",
        is_exported=True,
        signature="def login(user: str):",
        params=["user: str"],
        decorators=["@require_auth"],
        return_type="bool",
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[sym_before], [sym_after]]
        f = make_file(
            path="src/auth.py",
            language="python",
            before="def login(): pass",
            after="def login(user: str): pass",
            diff="-def login(): pass\n+def login(user: str): pass\n",
        )
        symbols = classify_changed_file(f)

    login_syms = [s for s in symbols if s.name == "login"]
    assert len(login_syms) == 1
    sym = login_syms[0]
    assert sym.change_type == "interface_changed"
    assert sym.signature_before == "def login():"
    assert sym.signature_after == "def login(user: str):"
    assert sym.params == ["user: str"]
    assert sym.decorators == ["@require_auth"]
    assert sym.return_type == "bool"


def test_ast_path_python_added_function():
    """AST path detects a newly added function as interface_added."""
    sym_after = ASTSymbol(
        name="signup",
        kind="function",
        is_exported=True,
        signature="def signup(email: str):",
        params=["email: str"],
        decorators=[],
        return_type=None,
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[], [sym_after]]
        f = make_file(
            path="src/auth.py",
            language="python",
            before="# empty",
            after="def signup(email: str): pass",
            diff="+def signup(email: str): pass\n",
        )
        symbols = classify_changed_file(f)

    signup_syms = [s for s in symbols if s.name == "signup"]
    assert len(signup_syms) == 1
    assert signup_syms[0].change_type == "interface_added"


def test_ast_path_python_removed_function():
    """AST path detects a removed function as interface_removed."""
    sym_before = ASTSymbol(
        name="logout",
        kind="function",
        is_exported=True,
        signature="def logout():",
        params=[],
        decorators=[],
        return_type=None,
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[sym_before], []]
        f = make_file(
            path="src/auth.py",
            language="python",
            before="def logout(): pass",
            after="# empty",
            diff="-def logout(): pass\n",
        )
        symbols = classify_changed_file(f)

    logout_syms = [s for s in symbols if s.name == "logout"]
    assert len(logout_syms) == 1
    assert logout_syms[0].change_type == "interface_removed"


def test_regex_fallback_when_extract_symbols_returns_none():
    """When extract_symbols returns None, classifier falls back to regex and still works."""
    with patch("pr_impact.classifier.extract_symbols", return_value=None):
        f = make_file(
            path="src/auth.py",
            language="python",
            before="def login(): pass\n",
            after="def login(user): pass\n",
            diff="-def login(): pass\n+def login(user): pass\n",
        )
        symbols = classify_changed_file(f)

    assert isinstance(symbols, list)
    login_syms = [s for s in symbols if s.name == "login"]
    assert len(login_syms) == 1
    assert login_syms[0].change_type == "interface_changed"
    # Regex fallback should not populate AST-only fields
    assert login_syms[0].params == []
    assert login_syms[0].decorators == []
    assert login_syms[0].return_type is None


def test_regex_fallback_when_only_before_returns_none():
    """When extract_symbols returns None for before but not after, regex fallback is used."""
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [None, [ASTSymbol(name="login", kind="function", signature="def login(user):")]]
        f = make_file(
            path="src/auth.py",
            language="python",
            before="def login(): pass\n",
            after="def login(user): pass\n",
            diff="-def login(): pass\n+def login(user): pass\n",
        )
        symbols = classify_changed_file(f)

    assert isinstance(symbols, list)
    login_syms = [s for s in symbols if s.name == "login"]
    assert len(login_syms) == 1
    # Falls back to regex because both must be non-None for AST path
    assert login_syms[0].change_type == "interface_changed"


def test_ast_path_typescript_signature_change():
    """AST path works for TypeScript — exported function with changed params."""
    sym_before = ASTSymbol(
        name="fetchUser",
        kind="function",
        is_exported=True,
        signature="export function fetchUser(id: number): Promise<User>",
        params=["id: number"],
        decorators=[],
        return_type="Promise<User>",
    )
    sym_after = ASTSymbol(
        name="fetchUser",
        kind="function",
        is_exported=True,
        signature="export function fetchUser(id: string): Promise<User>",
        params=["id: string"],
        decorators=[],
        return_type="Promise<User>",
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[sym_before], [sym_after]]
        f = make_file(
            path="src/api.ts",
            language="typescript",
            before="export function fetchUser(id: number): Promise<User> { }",
            after="export function fetchUser(id: string): Promise<User> { }",
            diff="-export function fetchUser(id: number): Promise<User> {\n+export function fetchUser(id: string): Promise<User> {\n",
        )
        symbols = classify_changed_file(f)

    fetch_syms = [s for s in symbols if s.name == "fetchUser"]
    assert len(fetch_syms) == 1
    sym = fetch_syms[0]
    assert sym.change_type == "interface_changed"
    assert sym.signature_before == "export function fetchUser(id: number): Promise<User>"
    assert sym.signature_after == "export function fetchUser(id: string): Promise<User>"
    assert sym.params == ["id: string"]
    assert sym.return_type == "Promise<User>"


def test_ast_path_typescript_class_change():
    """AST path correctly identifies TypeScript class changes."""
    sym_before = ASTSymbol(
        name="UserService",
        kind="class",
        is_exported=True,
        signature="export class UserService",
        params=[],
        decorators=[],
        return_type=None,
    )
    sym_after = ASTSymbol(
        name="UserService",
        kind="class",
        is_exported=True,
        signature="export class UserService extends BaseService",
        params=[],
        decorators=[],
        return_type=None,
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[sym_before], [sym_after]]
        f = make_file(
            path="src/service.ts",
            language="typescript",
            before="export class UserService {}",
            after="export class UserService extends BaseService {}",
            diff="-export class UserService {}\n+export class UserService extends BaseService {}\n",
        )
        symbols = classify_changed_file(f)

    svc_syms = [s for s in symbols if s.name == "UserService"]
    assert len(svc_syms) == 1
    assert svc_syms[0].kind == "class"
    assert svc_syms[0].change_type == "interface_changed"


def test_regex_fallback_typescript_when_extract_symbols_returns_none():
    """TypeScript regex fallback works when extract_symbols returns None."""
    with patch("pr_impact.classifier.extract_symbols", return_value=None):
        f = make_file(
            path="src/api.ts",
            language="typescript",
            before="export function greet(name: string): string { return name; }\n",
            after="export function greet(name: string, title: string): string { return title + name; }\n",
            diff="-export function greet(name: string): string {\n+export function greet(name: string, title: string): string {\n",
        )
        symbols = classify_changed_file(f)

    assert isinstance(symbols, list)
    greet_syms = [s for s in symbols if s.name == "greet"]
    assert len(greet_syms) == 1
    assert greet_syms[0].change_type == "interface_changed"


# --- Unsupported languages return empty symbols ---


def test_csharp_returns_empty_symbols():
    """C# files are not processed for symbol extraction; returns empty list."""
    f = make_file(
        path="src/Service.cs",
        language="csharp",
        before="public void Login() {}",
        after="public void Login(string user) {}",
        diff="-public void Login() {}\n+public void Login(string user) {}\n",
    )
    symbols = classify_changed_file(f)
    assert symbols == []


def test_java_returns_empty_symbols():
    """Java files are not processed for symbol extraction; returns empty list."""
    f = make_file(
        path="src/Service.java",
        language="java",
        before="public void login() {}",
        after="public void login(String user) {}",
        diff="-public void login() {}\n+public void login(String user) {}\n",
    )
    symbols = classify_changed_file(f)
    assert symbols == []


def test_go_returns_empty_symbols():
    """Go files are not processed for symbol extraction; returns empty list."""
    f = make_file(
        path="src/service.go",
        language="go",
        before="func Login() {}",
        after="func Login(user string) {}",
        diff="-func Login() {}\n+func Login(user string) {}\n",
    )
    symbols = classify_changed_file(f)
    assert symbols == []


def test_ruby_returns_empty_symbols():
    """Ruby files are not processed for symbol extraction; returns empty list."""
    f = make_file(
        path="src/service.rb",
        language="ruby",
        before="def login\nend",
        after="def login(user)\nend",
        diff="-def login\n+def login(user)\n",
    )
    symbols = classify_changed_file(f)
    assert symbols == []


def test_unknown_language_returns_empty_symbols():
    """An unrecognized language returns empty symbols without raising."""
    f = make_file(
        path="src/main.rs",
        language="rust",
        before="fn main() {}",
        after="fn main() { println!(); }",
        diff="-fn main() {}\n+fn main() { println!(); }\n",
    )
    symbols = classify_changed_file(f)
    assert symbols == []


def test_ast_path_body_only_change_is_internal():
    """AST path: when signatures are identical, change_type is 'internal'."""
    sym = ASTSymbol(
        name="compute",
        kind="function",
        is_exported=True,
        signature="def compute(x):",
        params=["x"],
        decorators=[],
        return_type=None,
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        # Both before and after have the same symbol with the same signature
        mock_ast.side_effect = [[sym], [sym]]
        f = make_file(
            path="src/math.py",
            language="python",
            before="def compute(x):\n    return x\n",
            after="def compute(x):\n    return x * 2\n",
            # Include the def line so "compute" appears in the diff token set
            diff="-def compute(x):\n-    return x\n+def compute(x):\n+    return x * 2\n",
        )
        symbols = classify_changed_file(f)

    compute_syms = [s for s in symbols if s.name == "compute"]
    assert len(compute_syms) >= 1, "expected compute to appear as a changed symbol"
    for s in compute_syms:
        assert s.change_type == "internal"


def test_ast_path_private_function_is_internal():
    """AST path: private Python function (_-prefixed) with signature change is internal."""
    sym_before = ASTSymbol(
        name="_helper",
        kind="function",
        is_exported=False,
        signature="def _helper(x):",
        params=["x"],
        decorators=[],
        return_type=None,
    )
    sym_after = ASTSymbol(
        name="_helper",
        kind="function",
        is_exported=False,
        signature="def _helper(x, y):",
        params=["x", "y"],
        decorators=[],
        return_type=None,
    )
    with patch("pr_impact.classifier.extract_symbols") as mock_ast:
        mock_ast.side_effect = [[sym_before], [sym_after]]
        f = make_file(
            path="src/utils.py",
            language="python",
            before="def _helper(x): pass",
            after="def _helper(x, y): pass",
            diff="-def _helper(x): pass\n+def _helper(x, y): pass\n",
        )
        symbols = classify_changed_file(f)

    helper_syms = [s for s in symbols if s.name == "_helper"]
    assert len(helper_syms) == 1
    assert helper_syms[0].change_type == "internal"


# ---------------------------------------------------------------------------
# _diff_changed_after_lines
# ---------------------------------------------------------------------------


def test_diff_changed_after_lines_single_hunk():
    diff = "@@ -1,3 +1,3 @@\n def foo(x):\n-    return x\n+    return x * 2\n"
    result = _diff_changed_after_lines(diff)
    # context line advances to 1, removed skips, added reaches 2
    assert result == {2}


def test_diff_changed_after_lines_multiple_hunks():
    diff = (
        "@@ -1,2 +1,2 @@\n def foo(x):\n-    return x\n+    return x * 2\n"
        "@@ -10,2 +10,2 @@\n def bar(y):\n-    return y\n+    return y + 1\n"
    )
    result = _diff_changed_after_lines(diff)
    assert 2 in result
    assert 11 in result


def test_diff_changed_after_lines_only_removed_lines_returns_empty():
    diff = "@@ -1,2 +1,1 @@\n context\n-removed\n"
    assert _diff_changed_after_lines(diff) == set()


def test_diff_changed_after_lines_empty_diff():
    assert _diff_changed_after_lines("") == set()


def test_diff_changed_after_lines_no_hunk_header_does_not_crash():
    diff = "-old line\n+new line\n"
    result = _diff_changed_after_lines(diff)
    assert isinstance(result, set)
    assert 1 in result  # counts from 0 when no header present


def test_diff_changed_after_lines_excludes_triple_plus_from_result():
    # +++ file-header lines must not be added to the changed-line set
    diff = "@@ -0,0 +1,2 @@\n+++sentinel\n+real_content\n"
    result = _diff_changed_after_lines(diff)
    assert 1 not in result   # +++ line not counted as a changed line
    assert 2 in result        # real content line is counted


def test_diff_changed_after_lines_pure_additions():
    diff = "@@ -0,0 +1,3 @@\n+line_one\n+line_two\n+line_three\n"
    result = _diff_changed_after_lines(diff)
    assert result == {1, 2, 3}


# ---------------------------------------------------------------------------
# _body_changed_symbols
# ---------------------------------------------------------------------------


def test_body_changed_symbols_basic_detection():
    syms = [
        ASTSymbol(name="foo", kind="function", line=1),
        ASTSymbol(name="bar", kind="function", line=10),
    ]
    result = _body_changed_symbols(syms, {5})
    assert "foo" in result
    assert "bar" not in result


def test_body_changed_symbols_on_start_line():
    syms = [ASTSymbol(name="foo", kind="function", line=3)]
    assert "foo" in _body_changed_symbols(syms, {3})


def test_body_changed_symbols_last_symbol_uses_large_sentinel():
    syms = [ASTSymbol(name="only", kind="function", line=5)]
    assert "only" in _body_changed_symbols(syms, {9_999_999})


def test_body_changed_symbols_empty_ast_returns_empty():
    assert _body_changed_symbols([], {1, 2, 3}) == set()


def test_body_changed_symbols_empty_changed_lines_returns_empty():
    syms = [ASTSymbol(name="foo", kind="function", line=1)]
    assert _body_changed_symbols(syms, set()) == set()


def test_body_changed_symbols_qualified_name_with_container():
    syms = [ASTSymbol(name="method", kind="method", line=5, container="MyClass")]
    result = _body_changed_symbols(syms, {7})
    assert "MyClass.method" in result
    assert "method" not in result


def test_body_changed_symbols_unqualified_when_no_container():
    syms = [ASTSymbol(name="standalone", kind="function", line=1, container="")]
    assert "standalone" in _body_changed_symbols(syms, {2})


def test_body_changed_symbols_sorts_unsorted_input():
    # Pass symbols in reverse order to verify sorting happens
    syms = [
        ASTSymbol(name="second", kind="function", line=20),
        ASTSymbol(name="first", kind="function", line=1),
    ]
    result = _body_changed_symbols(syms, {15})  # line 15: in first's body (1–19)
    assert "first" in result
    assert "second" not in result


def test_body_changed_symbols_boundary_belongs_to_correct_symbol():
    syms = [
        ASTSymbol(name="foo", kind="function", line=1),
        ASTSymbol(name="bar", kind="function", line=10),
    ]
    # Line 9 = last line of foo's body (bar.line - 1)
    assert "foo" in _body_changed_symbols(syms, {9})
    assert "bar" not in _body_changed_symbols(syms, {9})
    # Line 10 = bar's own start line
    assert "bar" in _body_changed_symbols(syms, {10})


# ---------------------------------------------------------------------------
# classify_changed_file — _body_touched OR logic integration
# ---------------------------------------------------------------------------


def test_classify_body_change_detected_when_name_absent_from_diff():
    """Function body change with no @@ header falls back to touched-name heuristic."""
    before = "def compute(x):\n    return x\n\ndef unrelated():\n    pass\n"
    after = "def compute(x):\n    return x * 2\n\ndef unrelated():\n    pass\n"
    diff = "@@ -1,2 +1,2 @@\n def compute(x):\n-    return x\n+    return x * 2\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    names = [s.name for s in symbols if s.kind != "import"]
    assert "compute" in names
    assert "unrelated" not in names


def test_classify_body_change_is_internal_not_interface_changed():
    before = "def process(x):\n    return x\n"
    after = "def process(x):\n    return x + 1\n"
    diff = "@@ -1,2 +1,2 @@\n def process(x):\n-    return x\n+    return x + 1\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = [s for s in classify_changed_file(f) if s.name == "process"]
    assert symbols
    assert symbols[0].change_type == "internal"


def test_classify_malformed_diff_no_hunk_header_still_detects_via_touched():
    """Signature change detected via touched tokens even without @@ header."""
    before = "def foo(x):\n    return x\n"
    after = "def foo(x, y):\n    return x + y\n"
    diff = "-def foo(x):\n+def foo(x, y):\n"  # no @@ header
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo_syms = [s for s in symbols if s.name == "foo"]
    assert foo_syms, "signature change via touched tokens should still be detected"
    assert foo_syms[0].change_type == "interface_changed"


def test_classify_or_logic_no_duplicate_when_in_both_touched_and_body_touched():
    """A symbol in both touched and body_touched must appear exactly once."""
    before = "def foo(x):\n    y = foo(x)\n    return y\n"
    after = "def foo(x):\n    y = foo(x) * 2\n    return y\n"
    # foo references itself on the changed line → present in both sets
    diff = "@@ -1,3 +1,3 @@\n def foo(x):\n-    y = foo(x)\n+    y = foo(x) * 2\n     return y\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = [s for s in classify_changed_file(f) if s.name == "foo"]
    assert len(symbols) == 1
