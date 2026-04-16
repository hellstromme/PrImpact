"""Context and prompt builders for PrImpact's AI analysis layer.

Assembles the text blocks (diffs, signatures, test files, historical context, security
signals) that are injected into each Claude prompt. Pure data transformation — no API
calls. Called only by ai_layer.py.
"""

import os
import re
from pathlib import Path

from .models import BlastRadiusEntry, ChangedFile, SecuritySignal, resolve_language
from .utils import read_file_safe

# Rough characters-per-token estimate for budget calculations
_CHARS_PER_TOKEN = 4
_DIFF_TOKEN_LIMIT = 8_000
_DIFF_CHAR_LIMIT = _DIFF_TOKEN_LIMIT * _CHARS_PER_TOKEN
_TRUNCATION_SUFFIX = "\n... [truncated]"

_SIG_PY_KEEP = re.compile(
    r"^(?:[ \t]*(?:async\s+)?def\s+|[ \t]*class\s+|[ \t]*@\w+|import\s+|from\s+)"
)
_SIG_JS_KEEP = re.compile(
    r"^(?:import\s+|export\s+|(?:async\s+)?function\s+|class\s+|(?:const|let|var)\s+\w+\s*[=:])"
)
_SIG_GO_KEEP = re.compile(
    r"^(?:import\s+|func\s+|type\s+|var\s+|const\s+|package\s+)"
)
_SIG_JAVA_KEEP = re.compile(
    r"^(?:import\s+|package\s+|(?:(?:public|private|protected|static|abstract|final|synchronized|default)\s+)*(?:class|interface|enum|@interface)\s+|(?:(?:public|private|protected|static|abstract|final|synchronized|native|default)\s+)+[\w<\[\]]+\s+\w+\s*\()"
)
_SIG_CS_KEEP = re.compile(
    r"^(?:using\s+|namespace\s+|(?:(?:public|private|protected|internal|static|abstract|sealed|partial|virtual|override|readonly|async|extern)\s+)*(?:class|interface|struct|enum|record)\s+|(?:(?:public|private|protected|internal|static|abstract|virtual|override|readonly|async|sealed|partial)\s+)+[\w<\[\]?]+\s+\w+\s*[\(;])"
)
_SIG_RUBY_KEEP = re.compile(
    r"^(?:require\s+|require_relative\s+|def\s+|class\s+|module\s+|attr_(?:reader|writer|accessor)\s+)"
)

_TEST_EXTENSIONS = {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".java", ".cs", ".rb"}
# Max chars of raw file content to include when no test names can be extracted
_FALLBACK_FILE_CHARS = 4_000

_SIG_KEEP_BY_LANGUAGE: dict[str, re.Pattern] = {
    "python": _SIG_PY_KEEP,
    "javascript": _SIG_JS_KEEP,
    "typescript": _SIG_JS_KEEP,
    "go": _SIG_GO_KEEP,
    "java": _SIG_JAVA_KEEP,
    "csharp": _SIG_CS_KEEP,
    "ruby": _SIG_RUBY_KEEP,
}


def _extract_signatures(content: str, language: str) -> str:
    """Return import lines and def/class declaration lines, stripping bodies."""
    keep = _SIG_KEEP_BY_LANGUAGE.get(language, _SIG_JS_KEEP)
    lines = content.splitlines()
    return "\n".join(line.rstrip() for line in lines if keep.match(line))



