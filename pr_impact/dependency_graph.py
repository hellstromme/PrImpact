"""Dependency graph construction and blast-radius BFS for PrImpact.

Builds a forward import graph (file → files it imports) and exposes
get_blast_radius() for BFS traversal of the reverse graph.

Language-specific import resolution is delegated to language_resolvers.py.
"""

import os
import re
from collections import defaultdict, deque
from pathlib import Path

import git

from .ast_extractor import extract_imports as ast_extract_imports
from .language_resolvers import (
    _CS_USING,
    ImportResolutionConfig,
    extract_imports_for_file,
    find_go_module_for_file,
    load_tsconfig_aliases,
    read_file,
)
from .models import BlastGraph, BlastRadiusEntry, GraphEdge, GraphNode, resolve_language

# Matches C# `namespace Foo.Bar` declarations — used only to build the
# namespace→files map in build_import_graph; not a resolver concern.
_CS_NAMESPACE = re.compile(r"^namespace\s+([\w.]+)", re.MULTILINE)


def _list_repo_files(repo_path: str, language_filter: list[str]) -> list[str]:
    """Return repo-relative paths of tracked files matching the language filter."""
    extensions = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx", ".mjs", ".cjs"},
        "csharp": {".cs"},
        "java": {".java"},
        "go": {".go"},
        "ruby": {".rb"},
    }
    wanted_exts: set[str] = set()
    for lang in language_filter:
        wanted_exts |= extensions.get(lang, set())

    try:
        repo = git.Repo(repo_path)
        files = repo.git.ls_files().splitlines()
    except Exception:
        files = []

    return [f for f in files if Path(f).suffix in wanted_exts]


def build_import_graph(repo_path: str, language_filter: list[str]) -> dict[str, list[str]]:
    """Return forward graph: {file: [files it imports]}."""
    files = _list_repo_files(repo_path, language_filter)
    all_files = set(files)

    # Pre-build namespace→files map for C# resolution
    cs_namespace_map: dict[str, list[str]] = {}
    if "csharp" in language_filter:
        for rel_path in files:
            if not rel_path.endswith(".cs"):
                continue
            content = read_file(repo_path, rel_path)
            for m in _CS_NAMESPACE.finditer(content):
                cs_namespace_map.setdefault(m.group(1), []).append(rel_path)

    # Per-file Go module lookup; cache avoids repeated go.mod reads for the same module
    go_module_cache: dict[str, tuple[str, str]] = {}

    # Load TypeScript/JavaScript path aliases from tsconfig.json
    ts_base_url, ts_paths = "", {}
    if "typescript" in language_filter or "javascript" in language_filter:
        ts_base_url, ts_paths = load_tsconfig_aliases(repo_path)

    graph: dict[str, list[str]] = {}
    for rel_path in files:
        if rel_path.endswith("_test.go"):
            continue
        content = read_file(repo_path, rel_path)
        lang = resolve_language(rel_path)
        go_module_name, go_module_root = "", ""
        if lang == "go":
            go_module_name, go_module_root = find_go_module_for_file(
                repo_path, rel_path, go_module_cache
            )
        cfg = ImportResolutionConfig(
            cs_namespace_map=cs_namespace_map,
            go_module_name=go_module_name,
            go_module_root=go_module_root,
            ts_base_url=ts_base_url,
            ts_paths=ts_paths,
        )
        imports = extract_imports_for_file(content, rel_path, lang, all_files, cfg)
        graph[rel_path] = imports

    return graph


def get_blast_radius(
    reverse_graph: dict[str, list[str]],
    changed_files: list[str],
    max_depth: int = 3,
    repo_path: str = "",
    depth_overrides: dict[str, int] | None = None,
) -> list[BlastRadiusEntry]:
    """BFS through reverse graph from changed files. Returns entries sorted by distance then path.

    When *depth_overrides* is provided it maps individual starting file paths to a
    per-file max BFS depth (overriding *max_depth* for that file only, still capped
    at 3). If different starting files have different depths the BFS is run once per
    unique depth group and results are merged, keeping the minimum distance when a
    dependent appears via multiple starting files.
    """
    if not depth_overrides:
        # Fast path: single BFS with uniform depth
        visited: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for path in changed_files:
            queue.append((path, 0))
        while queue:
            current, dist = queue.popleft()
            if current in visited:
                continue
            visited[current] = dist
            if dist < max_depth:
                for dependent in reverse_graph.get(current, []):
                    if dependent not in visited:
                        queue.append((dependent, dist + 1))
    else:
        # Per-file depth: group starting files by their effective depth and BFS each group
        # separately, then merge results taking minimum distance.
        depth_groups: dict[int, list[str]] = defaultdict(list)
        for path in changed_files:
            effective = depth_overrides.get(path, max_depth)
            depth_groups[effective].append(path)

        visited = {}
        for group_depth, group_paths in depth_groups.items():
            group_depth = min(group_depth, 3)  # enforce documented cap defensively
            group_visited: dict[str, int] = {}
            q: deque[tuple[str, int]] = deque()
            for path in group_paths:
                q.append((path, 0))
            while q:
                current, dist = q.popleft()
                if current in group_visited:
                    continue
                group_visited[current] = dist
                if dist < group_depth:
                    for dependent in reverse_graph.get(current, []):
                        if dependent not in group_visited:
                            q.append((dependent, dist + 1))
            for path, dist in group_visited.items():
                if path not in visited or dist < visited[path]:
                    visited[path] = dist

    # Build entries for dependents only (exclude the changed files themselves at dist 0)
    entries: list[BlastRadiusEntry] = []
    for path, dist in visited.items():
        if dist == 0:
            continue
        entries.append(
            BlastRadiusEntry(
                path=path,
                distance=dist,
                imported_symbols=[],
                churn_score=None,
            )
        )

    entries.sort(key=lambda e: (e.distance, e.path))
    return entries


