import json
import os
import re
from collections import deque
from pathlib import Path

import git

from .ast_extractor import extract_imports as ast_extract_imports
from .models import BlastRadiusEntry, resolve_language


def _posix(path: str) -> str:
    """Normalise *path* to a forward-slash string, stable on Windows."""
    return os.path.normpath(path).replace("\\", "/")


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

# Group 1: fully-qualified class/package name; group 2: '.*' if wildcard import
_JAVA_IMPORT = re.compile(
    r"^import\s+(?:static\s+)?([\w]+(?:\.[\w]+)*)(\.\*)?;", re.MULTILINE
)

_GO_IMPORT_SINGLE = re.compile(r'^import\s+(?:\w+\s+)?"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
_GO_IMPORT_PATH = re.compile(r'"([^"]+)"')

_RUBY_REQUIRE = re.compile(r"""^require\s+['"]([^'"]+)['"]""", re.MULTILINE)
_RUBY_REQUIRE_REL = re.compile(r"""^require_relative\s+['"]([^'"]+)['"]""", re.MULTILINE)

# Conventional Java source root prefixes tried in order (repo root first)
_JAVA_SOURCE_ROOTS = ("", "src/main/java/", "src/java/", "src/")


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


def _find_go_module_for_file(
    repo_path: str,
    source_file: str,
    cache: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Walk up from source_file's directory to find the nearest go.mod.

    Returns (module_name, module_root) where module_root is the repo-relative
    directory containing go.mod (empty string when go.mod is at the repo root).
    Returns ("", "") if no go.mod is found.

    cache maps repo-relative directory -> (module_name, module_root) so that
    files sharing the same module pay the I/O cost only once.
    """
    if cache is None:
        cache = {}

    d = os.path.dirname(source_file)
    visited: list[str] = []

    while True:
        if d in cache:
            result = cache[d]
            for v in visited:
                cache[v] = result
            return result

        visited.append(d)

        mod_rel = (d + "/go.mod") if d else "go.mod"
        content = _read_file(repo_path, mod_rel)
        m = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
        if m:
            result = (m.group(1), d)
            for v in visited:
                cache[v] = result
            return result

        parent = os.path.dirname(d)
        if parent == d or not d:
            # Reached repo root without finding go.mod
            for v in visited:
                cache[v] = ("", "")
            return ("", "")
        d = parent


def _strip_ts_dot_slash(p: str) -> str:
    """Strip a leading './' from a tsconfig path string; bare '.' becomes ''."""
    if p == ".":
        return ""
    if p.startswith("./"):
        return p[2:]
    return p


def _parse_tsconfig(
    repo_path: str, rel_path: str, seen: set[str]
) -> tuple[str, dict[str, list[str]]]:
    """Parse one tsconfig file, follow 'extends', and return (base_url, paths_map).

    Child compilerOptions override values inherited from the extended config.
    Cycles in the extends chain are silently broken via *seen*.
    """
    if rel_path in seen:
        return "", {}
    seen.add(rel_path)

    raw = _read_file(repo_path, rel_path)
    if not raw:
        return "", {}

    try:
        # tsconfig allows JS-style comments and trailing commas — strip them
        clean = re.sub(r"//[^\n]*", "", raw)
        clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
        clean = re.sub(r",\s*([}\]])", r"\1", clean)
        data = json.loads(clean)
    except Exception:
        return "", {}

    # Inherit from extended config first (parent is the base)
    base_url: str = ""
    paths: dict[str, list[str]] = {}
    extends = data.get("extends", "")
    if extends:
        config_dir = os.path.dirname(rel_path)
        if extends.startswith("."):
            ext_path = _posix(os.path.join(config_dir, extends) if config_dir else extends)
        else:
            ext_path = extends
        if not ext_path.endswith(".json"):
            ext_path += ".json"
        base_url, paths = _parse_tsconfig(repo_path, ext_path, seen)

    # Child compilerOptions override inherited values; strip leading './' from paths
    opts = data.get("compilerOptions", {})
    if "baseUrl" in opts:
        base_url = _strip_ts_dot_slash(opts["baseUrl"])
    if "paths" in opts:
        paths = {
            _strip_ts_dot_slash(alias): [_strip_ts_dot_slash(t) for t in targets]
            for alias, targets in opts["paths"].items()
        }

    return base_url, paths


def _load_tsconfig_aliases(repo_path: str) -> tuple[str, dict[str, list[str]]]:
    """Return (base_url, paths_map) from tsconfig.json (or tsconfig.base.json).

    Follows 'extends' chains recursively so aliases defined in a base config are
    inherited.  tsconfig.json is preferred over tsconfig.base.json.  Leading './'
    is stripped from all returned path strings so callers receive plain
    repo-relative values (e.g. './src/*' → 'src/*', '.' → '').
    """
    seen: set[str] = set()
    for name in ("tsconfig.json", "tsconfig.base.json"):
        if _read_file(repo_path, name):
            return _parse_tsconfig(repo_path, name, seen)
    return "", {}


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
        candidate = _posix(candidate)
        if candidate in all_files:
            return candidate
        # Try as package __init__
        candidate2 = os.path.join(base_dir, rest.replace(".", os.sep), "__init__.py")
        candidate2 = _posix(candidate2)
        if candidate2 in all_files:
            return candidate2
        return None

    # Absolute import: convert dots to path separators, normalise, then canonicalise to POSIX
    as_path = _posix(module.replace(".", os.sep) + ".py")
    if as_path in all_files:
        return as_path
    as_init = _posix(os.path.join(module.replace(".", os.sep), "__init__.py"))
    if as_init in all_files:
        return as_init
    return None


def _probe_js_extensions(raw: str, all_files: set[str]) -> str | None:
    """Try exact match then known JS/TS extensions and index fallbacks for a bare path."""
    if raw in all_files:
        return raw
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if raw + ext in all_files:
            return raw + ext
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        if raw + "/index" + ext in all_files:
            return raw + "/index" + ext
    return None


def _resolve_js_import(
    specifier: str,
    source_file: str,
    all_files: set[str],
    ts_base_url: str = "",
    ts_paths: dict[str, list[str]] | None = None,
) -> str | None:
    """Convert a JS/TS import specifier to a repo-relative file path."""
    if not specifier.startswith("."):
        # Try tsconfig paths aliases
        for alias, targets in (ts_paths or {}).items():
            if alias.endswith("/*"):
                prefix = alias[:-2]
                if specifier.startswith(prefix + "/"):
                    suffix = specifier[len(prefix) + 1:]
                    for target in targets:
                        base = target.rstrip("/*").rstrip("/")
                        r = _probe_js_extensions(base + "/" + suffix, all_files)
                        if r:
                            return r
            elif specifier == alias:
                for target in targets:
                    r = _probe_js_extensions(target.rstrip("/*"), all_files)
                    if r:
                        return r
        # Try baseUrl — treat specifier as relative to project root
        if ts_base_url:
            r = _probe_js_extensions(ts_base_url.rstrip("/") + "/" + specifier, all_files)
            if r:
                return r
        return None  # external package

    base_dir = os.path.dirname(source_file)
    raw = _posix(os.path.join(base_dir, specifier))
    return _probe_js_extensions(raw, all_files)


def _resolve_csharp_import(namespace: str, cs_namespace_map: dict[str, list[str]]) -> list[str]:
    """Look up a C# namespace in the pre-built namespace→files map."""
    return cs_namespace_map.get(namespace, [])


def _resolve_java_import(class_path: str, all_files: set[str]) -> str | None:
    """Convert a Java fully-qualified class name to a repo-relative .java file path.

    Tries conventional source roots (repo root, src/main/java/, etc.). For static
    imports the last component may be a method name; also tries without it.
    """
    rel = class_path.replace(".", "/") + ".java"
    for root in _JAVA_SOURCE_ROOTS:
        if (root + rel) in all_files:
            return root + rel
    # Static import fallback: drop last component (may be a method/field name)
    parts = class_path.rsplit(".", 1)
    if len(parts) == 2:
        rel2 = parts[0].replace(".", "/") + ".java"
        for root in _JAVA_SOURCE_ROOTS:
            if (root + rel2) in all_files:
                return root + rel2
    return None


def _resolve_java_wildcard(pkg_path: str, all_files: set[str]) -> list[str]:
    """Return .java files directly inside the given package directory (not subpackages).

    Java's `import pkg.*` only imports from the immediate package, not nested ones.
    """
    for root in _JAVA_SOURCE_ROOTS:
        prefix = root + pkg_path.replace(".", "/") + "/"
        matches = [
            f for f in all_files
            if f.startswith(prefix)
            and f.endswith(".java")
            and "/" not in f[len(prefix):]
        ]
        if matches:
            return matches
    return []


def _resolve_go_import(
    import_path: str,
    module_name: str,
    module_root: str,
    all_files: set[str],
) -> list[str]:
    """Resolve a Go import path to the .go source files in that package.

    Only internal packages (those sharing the module prefix) are resolved;
    stdlib, third-party, and vendored imports return an empty list.

    module_root is the repo-relative directory that contains the go.mod file
    (empty string when go.mod lives at the repo root).  Package directories are
    located at <module_root>/<suffix-after-module-name>/.
    """
    if not module_name or not import_path.startswith(module_name):
        return []
    suffix = import_path[len(module_name):].lstrip("/")
    if not suffix:
        return []  # importing the module root — not a useful edge
    pkg_dir = (module_root + "/" + suffix) if module_root else suffix
    prefix = pkg_dir + "/"
    return [
        f for f in all_files
        if f.startswith(prefix)
        and f.endswith(".go")
        and not f.endswith("_test.go")
        and not f.startswith("vendor/")
    ]


def _resolve_ruby_require_relative(
    specifier: str, source_file: str, all_files: set[str]
) -> str | None:
    """Resolve require_relative — always relative to the source file."""
    if not specifier.endswith(".rb"):
        specifier += ".rb"
    base_dir = os.path.dirname(source_file)
    candidate = _posix(os.path.join(base_dir, specifier))
    return candidate if candidate in all_files else None


def _resolve_ruby_require(
    specifier: str, source_file: str, all_files: set[str]
) -> str | None:
    """Resolve require — relative paths from source file; bare names tried at repo root
    then lib/ convention. External gem names return None."""
    if not specifier.endswith(".rb"):
        specifier += ".rb"
    if specifier.startswith("."):
        base_dir = os.path.dirname(source_file)
        candidate = _posix(os.path.join(base_dir, specifier))
        return candidate if candidate in all_files else None
    # Bare name — try repo-root first, then lib/ convention
    if specifier in all_files:
        return specifier
    lib_candidate = "lib/" + specifier
    if lib_candidate in all_files:
        return lib_candidate
    return None


def _extract_imports(
    content: str,
    source_file: str,
    language: str,
    all_files: set[str],
    cs_namespace_map: dict[str, list[str]] | None = None,
    go_module_name: str = "",
    go_module_root: str = "",
    ts_base_url: str = "",
    ts_paths: dict[str, list[str]] | None = None,
) -> list[str]:
    resolved: list[str] = []

    # --- AST-first path (Python and JS/TS only; others remain regex-based) ---
    if language in ("python", "javascript", "typescript"):
        ast_imports = ast_extract_imports(content, language)
        if ast_imports is not None:
            for ast_imp in ast_imports:
                spec = ast_imp.specifier
                if language == "python":
                    r = _resolve_python_module(spec, source_file, all_files)
                    if r:
                        resolved.append(r)
                else:
                    r = _resolve_js_import(spec, source_file, all_files, ts_base_url, ts_paths)
                    if r:
                        # Track re-exports as pass-through edges (barrel file support)
                        resolved.append(r)
            return list(set(resolved))
        # Fall through to regex path if AST returned None

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
            r = _resolve_js_import(m.group(1), source_file, all_files, ts_base_url, ts_paths)
            if r:
                resolved.append(r)
        for m in _JS_REQUIRE.finditer(content):
            r = _resolve_js_import(m.group(1), source_file, all_files, ts_base_url, ts_paths)
            if r:
                resolved.append(r)
        for m in _JS_PLAIN_IMPORT.finditer(content):
            r = _resolve_js_import(m.group(1), source_file, all_files, ts_base_url, ts_paths)
            if r:
                resolved.append(r)
    elif language == "csharp":
        for m in _CS_USING.finditer(content):
            resolved.extend(_resolve_csharp_import(m.group(1), cs_namespace_map or {}))
    elif language == "java":
        for m in _JAVA_IMPORT.finditer(content):
            if m.group(2):  # wildcard import (group 2 = '.*')
                # Try as a class first (handles `import static com.example.Util.*`)
                r = _resolve_java_import(m.group(1), all_files)
                if r:
                    resolved.append(r)
                else:
                    resolved.extend(_resolve_java_wildcard(m.group(1), all_files))
            else:
                r = _resolve_java_import(m.group(1), all_files)
                if r:
                    resolved.append(r)
    elif language == "go":
        for m in _GO_IMPORT_SINGLE.finditer(content):
            resolved.extend(_resolve_go_import(m.group(1), go_module_name, go_module_root, all_files))
        for block in _GO_IMPORT_BLOCK.finditer(content):
            for path_m in _GO_IMPORT_PATH.finditer(block.group(1)):
                resolved.extend(_resolve_go_import(path_m.group(1), go_module_name, go_module_root, all_files))
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

    # Per-file Go module lookup; cache avoids repeated go.mod reads for the same module
    go_module_cache: dict[str, tuple[str, str]] = {}

    # Load TypeScript/JavaScript path aliases from tsconfig.json
    ts_base_url, ts_paths = "", {}
    if "typescript" in language_filter or "javascript" in language_filter:
        ts_base_url, ts_paths = _load_tsconfig_aliases(repo_path)

    graph: dict[str, list[str]] = {}
    for rel_path in files:
        if rel_path.endswith("_test.go"):
            continue
        content = _read_file(repo_path, rel_path)
        lang = resolve_language(rel_path)
        go_module_name, go_module_root = "", ""
        if lang == "go":
            go_module_name, go_module_root = _find_go_module_for_file(
                repo_path, rel_path, go_module_cache
            )
        imports = _extract_imports(
            content, rel_path, lang, all_files, cs_namespace_map,
            go_module_name, go_module_root, ts_base_url, ts_paths,
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
