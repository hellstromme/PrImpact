"""Deterministic security signal detection and dependency integrity checks.

Two public functions:
  detect_pattern_signals(changed_files)   → list[SecuritySignal]
  check_dependency_integrity(changed_files) → list[DependencyIssue]

Both catch their own exceptions and return empty lists on failure.
No imports from other pr_impact modules except models.py.
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .models import ChangedFile, DependencyIssue, SecuritySignal

# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

# Patterns for added diff lines (language-agnostic unless noted).
# Each entry: (signal_type, compiled_regex, default_severity)
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Hardcoded IP addresses (any language)
    ("network_call",       re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),                                      "high"),
    # Python network
    ("network_call",       re.compile(r'\brequests\.(get|post|put|delete|patch|head)\s*\('),                  "medium"),
    ("network_call",       re.compile(r'\burllib\.request\.(urlopen|urlretrieve)\s*\('),                      "medium"),
    ("network_call",       re.compile(r'\bhttp\.client\.HTTPSConnection\s*\('),                               "medium"),
    ("network_call",       re.compile(r'\bsocket\.(?:connect|create_connection)\s*\('),                       "high"),
    # JS/TS network
    ("network_call",       re.compile(r'\bfetch\s*\('),                                                       "medium"),
    ("network_call",       re.compile(r'\baxios\.(?:get|post|put|delete|patch)\s*\('),                        "medium"),
    ("network_call",       re.compile(r'\bnew\s+WebSocket\s*\('),                                             "high"),
    ("network_call",       re.compile(r'\bnet\.(?:connect|createConnection|createServer)\s*\('),               "high"),
    # Ruby network
    ("network_call",       re.compile(r'\bNet::HTTP(?:\.get|\.post|\.start)?\b'),                             "medium"),
    ("network_call",       re.compile(r'\bRestClient\.(?:get|post|put|delete)\b'),                            "medium"),
    # Go network
    ("network_call",       re.compile(r'\bhttp\.(?:Get|Post|NewRequest)\s*\('),                               "medium"),
    ("network_call",       re.compile(r'\bnet\.Dial\s*\('),                                                   "high"),

    # Credentials assigned to string literals
    ("credential",         re.compile(
        r'(?:api[_-]?key|apikey|secret[_-]?key|password|passwd|token|credential|auth[_-]?token)'
        r'\s*(?:=|:)\s*["\'][^"\']{8,}["\']',
        re.IGNORECASE,
    ),                                                                                                         "high"),

    # Encoded payloads
    ("encoded_payload",    re.compile(r'\bbase64\.(?:b64decode|decodebytes|urlsafe_b64decode)\s*\('),         "medium"),
    ("encoded_payload",    re.compile(r'\batob\s*\('),                                                        "medium"),
    ("encoded_payload",    re.compile(r'\bBuffer\.from\s*\([^)]*,\s*["\']hex["\']\s*\)'),                     "medium"),
    ("encoded_payload",    re.compile(r'\bbytes\.fromhex\s*\('),                                              "medium"),

    # Dynamic execution
    ("dynamic_exec",       re.compile(r'\beval\s*\('),                                                        "high"),
    ("dynamic_exec",       re.compile(r'\bexec\s*\('),                                                        "high"),
    ("dynamic_exec",       re.compile(r'\bnew\s+Function\s*\('),                                              "high"),
    ("dynamic_exec",       re.compile(r'\bsubprocess\b.*\bshell\s*=\s*True'),                                 "high"),
    ("dynamic_exec",       re.compile(r'\bcompile\s*\([^)]+,\s*["\']exec["\']'),                             "medium"),

    # Shell invocation
    ("shell_invoke",       re.compile(r'\bos\.system\s*\('),                                                  "medium"),
    ("shell_invoke",       re.compile(r'\bsubprocess\.(?:run|call|Popen|check_output|check_call)\s*\('),      "medium"),
    ("shell_invoke",       re.compile(r'\bchild_process\.(?:exec|spawn|execSync|spawnSync|execFile)\s*\('),   "medium"),
    ("shell_invoke",       re.compile(r'\bKernel\.(?:system|exec|spawn)\b'),                                  "medium"),
    ("shell_invoke",       re.compile(r'\bOpen3\.(?:popen3|capture3|pipeline)\b'),                            "medium"),
    ("shell_invoke",       re.compile(r'\bos/exec\.Command\s*\('),                                            "medium"),

    # Suspicious imports (only meaningful on lines that look like import statements)
    ("suspicious_import",  re.compile(r'^(?:\+\s*)?(?:import|from)\s+(?:socket|ctypes|_ctypes|cffi|pty)\b'), "medium"),
    ("suspicious_import",  re.compile(r'require\s*\(\s*["\'](?:child_process|dgram|tls|cluster)["\']\s*\)'), "medium"),
    ("suspicious_import",  re.compile(r'from\s+["\'](?:child_process|dgram|tls|cluster)["\']\s*import'),     "medium"),
]

# File paths containing these segments are "infrastructure" contexts where
# shell/network patterns are expected → downgrade HIGH→MEDIUM, MEDIUM→LOW.
_INFRA_PATH_SEGMENTS = frozenset({
    "build", "deploy", "ci", "scripts", "tools", "devops",
    "makefile", "dockerfile", "setup", "install", "bootstrap",
})

_SEVERITY_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


def _is_infra_file(path: str) -> bool:
    parts = {p.lower() for p in Path(path).parts}
    return bool(parts & _INFRA_PATH_SEGMENTS)


def _pattern_in_before(content_before: str, pattern: re.Pattern) -> bool:
    return bool(pattern.search(content_before))


def _added_lines(diff: str) -> list[tuple[int, str]]:
    """Return (line_number, text) pairs for added lines in a unified diff."""
    results: list[tuple[int, str]] = []
    current_new_line = 0
    for raw_line in diff.splitlines():
        if raw_line.startswith("@@"):
            # Parse @@ -old,old +new,new @@ to get starting new line number
            m = re.search(r"\+(\d+)", raw_line)
            current_new_line = int(m.group(1)) - 1 if m else 0
        elif raw_line.startswith("+++"):
            pass
        elif raw_line.startswith("+"):
            current_new_line += 1
            results.append((current_new_line, raw_line[1:]))
        elif not raw_line.startswith("-"):
            current_new_line += 1
    return results


def detect_pattern_signals(changed_files: list[ChangedFile]) -> list[SecuritySignal]:
    """Scan changed file diffs for high-signal security patterns.

    Returns a list of SecuritySignal. Per-file failures are skipped silently;
    unexpected top-level errors propagate to the caller (cli.py catches and warns).
    """
    signals: list[SecuritySignal] = []
    for f in changed_files:
        try:
            infra = _is_infra_file(f.path)
            added = _added_lines(f.diff)
            for line_no, line_text in added:
                for signal_type, pattern, base_severity in _PATTERNS:
                    if not pattern.search(line_text):
                        continue
                    # Downgrade if the same pattern already existed before the change
                    if _pattern_in_before(f.content_before, pattern):
                        severity = "low"
                    elif infra and signal_type in ("shell_invoke", "network_call"):
                        severity = _SEVERITY_DOWNGRADE.get(base_severity, base_severity)
                    else:
                        severity = base_severity

                    match_text = pattern.search(line_text).group(0)  # type: ignore[union-attr]
                    description = f"New {signal_type.replace('_', ' ')}: `{match_text.strip()}`"
                    why_unusual = (
                        "Same pattern exists in the file before this change — included for completeness."
                        if severity == "low" and _pattern_in_before(f.content_before, pattern)
                        else f"Pattern `{signal_type}` added to `{f.path}`."
                    )
                    signals.append(SecuritySignal(
                        description=description,
                        file_path=f.path,
                        line_number=line_no,
                        signal_type=signal_type,
                        severity=severity,
                        why_unusual=why_unusual,
                        suggested_action="Confirm with the PR author that this is intentional.",
                    ))
        except Exception:
            pass  # skip this file; continue with others
    return signals


# ---------------------------------------------------------------------------
# Dependency integrity
# ---------------------------------------------------------------------------

# Top packages per ecosystem for typosquatting detection (edit distance ≤ 2)
_TOP_PACKAGES: dict[str, list[str]] = {
    "pypi": [
        "requests", "numpy", "pandas", "flask", "django", "fastapi", "sqlalchemy",
        "boto3", "pytest", "pydantic", "httpx", "celery", "redis", "pillow",
        "scipy", "matplotlib", "tensorflow", "torch", "transformers", "cryptography",
        "paramiko", "fabric", "ansible", "click", "typer", "rich", "black", "ruff",
    ],
    "npm": [
        "react", "lodash", "express", "axios", "moment", "webpack", "babel",
        "typescript", "eslint", "jest", "mocha", "vue", "angular", "next",
        "nuxt", "svelte", "prettier", "rollup", "vite", "esbuild", "chalk",
        "commander", "yargs", "dotenv", "mongoose", "sequelize", "prisma",
    ],
    "rubygems": [
        "rails", "rake", "bundler", "sinatra", "rspec", "minitest", "devise",
        "nokogiri", "httparty", "faraday", "sidekiq", "puma", "unicorn",
        "activerecord", "activesupport", "carrierwave", "paperclip",
    ],
    "go": [
        "gin", "echo", "fiber", "gorilla/mux", "chi", "grpc", "cobra",
        "viper", "zap", "logrus", "testify", "gorm", "sqlx", "redis",
    ],
}

_OSV_ECOSYSTEMS: dict[str, str] = {
    "pypi": "PyPI",
    "npm": "npm",
    "rubygems": "RubyGems",
    "go": "Go",
}

# Manifest file name → ecosystem key
_MANIFEST_ECOSYSTEM: dict[str, str] = {
    "requirements.txt": "pypi",
    "requirements-dev.txt": "pypi",
    "requirements-test.txt": "pypi",
    "pyproject.toml": "pypi",
    "package.json": "npm",
    "Gemfile": "rubygems",
    "go.mod": "go",
}

# Regex patterns to extract package names from added manifest lines
_MANIFEST_PARSERS: dict[str, re.Pattern] = {
    "pypi":      re.compile(r'^([A-Za-z0-9_\-\.]+)\s*(?:[=<>!~]+|$)'),
    "npm":       re.compile(r'^\s*"([^"@][^"]*?)"\s*:'),
    "rubygems":  re.compile(r"gem\s+['\"]([A-Za-z0-9_\-]+)['\"]"),
    "go":        re.compile(r'^\s*([^\s]+)\s+v[\d\.]+'),
}


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[len(b)]


def _is_typosquat(name: str, ecosystem: str) -> str | None:
    """Return the similar popular package name if name looks like a typosquat, else None."""
    name_lower = name.lower().replace("-", "_")
    for top in _TOP_PACKAGES.get(ecosystem, []):
        top_lower = top.lower().replace("-", "_")
        if name_lower == top_lower:
            return None  # exact match — not a typosquat
        dist = _levenshtein(name_lower, top_lower)
        if 1 <= dist <= 2:
            return top
    return None


def _osv_check(package_name: str, ecosystem: str, timeout: float = 5.0) -> list[str]:
    """Query OSV for known vulnerabilities. Returns list of vuln IDs, or [] on any error."""
    osv_ecosystem = _OSV_ECOSYSTEMS.get(ecosystem)
    if not osv_ecosystem:
        return []
    try:
        payload = json.dumps({"package": {"name": package_name, "ecosystem": osv_ecosystem}}).encode()
        req = urllib.request.Request(
            "https://api.osv.dev/v1/query",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return [v["id"] for v in data.get("vulns", [])]
    except Exception:
        return []


def _parse_new_packages(diff: str, ecosystem: str) -> list[str]:
    """Extract package names from added lines in a manifest diff."""
    parser = _MANIFEST_PARSERS.get(ecosystem)
    if not parser:
        return []
    packages: list[str] = []
    for raw_line in diff.splitlines():
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        line = raw_line[1:].strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = parser.match(line)
        if m:
            pkg = m.group(1).strip()
            if pkg and len(pkg) >= 2:
                packages.append(pkg)
    return packages


def _detect_version_changes(diff: str, ecosystem: str) -> list[str]:
    """Return package names whose version pin changed (present in both - and + lines)."""
    parser = _MANIFEST_PARSERS.get(ecosystem)
    if not parser:
        return []
    removed: set[str] = set()
    added: set[str] = set()
    for raw_line in diff.splitlines():
        if raw_line.startswith("---") or raw_line.startswith("+++"):
            continue
        line = raw_line[1:].strip()
        m = parser.match(line)
        if not m:
            continue
        pkg = m.group(1).strip()
        if raw_line.startswith("-"):
            removed.add(pkg)
        elif raw_line.startswith("+"):
            added.add(pkg)
    return sorted(removed & added)


def check_dependency_integrity(
    changed_files: list[ChangedFile],
    osv_check: bool = False,
) -> list[DependencyIssue]:
    """Check package manifest changes for typosquatting, version changes, and CVEs.

    osv_check: when True, query the OSV API for known vulnerabilities in new packages.
    Disabled by default — enable with --check-osv on the CLI.

    Per-file failures are skipped silently; unexpected top-level errors propagate
    to the caller (cli.py catches and warns).
    """
    issues: list[DependencyIssue] = []
    for f in changed_files:
        try:
            filename = Path(f.path).name
            ecosystem = _MANIFEST_ECOSYSTEM.get(filename)
            if ecosystem is None:
                # Try prefix match for requirements*.txt
                if re.match(r"requirements.*\.txt$", filename, re.IGNORECASE):
                    ecosystem = "pypi"
                else:
                    continue

            new_packages = _parse_new_packages(f.diff, ecosystem)
            version_changed = _detect_version_changes(f.diff, ecosystem)

            for pkg in new_packages:
                similar = _is_typosquat(pkg, ecosystem)
                if similar:
                    issues.append(DependencyIssue(
                        package_name=pkg,
                        issue_type="typosquat",
                        description=(
                            f"`{pkg}` is very similar to the popular package `{similar}` "
                            f"(edit distance ≤ 2). Verify this is intentional."
                        ),
                        severity="high",
                    ))

                if osv_check:
                    vulns = _osv_check(pkg, ecosystem)
                    if vulns:
                        vuln_list = ", ".join(vulns[:5])
                        issues.append(DependencyIssue(
                            package_name=pkg,
                            issue_type="vulnerability",
                            description=(
                                f"`{pkg}` has known vulnerabilities: {vuln_list}. "
                                "Review before merging."
                            ),
                            severity="high",
                        ))

            for pkg in version_changed:
                if any(i.package_name == pkg and i.issue_type == "typosquat" for i in issues):
                    continue
                issues.append(DependencyIssue(
                    package_name=pkg,
                    issue_type="version_change",
                    description=(
                        f"`{pkg}` version was changed. Confirm the new version is intentional "
                        "and review the changelog for breaking changes."
                    ),
                    severity="low",
                ))
        except Exception:
            pass  # skip this file; continue with others
    return issues
