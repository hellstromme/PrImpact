import json
import os
import re
import sys
import tempfile
from pathlib import Path

import anthropic

from .models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    ChangedFile,
    Decision,
    TestGap,
    resolve_language,
)
from .prompts import (
    PROMPT_ANOMALY_DETECTION,
    PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS,
    PROMPT_TEST_GAP_ANALYSIS,
)

MODEL = "claude-sonnet-4-6"
MAX_RESPONSE_TOKENS = 4096
# Rough characters-per-token estimate for budget calculations
_CHARS_PER_TOKEN = 4
_DIFF_TOKEN_LIMIT = 8_000
_DIFF_CHAR_LIMIT = _DIFF_TOKEN_LIMIT * _CHARS_PER_TOKEN

# --- Signature extraction ---

_SIG_PY_KEEP = re.compile(
    r"^(?:[ \t]*(?:async\s+)?def\s+|[ \t]*class\s+|[ \t]*@\w+|import\s+|from\s+)"
)
_SIG_JS_KEEP = re.compile(
    r"^(?:import\s+|export\s+|(?:async\s+)?function\s+|class\s+|(?:const|let|var)\s+\w+\s*[=:])"
)

_TEST_PATTERNS = re.compile(r"(?:test_|_test\.|\.test\.|\.spec\.).*$", re.IGNORECASE)
_TEST_EXTENSIONS = {".py", ".ts", ".js", ".tsx", ".jsx"}



def _extract_signatures(content: str, language: str) -> str:
    """Return import lines and def/class declaration lines, stripping bodies."""
    lines = content.splitlines()
    keep = _SIG_PY_KEEP if language == "python" else _SIG_JS_KEEP
    return "\n".join(line.rstrip() for line in lines if keep.match(line))