def build_diffs_context(
    changed_files: list[ChangedFile],
    high_sensitivity_modules: list[str] | None = None,
) -> str:
    header_overhead = sum(len(f"### {f.path}\n") for f in changed_files)
    sep_overhead = max(0, len(changed_files) - 1) * len("\n\n")
    available = _DIFF_CHAR_LIMIT - header_overhead - sep_overhead

    total_diffs = sum(len(f.diff) for f in changed_files)
    if total_diffs <= available:
        result = "\n\n".join(f"### {f.path}\n{f.diff}" for f in changed_files)
    elif len(changed_files) > 1:
        # Greedily include each file in full until the budget is exhausted
        parts: list[str] = []
        remaining = available
        for f in changed_files:
            if remaining <= 0:
                parts.append(f"### {f.path}\n... [truncated]")
            elif len(f.diff) <= remaining:
                parts.append(f"### {f.path}\n{f.diff}")
                remaining -= len(f.diff)
            else:
                diff_chars = max(0, remaining - len(_TRUNCATION_SUFFIX))
                parts.append(f"### {f.path}\n{f.diff[:diff_chars]}{_TRUNCATION_SUFFIX}")
                remaining = 0
        result = "\n\n".join(parts)
    else:
        # Single file exceeds limit
        single = changed_files[0]
        single_available = max(0, available - len(_TRUNCATION_SUFFIX))
        result = f"### {single.path}\n{single.diff[:single_available]}{_TRUNCATION_SUFFIX}"

    # Appended after budget calculation — intentionally outside the char limit
    # because the annotation is small (O(module count) chars) and fixed-size.
    if high_sensitivity_modules:
        module_list = "\n".join(f"- {m}" for m in high_sensitivity_modules)
        result += (
            "\n\n## High-Sensitivity Modules\n"
            "The following paths have been marked as requiring extra scrutiny:\n"
            + module_list
        )

    return result


def build_blast_radius_signatures(
    blast_radius: list[BlastRadiusEntry], repo_path: str, max_distance: int = 2
) -> str:
    parts: list[str] = []
    for entry in blast_radius:
        if entry.distance > max_distance:
            continue
        full_path = os.path.join(repo_path, entry.path)
        content = read_file_safe(full_path)
        if not content:
            continue
        lang = resolve_language(entry.path)
        sigs = _extract_signatures(content, lang)
        if sigs:
            parts.append(f"### {entry.path} (distance {entry.distance})\n{sigs}")
    return "\n\n".join(parts) if parts else "(none)"


def find_test_files(changed_files: list[ChangedFile], repo_path: str) -> str:
    found: dict[str, str] = {}
    for f in changed_files:
        stem = Path(f.path).stem
        search_dirs = [
            os.path.dirname(os.path.join(repo_path, f.path)),
            os.path.join(repo_path, "tests"),
            os.path.join(repo_path, "test"),
        ]
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for entry in os.scandir(search_dir):
                if entry.name in found:
                    continue
                if Path(entry.name).suffix not in _TEST_EXTENSIONS:
                    continue
                name_lower = entry.name.lower()
                if "test" not in name_lower and "spec" not in name_lower:
                    continue
                stem_lower = stem.lower().lstrip("_")
                if stem_lower and stem_lower not in name_lower:
                    continue
                content = read_file_safe(entry.path)
                if not content:
                    continue
                # Extract test names for a complete coverage picture.
                # Python: match sync and async test functions (async def test_* is common in pytest-asyncio).
                py_names = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", content, re.MULTILINE)
                # JS/TS: extract it/test/describe block titles.
                js_names = re.findall(
                    r"""\b(?:it|test|describe)\s*\(\s*["'`]([^"'`\n]+)["'`]""", content
                )
                test_names = py_names + js_names
                if test_names:
                    body = "\n".join(test_names)
                else:
                    body = content[:_FALLBACK_FILE_CHARS]  # non-standard structure — fall back to partial content
                found[entry.name] = f"### {entry.path}\n{body}"

    return "\n\n".join(found.values()) if found else "(no test files found)"


def find_neighbouring_signatures(
    changed_files: list[ChangedFile], repo_path: str, max_per_dir: int = 5
) -> str:
    changed_paths = {f.path for f in changed_files}
    seen_dirs: dict[str, int] = {}
    parts: list[str] = []

    for f in changed_files:
        dir_rel = os.path.dirname(f.path)
        dir_abs = os.path.join(repo_path, dir_rel) if dir_rel else repo_path
        if not os.path.isdir(dir_abs):
            continue
        count = seen_dirs.get(dir_rel, 0)
        if count >= max_per_dir:
            continue
        for entry in os.scandir(dir_abs):
            if count >= max_per_dir:
                break
            rel = os.path.join(dir_rel, entry.name).replace("\\", "/") if dir_rel else entry.name
            if rel in changed_paths or resolve_language(entry.name) == "unknown":
                continue
            content = read_file_safe(entry.path)
            if not content:
                continue
            file_lang = resolve_language(entry.name)
            sigs = _extract_signatures(content, file_lang)
            if sigs:
                parts.append(f"### {rel}\n{sigs}")
                count += 1
        seen_dirs[dir_rel] = count

    return "\n\n".join(parts) if parts else "(none)"


