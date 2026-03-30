"""Unit tests for pr_impact/dependency_graph.py."""

from pr_impact.dependency_graph import (
    _resolve_js_import,
    _resolve_python_module,
    get_blast_radius,
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
