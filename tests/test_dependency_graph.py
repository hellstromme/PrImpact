"""Unit tests for pr_impact/dependency_graph.py."""

import git

from pr_impact.dependency_graph import (
    _extract_imports,
    _list_repo_files,
    _read_file,
    _resolve_csharp_import,
    _resolve_js_import,
    _resolve_python_module,
    build_import_graph,
    get_blast_radius,
    get_imported_symbols,
)

# ---------------------------------------------------------------------------
# _resolve_python_module: absolute imports
# ---------------------------------------------------------------------------


def test_absolute_module_resolves_to_py_file():
    all_files = {"pr_impact/models.py"}
    assert (
        _resolve_python_module("pr_impact.models", "pr_impact/cli.py", all_files)
        == "pr_impact/models.py"
    )


def test_absolute_module_resolves_to_init():
    all_files = {"pr_impact/__init__.py"}
    assert (
        _resolve_python_module("pr_impact", "other/file.py", all_files) == "pr_impact/__init__.py"
    )


def test_absolute_nested_module():
    all_files = {"a/b/c.py"}
    assert _resolve_python_module("a.b.c", "other.py", all_files) == "a/b/c.py"


def test_absolute_module_not_in_all_files_returns_none():
    assert _resolve_python_module("missing.module", "other.py", set()) is None


def test_absolute_module_requires_forward_slash_keys():
    """all_files keys must use forward slashes; backslash keys never match (documents assumption)."""
    backslash_files = {"pr_impact\\models.py"}  # Windows-style — should NOT match
    assert _resolve_python_module("pr_impact.models", "pr_impact/cli.py", backslash_files) is None
    forward_slash_files = {"pr_impact/models.py"}  # POSIX-style — must match
    assert (
        _resolve_python_module("pr_impact.models", "pr_impact/cli.py", forward_slash_files)
        == "pr_impact/models.py"
    )


def test_absolute_prefers_file_over_init():
    all_files = {"pr_impact/models.py", "pr_impact/models/__init__.py"}
    result = _resolve_python_module("pr_impact.models", "other.py", all_files)
    assert result == "pr_impact/models.py"


# ---------------------------------------------------------------------------
# _resolve_python_module: relative imports
# ---------------------------------------------------------------------------


def test_single_dot_with_name_resolves_sibling():
    all_files = {"pr_impact/models.py"}
    result = _resolve_python_module(".models", "pr_impact/classifier.py", all_files)
    assert result == "pr_impact/models.py"


def test_single_dot_bare_resolves_to_init():
    all_files = {"pr_impact/__init__.py"}
    result = _resolve_python_module(".", "pr_impact/classifier.py", all_files)
    assert result == "pr_impact/__init__.py"


def test_double_dot_relative_goes_up_one_level():
    all_files = {"pr_impact/utils.py"}
    result = _resolve_python_module("..utils", "pr_impact/sub/foo.py", all_files)
    assert result == "pr_impact/utils.py"


def test_relative_resolves_to_package_init():
    all_files = {"pr_impact/sub/__init__.py"}
    result = _resolve_python_module(".sub", "pr_impact/classifier.py", all_files)
    assert result == "pr_impact/sub/__init__.py"


def test_relative_not_found_returns_none():
    result = _resolve_python_module(".missing", "pr_impact/classifier.py", set())
    assert result is None


# ---------------------------------------------------------------------------
# _resolve_js_import: extension probing
# ---------------------------------------------------------------------------


def test_external_package_returns_none():
    assert _resolve_js_import("react", "src/app.ts", set()) is None
    assert _resolve_js_import("lodash/merge", "src/app.ts", set()) is None


def test_exact_path_match_returned():
    all_files = {"src/utils.ts"}
    assert _resolve_js_import("./utils.ts", "src/app.ts", all_files) == "src/utils.ts"


def test_probes_ts_extension():
    all_files = {"src/utils.ts"}
    assert _resolve_js_import("./utils", "src/app.ts", all_files) == "src/utils.ts"