def get_imported_symbols(file_path: str, imported_from: str) -> list[str]:
    """Extract the named symbols that file_path imports from imported_from.

    Uses AST extraction for Python and JS/TS (v0.4); falls back to regex for
    all languages if AST is unavailable.
    """
    if not file_path or not imported_from:
        return []
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return []

    lang = resolve_language(file_path)
    symbols: list[str] = []

    # AST-first for Python and JS/TS — richer and more accurate named-import extraction
    if lang in ("python", "javascript", "typescript"):
        ast_imports = ast_extract_imports(content, lang)
        if ast_imports is not None:
            # Only collect names from imports that originate from the target module
            from_stem = Path(imported_from).stem
            for ast_imp in ast_imports:
                spec = ast_imp.specifier.lstrip("./")
                spec_stem = spec.split("/")[-1].split(".")[-1] if spec else ""
                if spec_stem == from_stem:
                    symbols.extend(ast_imp.imported_names)
            return list(set(s for s in symbols if s))

    # Regex fallback (also handles Java, Go, C#, Ruby which have no AST path above)

    # Python: from X import a, b, c
    py_from = re.compile(r"^from\s+[\w.]+\s+import\s+(.+)$", re.MULTILINE)
    for m in py_from.finditer(content):
        raw = m.group(1).strip().rstrip("\\")
        # Handle parenthesised multi-line imports roughly
        raw = raw.strip("()")
        for sym in re.split(r",\s*", raw):
            sym = sym.strip().split(" as ")[0].strip()
            if sym and sym != "*":
                symbols.append(sym)

    # JS/TS: import { a, b } from '...'
    js_named = re.compile(r"import\s+\{([^}]+)\}\s+from\s+['\"][^'\"]+['\"]")
    for m in js_named.finditer(content):
        for sym in re.split(r",\s*", m.group(1)):
            sym = sym.strip().split(" as ")[0].strip()
            if sym:
                symbols.append(sym)

    # C#: using Namespace — return leaf namespace name as best-effort
    for m in _CS_USING.finditer(content):
        leaf = m.group(1).split(".")[-1]
        symbols.append(leaf)

    # Java: import com.example.Foo → symbol "Foo"
    java_import = re.compile(
        r"^import\s+(?:static\s+)?[\w]+(?:\.[\w]+)*\.([\w]+)(?:\.\*)?;", re.MULTILINE
    )
    for m in java_import.finditer(content):
        symbols.append(m.group(1))

    # Go: named alias → use alias; anonymous → use last path component
    go_import_named = re.compile(r'^\s*(\w+)\s+"[^"]+"', re.MULTILINE)
    go_import_anon = re.compile(r'^\s*"([^"]+)"', re.MULTILINE)
    for m in go_import_named.finditer(content):
        if m.group(1) not in ("_", "import"):
            symbols.append(m.group(1))
    for m in go_import_anon.finditer(content):
        symbols.append(m.group(1).rsplit("/", 1)[-1])

    return list(set(symbols))


def build_blast_graph(
    reverse_graph: dict[str, list[str]],
    changed_paths: list[str],
    blast_radius: list[BlastRadiusEntry],
) -> BlastGraph:
    """Build a graph representation of the blast radius for interactive visualization.

    Edges are reconstructed from the reverse graph: for each node pair (A, B)
    where both are in the node set and B is in reverse_graph[A], an edge A→B is
    created (impact propagates from A to B because B imports A).
    """
    all_paths: set[str] = set(changed_paths) | {e.path for e in blast_radius}

    # Build nodes
    br_by_path = {e.path: e for e in blast_radius}
    nodes: list[GraphNode] = []
    for path in changed_paths:
        nodes.append(GraphNode(
            id=path, path=path, type="changed", distance=0,
            language=resolve_language(path), churn_score=None,
        ))
    for entry in blast_radius:
        nodes.append(GraphNode(
            id=entry.path, path=entry.path, type="affected", distance=entry.distance,
            language=resolve_language(entry.path), churn_score=entry.churn_score,
        ))

    # Build edges: reverse_graph[A] = [B, ...] means B imports A → edge A→B
    symbol_map = {e.path: e.imported_symbols for e in blast_radius}
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for source_path in all_paths:
        for target_path in reverse_graph.get(source_path, []):
            if target_path not in all_paths:
                continue
            key = (source_path, target_path)
            if key in seen:
                continue
            seen.add(key)
            edges.append(GraphEdge(
                source=source_path,
                target=target_path,
                symbols=symbol_map.get(target_path, []),
            ))

    return BlastGraph(nodes=nodes, edges=edges)