def _read_file_safe(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


def _build_diffs_context(changed_files: list[ChangedFile]) -> str:
    parts: list[str] = []
    for f in changed_files:
        parts.append(f"### {f.path}\n{f.diff}")
    combined = "\n\n".join(parts)
    if len(combined) > _DIFF_CHAR_LIMIT and len(changed_files) > 1:
        # Truncate each file's diff proportionally
        per_file = _DIFF_CHAR_LIMIT // len(changed_files)
        parts = []
        for f in changed_files:
            diff = f.diff[:per_file]
            if len(f.diff) > per_file:
                diff += "\n... [truncated]"
            parts.append(f"### {f.path}\n{diff}")
        combined = "\n\n".join(parts)
    elif len(combined) > _DIFF_CHAR_LIMIT:
        combined = combined[:_DIFF_CHAR_LIMIT] + "\n... [truncated]"
    return combined


def _build_blast_radius_signatures(
    blast_radius: list[BlastRadiusEntry], repo_path: str, max_distance: int = 2
) -> str:
    parts: list[str] = []
    for entry in blast_radius:
        if entry.distance > max_distance:
            continue
        full_path = os.path.join(repo_path, entry.path)
        content = _read_file_safe(full_path)
        if not content:
            continue
        lang = resolve_language(entry.path)
        sigs = _extract_signatures(content, lang)
        if sigs:
            parts.append(f"### {entry.path} (distance {entry.distance})\n{sigs}")
    return "\n\n".join(parts) if parts else "(none)"


def _find_test_files(changed_files: list[ChangedFile], repo_path: str) -> str:
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
                content = _read_file_safe(entry.path)
                if content:
                    found[entry.name] = f"### {entry.path}\n{content[:4000]}"

    return "\n\n".join(found.values()) if found else "(no test files found)"


def _find_neighbouring_signatures(
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
            content = _read_file_safe(entry.path)
            if not content:
                continue
            file_lang = resolve_language(entry.name)
            sigs = _extract_signatures(content, file_lang)
            if sigs:
                parts.append(f"### {rel}\n{sigs}")
                count += 1
        seen_dirs[dir_rel] = count

    return "\n\n".join(parts) if parts else "(none)"


# --- API call ---


def _call_claude(client: anthropic.Anthropic, prompt: str) -> str:
    for attempt in range(2):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=MAX_RESPONSE_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            block = message.content[0]
            if not isinstance(block, anthropic.types.TextBlock):
                raise ValueError(f"Unexpected content block type: {type(block)}")
            return block.text
        except Exception:
            if attempt == 1:
                raise
    return ""  # unreachable


def _parse_json_safe(raw: str) -> dict:
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to extracting the first JSON object in the text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _log_response(label: str, raw: str) -> None:
    try:
        path = os.path.join(tempfile.gettempdir(), f"primpact_{label}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
    except Exception:
        pass


def _call_api(client: anthropic.Anthropic, prompt: str, label: str) -> dict:
    """Call Claude, log the raw response, and parse JSON. Returns {} on any failure."""
    try:
        raw = _call_claude(client, prompt)
        _log_response(label, raw)
        return _parse_json_safe(raw)
    except Exception as exc:
        print(f"[pr-impact] AI call '{label}' failed: {exc}", file=sys.stderr)
        return {}


# --- Public interface ---


def run_ai_analysis(
    changed_files: list[ChangedFile],
    blast_radius: list[BlastRadiusEntry],
    repo_path: str,
) -> AIAnalysis:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set — AI analysis skipped. "
            "Set the key in the environment or in ~/.pr_impact/config.toml."
        )
    client = anthropic.Anthropic(api_key=api_key)
    result = AIAnalysis()

    diffs_ctx = _build_diffs_context(changed_files)
    blast_sigs = _build_blast_radius_signatures(blast_radius, repo_path)

    # Call 1: summary, decisions, assumptions
    data1 = _call_api(
        client,
        PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS.format(
            changed_files_diff=diffs_ctx,
            blast_radius_signatures=blast_sigs,
        ),
        "call1_summary",
    )
    result.summary = data1.get("summary", "")
    result.decisions = [
        Decision(
            description=d.get("description", ""),
            rationale=d.get("rationale", ""),
            risk=d.get("risk", ""),
        )
        for d in data1.get("decisions", [])
        if isinstance(d, dict)
    ]
    result.assumptions = [
        Assumption(
            description=a.get("description", ""),
            location=a.get("location", ""),
            risk=a.get("risk", ""),
        )
        for a in data1.get("assumptions", [])
        if isinstance(a, dict)
    ]

    # Call 2: anomaly detection
    try:
        neighbour_sigs = _find_neighbouring_signatures(changed_files, repo_path)
    except Exception as exc:
        print(f"[pr-impact] Neighbour signature collection failed: {exc}", file=sys.stderr)
        neighbour_sigs = "(none)"
    data2 = _call_api(
        client,
        PROMPT_ANOMALY_DETECTION.format(
            changed_files_diff=diffs_ctx,
            neighbouring_signatures=neighbour_sigs,
        ),
        "call2_anomalies",
    )
    result.anomalies = [
        Anomaly(
            description=a.get("description", ""),
            location=a.get("location", ""),
            severity=a.get("severity", "low"),
        )
        for a in data2.get("anomalies", [])
        if isinstance(a, dict)
    ]

    # Call 3: test gap analysis
    try:
        test_ctx = _find_test_files(changed_files, repo_path)
    except Exception as exc:
        print(f"[pr-impact] Test file collection failed: {exc}", file=sys.stderr)
        test_ctx = "(no test files found)"
    data3 = _call_api(
        client,
        PROMPT_TEST_GAP_ANALYSIS.format(
            changed_files_diff=diffs_ctx,
            test_files=test_ctx,
        ),
        "call3_test_gaps",
    )
    result.test_gaps = [
        TestGap(
            behaviour=t.get("behaviour", ""),
            location=t.get("location", ""),
        )
        for t in data3.get("test_gaps", [])
        if isinstance(t, dict)
    ]

    return result