def test_probes_tsx_extension():
    all_files = {"src/Button.tsx"}
    assert _resolve_js_import("./Button", "src/app.ts", all_files) == "src/Button.tsx"


def test_probes_js_extension():
    all_files = {"src/helpers.js"}
    assert _resolve_js_import("./helpers", "src/app.ts", all_files) == "src/helpers.js"


def test_index_fallback_ts():
    all_files = {"src/components/Button/index.ts"}
    result = _resolve_js_import("./components/Button", "src/app.ts", all_files)
    assert result == "src/components/Button/index.ts"


def test_index_fallback_tsx():
    all_files = {"src/components/Input/index.tsx"}
    result = _resolve_js_import("./components/Input", "src/app.ts", all_files)
    assert result == "src/components/Input/index.tsx"


def test_parent_directory_traversal():
    all_files = {"shared/types.ts"}
    result = _resolve_js_import("../shared/types", "src/app.ts", all_files)
    assert result == "shared/types.ts"


def test_no_match_returns_none():
    assert _resolve_js_import("./nonexistent", "src/app.ts", set()) is None


# ---------------------------------------------------------------------------
# get_blast_radius: BFS behaviour
# ---------------------------------------------------------------------------


def test_direct_dependent_at_distance_1():
    reverse_graph = {"a.py": ["b.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    assert len(entries) == 1
    assert entries[0].path == "b.py"
    assert entries[0].distance == 1


def test_two_hop_dependent_at_distance_2():
    reverse_graph = {"a.py": ["b.py"], "b.py": ["c.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    paths = {e.path: e.distance for e in entries}
    assert paths["b.py"] == 1
    assert paths["c.py"] == 2


def test_changed_files_excluded_from_output():
    reverse_graph = {"a.py": ["b.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    assert all(e.path != "a.py" for e in entries)


def test_max_depth_cap():
    # Chain: a → b → c → d → e (4 hops)
    reverse_graph = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": ["d.py"], "d.py": ["e.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=2, repo_path="")
    paths = {e.path for e in entries}
    assert "b.py" in paths
    assert "c.py" in paths
    assert "d.py" not in paths
    assert "e.py" not in paths


def test_default_max_depth_is_3():
    reverse_graph = {
        "a.py": ["b.py"],
        "b.py": ["c.py"],
        "c.py": ["d.py"],
        "d.py": ["e.py"],
    }
    entries = get_blast_radius(reverse_graph, ["a.py"], repo_path="")
    paths = {e.path for e in entries}
    assert "d.py" in paths  # distance 3 included
    assert "e.py" not in paths  # distance 4 excluded


def test_shortest_path_wins_diamond():
    # Diamond: a → b → d, a → c → d
    reverse_graph = {"a.py": ["b.py", "c.py"], "b.py": ["d.py"], "c.py": ["d.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    d_entry = next(e for e in entries if e.path == "d.py")
    assert d_entry.distance == 2  # not 3 via a→b→d or a→c→d both give 2


def test_no_dependents_returns_empty():
    reverse_graph = {}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    assert entries == []


def test_empty_changed_files_returns_empty():
    reverse_graph = {"a.py": ["b.py"]}
    entries = get_blast_radius(reverse_graph, [], max_depth=3, repo_path="")
    assert entries == []


def test_result_sorted_by_distance_then_path():
    reverse_graph = {"a.py": ["c.py", "b.py"], "b.py": ["e.py", "d.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    distances = [e.distance for e in entries]
    # Must be non-decreasing
    assert distances == sorted(distances)
    # Within same distance, sorted by path
    dist1 = [e.path for e in entries if e.distance == 1]
    assert dist1 == sorted(dist1)


def test_disconnected_subgraph_not_included():
    # x.py → y.py is disconnected from changed file a.py
    reverse_graph = {"a.py": ["b.py"], "x.py": ["y.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    paths = {e.path for e in entries}
    assert "b.py" in paths
    assert "y.py" not in paths
    assert "x.py" not in paths


def test_cycle_in_graph_terminates():
    # a → b → a (cycle)
    reverse_graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py"], max_depth=3, repo_path="")
    # Should terminate and return b.py at distance 1
    paths = {e.path for e in entries}
    assert "b.py" in paths
    assert "a.py" not in paths  # changed file excluded


def test_multiple_changed_files_union_of_dependents():
    reverse_graph = {"a.py": ["x.py"], "b.py": ["y.py"]}
    entries = get_blast_radius(reverse_graph, ["a.py", "b.py"], max_depth=3, repo_path="")
    paths = {e.path for e in entries}
    assert "x.py" in paths
    assert "y.py" in paths


# ---------------------------------------------------------------------------
# _read_file
# ---------------------------------------------------------------------------


def test_read_file_returns_content(tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("hello = 1\n", encoding="utf-8")
    assert _read_file(str(tmp_path), "foo.py") == "hello = 1\n"


def test_read_file_returns_empty_string_on_missing():
    assert _read_file("/nonexistent_dir_xyz", "missing.py") == ""


# ---------------------------------------------------------------------------
# _resolve_csharp_import
# ---------------------------------------------------------------------------


def test_csharp_namespace_found_in_map():
    ns_map = {"MyApp.Services": ["src/services.cs", "src/extra.cs"]}
    result = _resolve_csharp_import("MyApp.Services", ns_map)
    assert result == ["src/services.cs", "src/extra.cs"]


def test_csharp_namespace_not_found_returns_empty():
    assert _resolve_csharp_import("Missing.Namespace", {}) == []


def test_csharp_empty_map_returns_empty():
    assert _resolve_csharp_import("Any.Namespace", {}) == []


# ---------------------------------------------------------------------------
# _extract_imports: JS/TS and C# branches (Python covered by blast-radius tests)
# ---------------------------------------------------------------------------


def test_extract_imports_ts_named_import():
    content = "import { foo } from './utils';\n"
    result = _extract_imports(content, "src/app.ts", "typescript", {"src/utils.ts"})
    assert "src/utils.ts" in result


def test_extract_imports_ts_export_from():
    content = "export { bar } from './helpers';\n"
    result = _extract_imports(content, "src/index.ts", "typescript", {"src/helpers.ts"})
    assert "src/helpers.ts" in result


def test_extract_imports_js_require():
    content = "const utils = require('./lib/utils.js');\n"
    result = _extract_imports(content, "src/app.js", "javascript", {"src/lib/utils.js"})
    assert "src/lib/utils.js" in result


def test_extract_imports_js_plain_import():
    content = "import './polyfill.js';\n"
    result = _extract_imports(content, "src/app.js", "javascript", {"src/polyfill.js"})
    assert "src/polyfill.js" in result


def test_extract_imports_external_package_excluded():
    content = "import React from 'react';\n"
    result = _extract_imports(content, "src/app.ts", "typescript", set())
    assert result == []


def test_extract_imports_csharp_using():
    content = "using MyApp.Models;\nclass Foo {}\n"
    cs_map = {"MyApp.Models": ["src/models.cs"]}
    result = _extract_imports(content, "src/ctrl.cs", "csharp", {"src/models.cs"}, cs_map)
    assert "src/models.cs" in result


def test_extract_imports_csharp_no_map_returns_empty():
    content = "using MyApp.Models;\nclass Foo {}\n"
    result = _extract_imports(content, "src/ctrl.cs", "csharp", set())
    assert result == []


def test_extract_imports_unknown_language_returns_empty():
    content = "some content"
    result = _extract_imports(content, "file.rb", "ruby", set())
    assert result == []


def test_extract_imports_deduplicates():
    content = "import { a } from './mod';\nimport { b } from './mod';\n"
    result = _extract_imports(content, "src/app.ts", "typescript", {"src/mod.ts"})
    assert result.count("src/mod.ts") == 1


# ---------------------------------------------------------------------------
# get_imported_symbols
# ---------------------------------------------------------------------------


def test_get_imported_symbols_empty_path_returns_empty():
    assert get_imported_symbols("", "something.py") == []


def test_get_imported_symbols_empty_imported_from_returns_empty(tmp_path):
    f = tmp_path / "consumer.py"
    f.write_text("from models import foo\n")
    assert get_imported_symbols(str(f), "") == []


def test_get_imported_symbols_missing_file_returns_empty():
    assert get_imported_symbols("/nonexistent/file.py", "models.py") == []


def test_get_imported_symbols_python_from_import(tmp_path):
    f = tmp_path / "consumer.py"
    f.write_text("from models import foo, bar\n")
    result = get_imported_symbols(str(f), "models.py")
    assert "foo" in result
    assert "bar" in result


def test_get_imported_symbols_python_aliased_import(tmp_path):
    f = tmp_path / "consumer.py"
    f.write_text("from models import foo as f, bar\n")
    result = get_imported_symbols(str(f), "models.py")
    assert "foo" in result
    assert "bar" in result


def test_get_imported_symbols_js_named_import(tmp_path):
    f = tmp_path / "consumer.ts"
    f.write_text("import { doThing, helper } from './utils';\n")
    result = get_imported_symbols(str(f), "utils.ts")
    assert "doThing" in result
    assert "helper" in result


def test_get_imported_symbols_csharp_using(tmp_path):
    f = tmp_path / "Controller.cs"
    f.write_text("using MyApp.Models;\nclass Foo {}\n")
    result = get_imported_symbols(str(f), "Models.cs")
    assert "Models" in result


def test_get_imported_symbols_star_import_excluded(tmp_path):
    f = tmp_path / "consumer.py"
    f.write_text("from models import *\n")
    result = get_imported_symbols(str(f), "models.py")
    assert "*" not in result


# ---------------------------------------------------------------------------
# _extract_imports: Python branch (lines 129-136)
# ---------------------------------------------------------------------------


def test_extract_imports_python_absolute_import():
    content = "import pr_impact.models\n"
    all_files = {"pr_impact/models.py"}
    result = _extract_imports(content, "pr_impact/cli.py", "python", all_files)
    assert "pr_impact/models.py" in result


def test_extract_imports_python_from_import():
    content = "from pr_impact.models import Foo\n"
    all_files = {"pr_impact/models.py"}
    result = _extract_imports(content, "pr_impact/cli.py", "python", all_files)
    assert "pr_impact/models.py" in result


def test_extract_imports_python_unresolvable_returns_empty():
    content = "from external_package import something\n"
    result = _extract_imports(content, "src/app.py", "python", set())
    assert result == []


# ---------------------------------------------------------------------------
# _list_repo_files and build_import_graph: git-backed tests
# ---------------------------------------------------------------------------


def _make_git_repo(path):
    """Create a minimal git repo with two tracked Python files."""
    repo = git.Repo.init(str(path))
    actor = git.Actor("test", "test@example.com")
    (path / "a.py").write_text("from b import foo\n")
    (path / "b.py").write_text("def foo(): pass\n")
    repo.index.add(["a.py", "b.py"])
    repo.index.commit("init", author=actor, committer=actor)
    return repo


def test_list_repo_files_returns_tracked_py_files(tmp_path):
    _make_git_repo(tmp_path)
    result = _list_repo_files(str(tmp_path), ["python"])
    assert "a.py" in result
    assert "b.py" in result


def test_list_repo_files_filters_by_language(tmp_path):
    _make_git_repo(tmp_path)
    result = _list_repo_files(str(tmp_path), ["typescript"])
    assert "a.py" not in result
    assert "b.py" not in result


def test_list_repo_files_returns_empty_on_invalid_repo():
    result = _list_repo_files("/nonexistent_repo_path_xyz", ["python"])
    assert result == []


def test_build_import_graph_python(tmp_path):
    _make_git_repo(tmp_path)
    graph = build_import_graph(str(tmp_path), ["python"])
    assert "a.py" in graph
    assert "b.py" in graph
    # a.py imports b.py
    assert "b.py" in graph["a.py"]


def test_build_import_graph_returns_empty_on_invalid_repo():
    result = build_import_graph("/nonexistent_repo_path_xyz", ["python"])
    assert result == {}
