"""Unit tests for pr_impact/classifier.py."""

from pr_impact.classifier import classify_changed_file, get_interface_changes
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


def test_new_file_mutates_changed_symbols_in_place():
    f = make_file(before="", after="def foo(): pass\n")
    returned = classify_changed_file(f)
    assert f.changed_symbols is returned


def test_deleted_file_mutates_changed_symbols_in_place():
    f = make_file(before="def foo(): pass\n", after="")
    returned = classify_changed_file(f)
    assert f.changed_symbols is returned


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
    # Diff only touches body lines, not the def line
    diff = "-    return x\n+    return x * 2\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = [s for s in classify_changed_file(f) if s.name == "foo"]
    # foo is not in the diff tokens, so it's either absent or internal
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


def test_all_excludes_function_from_interface_change():
    """A function not in __all__ should be classified as internal even if signature changes."""
    before = "def foo(x):\n    return x\n"
    after = "__all__ = ['bar']\ndef foo(x, y):\n    return x + y\ndef bar(): pass\n"
    diff = "+__all__ = ['bar']\n-def foo(x):\n+def foo(x, y):\n"
    f = make_file(before=before, after=after, diff=diff)
    symbols = classify_changed_file(f)
    foo_syms = [s for s in symbols if s.name == "foo"]
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
