"""Language-specific import resolution for the PrImpact dependency graph.

Contains:
- Regex patterns for each supported language
- File I/O helpers used during resolution
- Per-language resolver functions (_resolve_python_module, _resolve_js_import, etc.)
- _extract_imports: AST-first, regex-fallback orchestrator

Called only by dependency_graph.py.
"""

import json
import os
import re
from dataclasses import dataclass, field

from .ast_extractor import extract_imports as ast_extract_imports
from .utils import read_file_safe


@dataclass
class ImportResolutionConfig:
    """Language-specific context for import resolution.

    Assembled once per graph build in build_import_graph() and passed to
    extract_imports_for_file() for every file in the loop. Fields not relevant
    to a file's language are silently ignored by the resolver.
    """

    cs_namespace_map: dict[str, list[str]] | None = None
    go_module_name: str = ""
    go_module_root: str = ""
    ts_base_url: str = ""
    ts_paths: dict[str, list[str]] | None = field(default_factory=dict)


def _posix(path: str) -> str:
    """Normalise *path* to a forward-slash string, stable on Windows."""
    return os.path.normpath(path).replace("\\", "/")


def read_file(repo_path: str, rel_path: str) -> str:
    return read_file_safe(os.path.join(repo_path, rel_path))


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Go module helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_go_module_for_file(
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
        content = read_file(repo_path, mod_rel)
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


# ─────────────────────────────────────────────────────────────────────────────
# TypeScript/tsconfig helpers
# ─────────────────────────────────────────────────────────────────────────────

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

    raw = read_file(repo_path, rel_path)
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


def load_tsconfig_aliases(repo_path: str) -> tuple[str, dict[str, list[str]]]:
    """Return (base_url, paths_map) from tsconfig.json (or tsconfig.base.json).

    Follows 'extends' chains recursively so aliases defined in a base config are
    inherited.  tsconfig.json is preferred over tsconfig.base.json.  Leading './'
    is stripped from all returned path strings so callers receive plain
    repo-relative values (e.g. './src/*' → 'src/*', '.' → '').
    """
    seen: set[str] = set()
    for name in ("tsconfig.json", "tsconfig.base.json"):
        if read_file(repo_path, name):
            return _parse_tsconfig(repo_path, name, seen)
    return "", {}


# ─────────────────────────────────────────────────────────────────────────────
# Per-language resolver functions
# ─────────────────────────────────────────────────────────────────────────────

def resolve_python_module(module: str, source_file: str, all_files: set[str]) -> str | None:
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
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if raw + "/index" + ext in all_files:
            return raw + "/index" + ext
    return None


def resolve_js_import(
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


def resolve_csharp_import(namespace: str, cs_namespace_map: dict[str, list[str]]) -> list[str]:
    """Look up a C# namespace in the pre-built namespace→files map."""
    return cs_namespace_map.get(namespace, [])


def resolve_java_import(class_path: str, all_files: set[str]) -> str | None:
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


def resolve_java_wildcard(pkg_path: str, all_files: set[str]) -> list[str]:
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


def resolve_go_import(
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
    if not module_name or (import_path != module_name and not import_path.startswith(module_name + "/")):
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


def resolve_ruby_require_relative(
    specifier: str, source_file: str, all_files: set[str]
) -> str | None:
    """Resolve require_relative — always relative to the source file."""
    if not specifier.endswith(".rb"):
        specifier += ".rb"
    base_dir = os.path.dirname(source_file)
    candidate = _posix(os.path.join(base_dir, specifier))
    return candidate if candidate in all_files else None


def resolve_ruby_require(
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


# ─────────────────────────────────────────────────────────────────────────────
# Import extraction orchestrator (AST-first, regex fallback)
# ─────────────────────────────────────────────────────────────────────────────

def extract_imports_for_file(
    content: str,
    source_file: str,
    language: str,
    all_files: set[str],
    config: ImportResolutionConfig | None = None,
) -> list[str]:
    """Return repo-relative paths of files imported by source_file.

    Uses AST extraction for Python/JS/TS with a regex fallback for all languages.
    Language-specific settings (TypeScript aliases, C# namespace map, Go module
    info) are passed via *config*; defaults to empty config when omitted.
    """
    if config is None:
        config = ImportResolutionConfig()

    resolved: list[str] = []

    # --- AST-first path (Python and JS/TS only; others remain regex-based) ---
    if language in ("python", "javascript", "typescript"):
        ast_imports = ast_extract_imports(content, language)
        if ast_imports is not None:
            for ast_imp in ast_imports:
                spec = ast_imp.specifier
                if language == "python":
                    r = resolve_python_module(spec, source_file, all_files)
                    if r:
                        resolved.append(r)
                else:
                    r = resolve_js_import(spec, source_file, all_files, config.ts_base_url, config.ts_paths)
                    if r:
                        # Track re-exports as pass-through edges (barrel file support)
                        resolved.append(r)
            return list(set(resolved))
        # Fall through to regex path if AST returned None

    if language == "python":
        for m in _PY_IMPORT.finditer(content):
            r = resolve_python_module(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _PY_FROM.finditer(content):
            r = resolve_python_module(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
    elif language in ("javascript", "typescript"):
        for m in _JS_IMPORT_FROM.finditer(content):
            r = resolve_js_import(m.group(1), source_file, all_files, config.ts_base_url, config.ts_paths)
            if r:
                resolved.append(r)
        for m in _JS_REQUIRE.finditer(content):
            r = resolve_js_import(m.group(1), source_file, all_files, config.ts_base_url, config.ts_paths)
            if r:
                resolved.append(r)
        for m in _JS_PLAIN_IMPORT.finditer(content):
            r = resolve_js_import(m.group(1), source_file, all_files, config.ts_base_url, config.ts_paths)
            if r:
                resolved.append(r)
    elif language == "csharp":
        for m in _CS_USING.finditer(content):
            resolved.extend(resolve_csharp_import(m.group(1), config.cs_namespace_map or {}))
    elif language == "java":
        for m in _JAVA_IMPORT.finditer(content):
            if m.group(2):  # wildcard import (group 2 = '.*')
                # Try as a class first (handles `import static com.example.Util.*`)
                r = resolve_java_import(m.group(1), all_files)
                if r:
                    resolved.append(r)
                else:
                    resolved.extend(resolve_java_wildcard(m.group(1), all_files))
            else:
                r = resolve_java_import(m.group(1), all_files)
                if r:
                    resolved.append(r)
    elif language == "go":
        for m in _GO_IMPORT_SINGLE.finditer(content):
            resolved.extend(resolve_go_import(m.group(1), config.go_module_name, config.go_module_root, all_files))
        for block in _GO_IMPORT_BLOCK.finditer(content):
            for path_m in _GO_IMPORT_PATH.finditer(block.group(1)):
                resolved.extend(resolve_go_import(path_m.group(1), config.go_module_name, config.go_module_root, all_files))
    elif language == "ruby":
        for m in _RUBY_REQUIRE.finditer(content):
            r = resolve_ruby_require(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)
        for m in _RUBY_REQUIRE_REL.finditer(content):
            r = resolve_ruby_require_relative(m.group(1), source_file, all_files)
            if r:
                resolved.append(r)

    return list(set(resolved))
