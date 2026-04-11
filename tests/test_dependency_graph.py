"""Unit tests for pr_impact/dependency_graph.py."""

import git
from unittest.mock import patch

from pr_impact.dependency_graph import (
    _list_repo_files,
    build_import_graph,
    get_blast_radius,
    get_imported_symbols,
)
from pr_impact.language_resolvers import (
    _probe_js_extensions,
    extract_imports_for_file as _extract_imports,
    find_go_module_for_file as _find_go_module_for_file,
    load_tsconfig_aliases as _load_tsconfig_aliases,
    read_file as _read_file,
    resolve_csharp_import as _resolve_csharp_import,
    resolve_go_import as _resolve_go_import,
    resolve_java_import as _resolve_java_import,
    resolve_java_wildcard as _resolve_java_wildcard,
    resolve_js_import as _resolve_js_import,
    resolve_python_module as _resolve_python_module,
    resolve_ruby_require as _resolve_ruby_require,
    resolve_ruby_require_relative as _resolve_ruby_require_relative,
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


# ---------------------------------------------------------------------------
# _resolve_java_import
# ---------------------------------------------------------------------------


def test_java_simple_class_import():
    all_files = {"com/example/Foo.java"}
    assert _resolve_java_import("com.example.Foo", all_files) == "com/example/Foo.java"


def test_java_nested_package():
    all_files = {"org/apache/commons/lang3/StringUtils.java"}
    result = _resolve_java_import("org.apache.commons.lang3.StringUtils", all_files)
    assert result == "org/apache/commons/lang3/StringUtils.java"


def test_java_static_import_drops_method_component():
    # import static com.example.Foo.bar -> class is Foo, method is bar
    all_files = {"com/example/Foo.java"}
    assert _resolve_java_import("com.example.Foo.bar", all_files) == "com/example/Foo.java"


def test_java_wildcard_import_returns_none():
    # import com.example.*; -> regex captures "com.example", no .java at that path
    all_files = {"com/example/Foo.java"}
    assert _resolve_java_import("com.example", all_files) is None


def test_java_class_not_in_repo_returns_none():
    assert _resolve_java_import("com.example.Missing", set()) is None


# ---------------------------------------------------------------------------
# _resolve_go_import
# ---------------------------------------------------------------------------


def test_go_local_package_resolves_to_files():
    module = "github.com/user/myapp"
    all_files = {"pkg/util/helpers.go", "pkg/util/format.go"}
    result = _resolve_go_import("github.com/user/myapp/pkg/util", module, "", all_files)
    assert set(result) == {"pkg/util/helpers.go", "pkg/util/format.go"}


def test_go_external_package_returns_empty():
    module = "github.com/user/myapp"
    all_files = {"pkg/util/helpers.go"}
    assert _resolve_go_import("fmt", module, "", all_files) == []
    assert _resolve_go_import("github.com/other/lib", module, "", all_files) == []


def test_go_empty_module_name_returns_empty():
    all_files = {"pkg/util/helpers.go"}
    assert _resolve_go_import("github.com/user/myapp/pkg/util", "", "", all_files) == []


def test_go_module_root_import_returns_empty():
    module = "github.com/user/myapp"
    all_files = {"main.go"}
    assert _resolve_go_import("github.com/user/myapp", module, "", all_files) == []


def test_go_test_files_excluded():
    module = "github.com/user/myapp"
    all_files = {"pkg/util/helpers.go", "pkg/util/helpers_test.go"}
    result = _resolve_go_import("github.com/user/myapp/pkg/util", module, "", all_files)
    assert result == ["pkg/util/helpers.go"]


# ---------------------------------------------------------------------------
# _resolve_ruby_require_relative
# ---------------------------------------------------------------------------


def test_ruby_require_relative_no_extension():
    all_files = {"lib/models/user.rb"}
    result = _resolve_ruby_require_relative("models/user", "lib/app.rb", all_files)
    assert result == "lib/models/user.rb"


def test_ruby_require_relative_with_extension():
    all_files = {"lib/helpers.rb"}
    result = _resolve_ruby_require_relative("helpers.rb", "lib/app.rb", all_files)
    assert result == "lib/helpers.rb"


def test_ruby_require_relative_parent_traversal():
    all_files = {"lib/shared.rb"}
    result = _resolve_ruby_require_relative("../lib/shared", "app/main.rb", all_files)
    assert result == "lib/shared.rb"


def test_ruby_require_relative_not_found_returns_none():
    result = _resolve_ruby_require_relative("missing", "lib/app.rb", set())
    assert result is None


# ---------------------------------------------------------------------------
# _resolve_ruby_require
# ---------------------------------------------------------------------------


def test_ruby_require_relative_path_resolves():
    all_files = {"lib/helpers.rb"}
    result = _resolve_ruby_require("./helpers", "lib/app.rb", all_files)
    assert result == "lib/helpers.rb"


def test_ruby_require_bare_name_resolves_from_root():
    all_files = {"lib/foo.rb"}
    result = _resolve_ruby_require("lib/foo", "bin/main.rb", all_files)
    assert result == "lib/foo.rb"


def test_ruby_require_external_gem_returns_none():
    result = _resolve_ruby_require("json", "lib/app.rb", set())
    assert result is None


# ---------------------------------------------------------------------------
# _extract_imports: Java, Go, Ruby branches
# ---------------------------------------------------------------------------


def test_extract_imports_java():
    content = "import com.example.Foo;\n"
    all_files = {"com/example/Foo.java"}
    result = _extract_imports(content, "com/example/Main.java", "java", all_files)
    assert "com/example/Foo.java" in result


def test_extract_imports_go_single():
    content = 'import "github.com/user/myapp/pkg/util"\n'
    all_files = {"pkg/util/helpers.go"}
    result = _extract_imports(
        content, "cmd/main.go", "go", all_files,
        go_module_name="github.com/user/myapp", go_module_root="",
    )
    assert "pkg/util/helpers.go" in result


def test_extract_imports_go_block():
    content = 'import (\n    "github.com/user/myapp/pkg/util"\n    "fmt"\n)\n'
    all_files = {"pkg/util/helpers.go"}
    result = _extract_imports(
        content, "cmd/main.go", "go", all_files,
        go_module_name="github.com/user/myapp", go_module_root="",
    )
    assert "pkg/util/helpers.go" in result


def test_extract_imports_ruby_require():
    content = "require 'lib/models'\n"
    all_files = {"lib/models.rb"}
    result = _extract_imports(content, "app.rb", "ruby", all_files)
    assert "lib/models.rb" in result


def test_extract_imports_ruby_require_relative():
    content = "require_relative 'models/user'\n"
    all_files = {"lib/models/user.rb"}
    result = _extract_imports(content, "lib/app.rb", "ruby", all_files)
    assert "lib/models/user.rb" in result


# ---------------------------------------------------------------------------
# build_import_graph: Go module name pre-pass
# ---------------------------------------------------------------------------


@patch("pr_impact.language_resolvers.read_file")
@patch("pr_impact.dependency_graph.read_file")
@patch("pr_impact.dependency_graph.git.Repo")
def test_build_import_graph_go_reads_module(mock_repo, mock_dg_read, mock_lr_read):
    mock_repo.return_value.git.ls_files.return_value = "cmd/main.go\npkg/util/helpers.go"

    def fake_read(repo, path):
        if path == "go.mod":
            return "module github.com/user/myapp\n"
        if path == "cmd/main.go":
            return 'import "github.com/user/myapp/pkg/util"\n'
        return ""

    mock_dg_read.side_effect = fake_read
    mock_lr_read.side_effect = fake_read
    graph = build_import_graph("/repo", ["go"])
    assert "pkg/util/helpers.go" in graph.get("cmd/main.go", [])


@patch("pr_impact.language_resolvers.read_file")
@patch("pr_impact.dependency_graph.read_file")
@patch("pr_impact.dependency_graph.git.Repo")
def test_build_import_graph_go_skips_test_files(mock_repo, mock_dg_read, mock_lr_read):
    """_test.go files must not appear as nodes in the dependency graph."""
    mock_repo.return_value.git.ls_files.return_value = (
        "cmd/main.go\npkg/util/helpers.go\npkg/util/helpers_test.go"
    )

    def fake_read(repo, path):
        if path == "go.mod":
            return "module github.com/user/myapp\n"
        return ""

    mock_dg_read.side_effect = fake_read
    mock_lr_read.side_effect = fake_read
    graph = build_import_graph("/repo", ["go"])
    assert "pkg/util/helpers_test.go" not in graph


# ---------------------------------------------------------------------------
# get_imported_symbols: Java and Go
# ---------------------------------------------------------------------------


def test_get_imported_symbols_java(tmp_path):
    f = tmp_path / "Main.java"
    f.write_text("import com.example.Foo;\nimport static com.example.Bar.method;\n", encoding="utf-8")
    result = get_imported_symbols(str(f), "com/example/Foo.java")
    assert "Foo" in result
    assert "method" in result  # static import captures the member name


def test_get_imported_symbols_go_anonymous(tmp_path):
    f = tmp_path / "main.go"
    f.write_text('import (\n    "github.com/user/myapp/pkg/util"\n)\n', encoding="utf-8")
    result = get_imported_symbols(str(f), "pkg/util/helpers.go")
    assert "util" in result


def test_get_imported_symbols_go_aliased(tmp_path):
    f = tmp_path / "main.go"
    # Named alias in a block import - matched by go_import_named pattern
    f.write_text('import (\n    myutil "github.com/user/myapp/pkg/util"\n)\n', encoding="utf-8")
    result = get_imported_symbols(str(f), "pkg/util/helpers.go")
    assert "myutil" in result


# ---------------------------------------------------------------------------
# Java: source root detection (Unit 1)
# ---------------------------------------------------------------------------


def test_java_resolves_in_maven_source_root():
    all_files = {"src/main/java/com/example/Foo.java"}
    result = _resolve_java_import("com.example.Foo", all_files)
    assert result == "src/main/java/com/example/Foo.java"


def test_java_resolves_in_src_source_root():
    all_files = {"src/com/example/Bar.java"}
    result = _resolve_java_import("com.example.Bar", all_files)
    assert result == "src/com/example/Bar.java"


def test_java_repo_root_preferred_over_source_roots():
    all_files = {"com/example/Foo.java", "src/main/java/com/example/Foo.java"}
    result = _resolve_java_import("com.example.Foo", all_files)
    assert result == "com/example/Foo.java"


def test_java_static_import_with_source_root():
    all_files = {"src/main/java/com/example/Foo.java"}
    result = _resolve_java_import("com.example.Foo.staticMethod", all_files)
    assert result == "src/main/java/com/example/Foo.java"


# ---------------------------------------------------------------------------
# Java: wildcard import resolution (Unit 1)
# ---------------------------------------------------------------------------


def test_java_wildcard_resolves_package_files():
    all_files = {"com/example/Foo.java", "com/example/Bar.java", "com/other/Baz.java"}
    result = _resolve_java_wildcard("com.example", all_files)
    assert set(result) == {"com/example/Foo.java", "com/example/Bar.java"}


def test_java_wildcard_with_maven_source_root():
    all_files = {"src/main/java/com/example/Foo.java", "src/main/java/com/example/Bar.java"}
    result = _resolve_java_wildcard("com.example", all_files)
    assert set(result) == {
        "src/main/java/com/example/Foo.java",
        "src/main/java/com/example/Bar.java",
    }


def test_java_wildcard_no_match_returns_empty():
    all_files = {"com/other/Baz.java"}
    result = _resolve_java_wildcard("com.example", all_files)
    assert result == []


def test_java_wildcard_excludes_subpackages():
    """import com.example.* must not pull in files from nested packages."""
    all_files = {
        "com/example/Foo.java",
        "com/example/sub/Bar.java",   # subpackage — must be excluded
    }
    result = _resolve_java_wildcard("com.example", all_files)
    assert result == ["com/example/Foo.java"]


def test_extract_imports_java_wildcard():
    content = "import com.example.*;\n"
    all_files = {"com/example/Foo.java", "com/example/Bar.java"}
    result = _extract_imports(content, "com/Main.java", "java", all_files)
    assert set(result) == {"com/example/Foo.java", "com/example/Bar.java"}


def test_extract_imports_java_static_wildcard_resolves_class():
    """import static com.example.Util.* should resolve the class file, not a directory."""
    content = "import static com.example.Util.*;\n"
    all_files = {"com/example/Util.java"}
    result = _extract_imports(content, "com/Main.java", "java", all_files)
    assert result == ["com/example/Util.java"]


def test_extract_imports_java_static_wildcard_falls_back_to_package():
    """If the wildcard target is not a class, fall back to package wildcard resolution."""
    content = "import static com.example.*;\n"
    all_files = {"com/example/Foo.java", "com/example/Bar.java"}
    result = _extract_imports(content, "com/Main.java", "java", all_files)
    assert set(result) == {"com/example/Foo.java", "com/example/Bar.java"}


# ---------------------------------------------------------------------------
# Go: vendor/ directory exclusion (Unit 2)
# ---------------------------------------------------------------------------


def test_go_vendor_files_excluded():
    module = "github.com/user/myapp"
    all_files = {
        "pkg/util/helpers.go",
        "vendor/pkg/util/helpers.go",
    }
    result = _resolve_go_import("github.com/user/myapp/pkg/util", module, "", all_files)
    assert "vendor/pkg/util/helpers.go" not in result
    assert "pkg/util/helpers.go" in result


# ---------------------------------------------------------------------------
# Go: non-root go.mod discovery (Unit 3)
# ---------------------------------------------------------------------------


@patch("pr_impact.language_resolvers.read_file")
def test_find_go_module_for_file_at_root(mock_read):
    def fake_read(repo, path):
        if path == "go.mod":
            return "module github.com/user/myapp\n"
        return ""  # no go.mod in subdirectories

    mock_read.side_effect = fake_read
    module_name, module_root = _find_go_module_for_file("/repo", "cmd/main.go")
    assert module_name == "github.com/user/myapp"
    assert module_root == ""  # go.mod is at repo root


@patch("pr_impact.language_resolvers.read_file")
def test_find_go_module_for_file_in_subdirectory(mock_read):
    def fake_read(repo, path):
        if path == "services/auth/go.mod":
            return "module github.com/user/auth\n"
        return ""

    mock_read.side_effect = fake_read
    module_name, module_root = _find_go_module_for_file("/repo", "services/auth/handler.go")
    assert module_name == "github.com/user/auth"
    assert module_root == "services/auth"


@patch("pr_impact.language_resolvers.read_file")
def test_find_go_module_for_file_missing_returns_empty(mock_read):
    mock_read.return_value = ""
    module_name, module_root = _find_go_module_for_file("/repo", "cmd/main.go")
    assert module_name == ""
    assert module_root == ""


@patch("pr_impact.language_resolvers.read_file")
def test_find_go_module_for_file_cache_reuse(mock_read):
    """Second call for a file in the same directory must not re-read go.mod."""
    mock_read.return_value = "module github.com/user/myapp\n"
    cache: dict = {}
    _find_go_module_for_file("/repo", "cmd/main.go", cache)
    call_count_after_first = mock_read.call_count
    _find_go_module_for_file("/repo", "cmd/server.go", cache)
    assert mock_read.call_count == call_count_after_first  # no extra reads


@patch("pr_impact.language_resolvers.read_file")
def test_find_go_module_for_file_nested_preferred_over_root(mock_read):
    """A nested go.mod must take precedence over a root-level one."""
    def fake_read(repo, path):
        if path == "services/auth/go.mod":
            return "module github.com/user/auth\n"
        if path == "go.mod":
            return "module github.com/user/monorepo\n"
        return ""

    mock_read.side_effect = fake_read
    module_name, module_root = _find_go_module_for_file("/repo", "services/auth/handler.go")
    assert module_name == "github.com/user/auth"
    assert module_root == "services/auth"


# ---------------------------------------------------------------------------
# Ruby: lib/ convention fallback (Unit 4)
# ---------------------------------------------------------------------------


def test_ruby_require_lib_convention_fallback():
    all_files = {"lib/models/user.rb"}
    result = _resolve_ruby_require("models/user", "bin/main.rb", all_files)
    assert result == "lib/models/user.rb"


def test_ruby_require_root_preferred_over_lib():
    all_files = {"models/user.rb", "lib/models/user.rb"}
    result = _resolve_ruby_require("models/user", "bin/main.rb", all_files)
    assert result == "models/user.rb"


# ---------------------------------------------------------------------------
# TypeScript: tsconfig path alias resolution (Unit 5)
# ---------------------------------------------------------------------------


def test_probe_js_extensions_exact_match():
    assert _probe_js_extensions("src/utils.ts", {"src/utils.ts"}) == "src/utils.ts"


def test_probe_js_extensions_adds_ts():
    assert _probe_js_extensions("src/utils", {"src/utils.ts"}) == "src/utils.ts"


def test_probe_js_extensions_index_fallback():
    assert (
        _probe_js_extensions("src/components/Button", {"src/components/Button/index.tsx"})
        == "src/components/Button/index.tsx"
    )


def test_probe_js_extensions_no_match_returns_none():
    assert _probe_js_extensions("src/missing", set()) is None


def test_resolve_js_import_wildcard_alias():
    all_files = {"src/components/Button.tsx"}
    ts_paths = {"@/*": ["src/*"]}
    result = _resolve_js_import("@/components/Button", "app/page.ts", all_files, ts_paths=ts_paths)
    assert result == "src/components/Button.tsx"


def test_resolve_js_import_exact_alias():
    all_files = {"src/config/index.ts"}
    ts_paths = {"@config": ["src/config/index.ts"]}
    result = _resolve_js_import("@config", "app/page.ts", all_files, ts_paths=ts_paths)
    assert result == "src/config/index.ts"


def test_resolve_js_import_base_url():
    all_files = {"src/utils/format.ts"}
    result = _resolve_js_import("utils/format", "app/page.ts", all_files, ts_base_url="src")
    assert result == "src/utils/format.ts"


def test_resolve_js_import_no_alias_no_baseurl_returns_none():
    all_files = {"src/utils/format.ts"}
    result = _resolve_js_import("utils/format", "app/page.ts", all_files)
    assert result is None


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_parses_paths(mock_read):
    mock_read.return_value = """{
  "compilerOptions": {
    "baseUrl": "src",
    "paths": {
      "@/*": ["src/*"],
      "@utils": ["src/utils/index.ts"]
    }
  }
}"""
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == "src"
    assert paths == {"@/*": ["src/*"], "@utils": ["src/utils/index.ts"]}


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_missing_returns_empty(mock_read):
    mock_read.return_value = ""
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == ""
    assert paths == {}


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_malformed_returns_empty(mock_read):
    mock_read.return_value = "{ this is not valid json !!!"
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == ""
    assert paths == {}


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_strips_comments(mock_read):
    mock_read.return_value = """{
  // tsconfig with comments
  "compilerOptions": {
    /* base url */
    "baseUrl": "src",
    "paths": {
      "@/*": ["src/*"] // trailing comment
    }
  }
}"""
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == "src"
    assert "@/*" in paths


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_follows_extends(mock_read):
    """Paths defined only in the extended config are inherited by the child."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"extends": "./tsconfig.base.json", "compilerOptions": {"baseUrl": "src"}}'
        if path == "tsconfig.base.json":
            return '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}'
        return ""

    mock_read.side_effect = fake_read
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == "src"
    assert paths == {"@/*": ["src/*"]}


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_child_overrides_parent(mock_read):
    """Child compilerOptions take precedence over inherited values."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"extends": "./tsconfig.base.json", "compilerOptions": {"baseUrl": "app"}}'
        if path == "tsconfig.base.json":
            return '{"compilerOptions": {"baseUrl": "src", "paths": {"@/*": ["src/*"]}}}'
        return ""

    mock_read.side_effect = fake_read
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == "app"          # child wins
    assert paths == {"@/*": ["src/*"]}  # inherited from parent


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_normalizes_dot_slash_base_url(mock_read):
    """Leading './' and bare '.' in baseUrl are stripped to repo-relative form."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"compilerOptions": {"baseUrl": "./src"}}'
        return ""

    mock_read.side_effect = fake_read
    base_url, _ = _load_tsconfig_aliases("/repo")
    assert base_url == "src"


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_normalizes_dot_base_url(mock_read):
    """A bare '.' baseUrl (repo root) is returned as an empty string."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"compilerOptions": {"baseUrl": "."}}'
        return ""

    mock_read.side_effect = fake_read
    base_url, _ = _load_tsconfig_aliases("/repo")
    assert base_url == ""


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_normalizes_dot_slash_path_targets(mock_read):
    """Leading './' on path targets is stripped so callers get plain paths."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
        return ""

    mock_read.side_effect = fake_read
    _, paths = _load_tsconfig_aliases("/repo")
    assert paths == {"@/*": ["src/*"]}


@patch("pr_impact.language_resolvers.read_file")
def test_load_tsconfig_aliases_cycle_does_not_loop(mock_read):
    """A circular extends chain terminates without error."""
    def fake_read(repo, path):
        if path == "tsconfig.json":
            return '{"extends": "./tsconfig.json", "compilerOptions": {"baseUrl": "src"}}'
        return ""

    mock_read.side_effect = fake_read
    base_url, paths = _load_tsconfig_aliases("/repo")
    assert base_url == "src"
    assert paths == {}