def build_changed_files_before_signatures(changed_files: list[ChangedFile]) -> str:
    """Return import/declaration signatures from the before-state of each changed file.

    Anomaly detection excludes changed files from the neighbour set, so on large PRs
    that touch many files in one package the 'established patterns' context is nearly
    empty.  Providing the before-signatures of the changed files themselves gives
    Claude evidence of what patterns already existed — e.g. that cli.py already
    imported from classifier, dependency_graph, git_analysis etc. before this PR,
    establishing that direct module calls from the orchestration layer are the norm.
    """
    parts: list[str] = []
    for f in changed_files:
        if not f.content_before:
            continue
        lang = resolve_language(f.path)
        sigs = _extract_signatures(f.content_before, lang)
        if sigs:
            parts.append(f"### {f.path} (before this PR)\n{sigs}")
    return "\n\n".join(parts) if parts else "(none)"


def build_signatures_before_after(changed_files: list[ChangedFile]) -> str:
    """Build a compact before/after signature comparison for semantic equivalence detection."""
    parts: list[str] = []
    for f in changed_files:
        lang = resolve_language(f.path)
        before_sigs = _extract_signatures(f.content_before, lang) if f.content_before else "(new file)"
        after_sigs = _extract_signatures(f.content_after, lang) if f.content_after else "(deleted file)"
        if before_sigs != after_sigs:
            parts.append(
                f"### {f.path}\n"
                f"**Before:**\n{before_sigs}\n\n"
                f"**After:**\n{after_sigs}"
            )
    return "\n\n".join(parts) if parts else "(no signature changes)"


def build_security_signals_context(
    pattern_signals: list[SecuritySignal],
    changed_files: list[ChangedFile],
) -> tuple[str, str]:
    """Return (signals_text, file_context_text) for the security prompt."""
    if not pattern_signals:
        signals_text = "(none)"
    else:
        parts: list[str] = []
        for sig in pattern_signals:
            line_info = f" line {sig.location.line}" if sig.location.line else ""
            symbol_info = f":{sig.location.symbol}" if sig.location.symbol else ""
            parts.append(
                f"- [{sig.severity.upper()}] {sig.signal_type}: {sig.description}"
                f"  ({sig.location.file}{symbol_info}{line_info})"
            )
        signals_text = "\n".join(parts)

    ctx_parts: list[str] = []
    for f in changed_files:
        if not f.content_before:
            continue
        lang = resolve_language(f.path)
        sigs = _extract_signatures(f.content_before, lang)
        if sigs:
            ctx_parts.append(f"### {f.path} (before)\n{sigs}")
    file_context = "\n\n".join(ctx_parts) if ctx_parts else "(no prior content)"
    return signals_text, file_context


def build_historical_context(
    anomaly_history: list[dict] | None,
    hotspots: list[dict] | None,
) -> str:
    """Build a historical context section to append to the anomaly detection prompt."""
    parts: list[str] = []
    if hotspots:
        lines = [f"- {h['file']} ({h['appearances']} appearances)" for h in hotspots[:10]]
        parts.append(
            "## Historical context for this repo\n"
            "Files that frequently appear in blast radii (architectural hotspots):\n"
            + "\n".join(lines)
        )
    if anomaly_history:
        lines = [f"- {a['file']}: {a['description']}" for a in anomaly_history[:10]]
        parts.append(
            "Recurring anomaly patterns from past analyses:\n" + "\n".join(lines)
        )
    return "\n\n".join(parts)
