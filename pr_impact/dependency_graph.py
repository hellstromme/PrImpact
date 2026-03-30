import os
import re
from collections import deque
from pathlib import Path

import git

from .models import BlastRadiusEntry

# --- Import extraction patterns ---

_PY_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)
_PY_FROM = re.compile(r"^from\s+(\.{0,3}[\w.]*)\s+import", re.MULTILINE)

_JS_IMPORT_FROM = re.compile(
    r"""(?:import\s+.*?\s+from|export\s+\{[^}]*\}\s+from)\s+['"]([^'"]+)['"]"""
)
_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_PLAIN_IMPORT = re.compile(r"""^import\s+['"]([^'"]+)['"]""", re.MULTILINE)


def _list_repo_files(repo_path: str, language_filter: list[str]) -> list[str]:
    """Return repo-relative paths of tracked files matching the language filter."""
    extensions = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx", ".mjs", ".cjs"},
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


def _read_file(repo_path: str, rel_path: str) -> str:
    try:
        full = os.path.join(repo_path, rel_path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


def _resolve_python_module(module: str, source_file: str, all_files: set[str]) -> str | None:
    """Convert a Python module name to a repo-relative file path."""
    if module.startswith("."):
        # Relative import: count leading dots
        dots = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".")
        base_dir = os.path.dirname(source_file)
        for _ in range(dots - 1):
            base_dir = os.path.dirname(base_dir)
        candidate = os.path.join(base_dir, rest.replace(".", os.sep) + ".py")
        candidate = os.path.normpath(candidate).replace("\\", "/")
        if candidate in all_files:
            return candidate
        # Try as package __init__
        candidate2 = os.path.join(base_dir, rest.replace(".", os.sep), "__init__.py")
        candidate2 = os.path.normpath(candidate2).replace("\\", "/")
        if candidate2 in all_files:
            return candidate2
        return None

    # Absolute import: convert dots to path separators (always forward slashes)
    as_path = module.replace(".", "/") + ".py"
    if as_path in all_files:
        return as_path
    as_init = module.replace(".", "/") + "/__init__.py"
    if as_init in all_files:
        return as_init
    return None


def _resolve_js_import(specifier: str, source_file: str, all_files: set[str]) -> str | None:
    """Convert a JS/TS import specifier to a repo-relative file path."""
    if not specifier.startswith("."):
        return None  # external package

    base_dir = os.path.dirname(source_file)
    raw = os.path.normpath(os.path.join(base_dir, specifier)).replace("\\", "/")

    # Try exact match
    if raw in all_files:
        return raw

    # Try adding known extensions
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        candidate = raw + ext
        if candidate in all_files:
            return candidate

    # Try index file
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidate = raw + "/index" + ext
        if candidate in all_files:
            return candidate

    return None


def _extract_imports(
    content: str, source_file: str, language: str, all_files: set[str]
) -> list[str]:
    resolved: list[str] = []

    if language == "python":
        for m in _PY_IMPORT.finditer(content):
            r = _resolve_python_module(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _PY_FROM.finditer(content):
            r = _resolve_python_module(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
    else:
        for m in _JS_IMPORT_FROM.finditer(content):
            r = _resolve_js_import(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _JS_REQUIRE.finditer(content):
            r = _resolve_js_import(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _JS_PLAIN_IMPORT.finditer(content):
            r = _resolve_js_import(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)

    return list(set(resolved))


def _file_language(path: str) -> str:
    suffix = Path(path).suffix
    if suffix == ".py":
        return "python"
    if suffix in (".ts", ".tsx"):
        return "typescript"
    if suffix in (".js", ".jsx", ".mjs", ".cjs"):
        return "javascript"
    return "unknown"


def build_import_graph(repo_path: str, language_filter: list[str]) -> dict[str, list[str]]:
    """Return forward graph: {file: [files it imports]}."""
    files = _list_repo_files(repo_path, language_filter)
    all_files = set(files)
    graph: dict[str, list[str]] = {}

    for rel_path in files:
        content = _read_file(repo_path, rel_path)
        lang = _file_language(rel_path)
        imports = _extract_imports(content, rel_path, lang, all_files)
        graph[rel_path] = imports

    return graph


def get_blast_radius(
    reverse_graph: dict[str, list[str]],
    changed_files: list[str],
    max_depth: int = 3,
    repo_path: str = "",
) -> list[BlastRadiusEntry]:
    """BFS through reverse graph from changed files. Returns entries sorted by distance then path."""
    visited: dict[str, int] = {}  # path -> shortest distance
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

    # Build entries for dependents only (exclude the changed files themselves at dist 0)
    entries: list[BlastRadiusEntry] = []
    for path, dist in visited.items():
        if dist == 0:
            continue
        imported_symbols = get_imported_symbols(
            os.path.join(repo_path, path) if repo_path else path,
            # We need to find which changed file this entry imports;
            # use the first changed file reachable (simplified: pass empty for now,
            # populated properly in cli.py if needed)
            "",
        )
        entries.append(
            BlastRadiusEntry(
                path=path,
                distance=dist,
                imported_symbols=imported_symbols,
                churn_score=None,
            )
        )

    entries.sort(key=lambda e: (e.distance, e.path))
    return entries


def get_imported_symbols(file_path: str, imported_from: str) -> list[str]:
    """Extract the named symbols that file_path imports from imported_from."""
    if not file_path or not imported_from:
        return []
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return []

    symbols: list[str] = []

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

    return list(set(symbols))
