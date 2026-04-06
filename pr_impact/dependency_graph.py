import os
import re
from collections import deque
from pathlib import Path

import git

from .models import BlastRadiusEntry, resolve_language

# --- Import extraction patterns ---

# Matches `import foo.bar` (absolute Python imports)
_PY_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)
# Matches `from .foo import` and `from foo.bar import`, including relative (leading dots)
_PY_FROM = re.compile(r"^from\s+(\.{0,3}[\w.]*)\s+import", re.MULTILINE)

# Matches `import ... from '...'` and re-export forms like `export { x } from '...'`
_JS_IMPORT_FROM = re.compile(
    r"""(?:import\s+.*?\s+from|export\s+\{[^}]*\}\s+from)\s+['"]([^'"]+)['"]"""
)
# Matches `require('...')` CommonJS require calls
_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
# Matches bare `import '...'` side-effect imports
_JS_PLAIN_IMPORT = re.compile(r"""^import\s+['"]([^'"]+)['"]""", re.MULTILINE)

# Matches C# `using Namespace;` statements
_CS_USING = re.compile(r"^using\s+([\w.]+)\s*;", re.MULTILINE)
# Matches C# `namespace Foo.Bar` declarations
_CS_NAMESPACE = re.compile(r"^namespace\s+([\w.]+)", re.MULTILINE)

_JAVA_IMPORT = re.compile(
    r"^import\s+(?:static\s+)?([\w]+(?:\.[\w]+)*)(?:\.\*)?;", re.MULTILINE
)

_GO_IMPORT_SINGLE = re.compile(r'^import\s+(?:\w+\s+)?"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
_GO_IMPORT_PATH = re.compile(r'"([^"]+)"')

_RUBY_REQUIRE = re.compile(r"""^require\s+['"]([^'"]+)['"]""", re.MULTILINE)
_RUBY_REQUIRE_REL = re.compile(r"""^require_relative\s+['"]([^'"]+)['"]""", re.MULTILINE)


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

    # Absolute import: convert dots to path separators, normalise, then canonicalise to POSIX
    as_path = os.path.normpath(module.replace(".", os.sep) + ".py").replace("\\", "/")
    if as_path in all_files:
        return as_path
    as_init = os.path.normpath(
        os.path.join(module.replace(".", os.sep), "__init__.py")
    ).replace("\\", "/")
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


def _resolve_csharp_import(namespace: str, cs_namespace_map: dict[str, list[str]]) -> list[str]:
    """Look up a C# namespace in the pre-built namespace→files map."""
    return cs_namespace_map.get(namespace, [])


def _resolve_java_import(class_path: str, all_files: set[str]) -> str | None:
    """Convert a Java fully-qualified class name to a repo-relative .java file path.

    For static imports (com.example.Foo.method) the last component may be a method
    name rather than a class; try with and without it.
    """
    candidate = class_path.replace(".", "/") + ".java"
    if candidate in all_files:
        return candidate
    # Try dropping the last component (handles static imports)
    parts = class_path.rsplit(".", 1)
    if len(parts) == 2:
        candidate2 = parts[0].replace(".", "/") + ".java"
        if candidate2 in all_files:
            return candidate2
    return None


def _resolve_go_import(
    import_path: str, module_name: str, all_files: set[str]
) -> list[str]:
    """Resolve a Go import path to the .go source files in that package.

    Only internal packages (those sharing the module prefix) are resolved;
    stdlib and third-party imports return an empty list.
    """
    if not module_name or not import_path.startswith(module_name):
        return []
    rel = import_path[len(module_name):].lstrip("/")
    if not rel:
        return []  # importing the module root — not a useful edge
    prefix = rel + "/"
    return [
        f for f in all_files
        if f.startswith(prefix) and f.endswith(".go") and not f.endswith("_test.go")
    ]


def _resolve_ruby_require_relative(
    specifier: str, source_file: str, all_files: set[str]
) -> str | None:
    """Resolve require_relative — always relative to the source file."""
    if not specifier.endswith(".rb"):
        specifier += ".rb"
    base_dir = os.path.dirname(source_file)
    candidate = os.path.normpath(os.path.join(base_dir, specifier)).replace("\\", "/")
    return candidate if candidate in all_files else None


def _resolve_ruby_require(
    specifier: str, source_file: str, all_files: set[str]
) -> str | None:
    """Resolve require — relative paths are resolved from source file; bare names
    are tried as repo-root-relative. External gem names return None."""
    if not specifier.endswith(".rb"):
        specifier += ".rb"
    if specifier.startswith("."):
        base_dir = os.path.dirname(source_file)
        candidate = os.path.normpath(os.path.join(base_dir, specifier)).replace("\\", "/")
        return candidate if candidate in all_files else None
    # Bare name — try as repo-root-relative (e.g. require 'lib/foo' → lib/foo.rb)
    return specifier if specifier in all_files else None


def _extract_imports(
    content: str,
    source_file: str,
    language: str,
    all_files: set[str],
    cs_namespace_map: dict[str, list[str]] | None = None,
    go_module_name: str = "",
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
    elif language in ("javascript", "typescript"):
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
    elif language == "csharp":
        for m in _CS_USING.finditer(content):
            resolved.extend(_resolve_csharp_import(m.group(1), cs_namespace_map or {}))
    elif language == "java":
        for m in _JAVA_IMPORT.finditer(content):
            r = _resolve_java_import(m.group(1), all_files)
            if r:
                resolved.append(r)
    elif language == "go":
        for m in _GO_IMPORT_SINGLE.finditer(content):
            resolved.extend(_resolve_go_import(m.group(1), go_module_name, all_files))
        for block in _GO_IMPORT_BLOCK.finditer(content):
            for path_m in _GO_IMPORT_PATH.finditer(block.group(1)):
                resolved.extend(_resolve_go_import(path_m.group(1), go_module_name, all_files))
    elif language == "ruby":
        for m in _RUBY_REQUIRE.finditer(content):
            r = _resolve_ruby_require(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _RUBY_REQUIRE_REL.finditer(content):
            r = _resolve_ruby_require_relative(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)

    return list(set(resolved))




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
            content = _read_file(repo_path, rel_path)
            for m in _CS_NAMESPACE.finditer(content):
                cs_namespace_map.setdefault(m.group(1), []).append(rel_path)

    # Pre-read Go module name from go.mod for internal import resolution
    go_module_name = ""
    if "go" in language_filter:
        go_mod = _read_file(repo_path, "go.mod")
        mo = re.search(r"^module\s+(\S+)", go_mod, re.MULTILINE)
        if mo:
            go_module_name = mo.group(1)

    graph: dict[str, list[str]] = {}
    for rel_path in files:
        content = _read_file(repo_path, rel_path)
        lang = resolve_language(rel_path)
        imports = _extract_imports(
            content, rel_path, lang, all_files, cs_namespace_map, go_module_name
        )
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
