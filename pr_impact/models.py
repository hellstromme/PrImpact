from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".cs": "csharp",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
}


def resolve_language(path: str) -> str:
    """Return the language string for a file path, or 'unknown'."""
    return _LANGUAGE_MAP.get(Path(path).suffix, "unknown")


@dataclass
class ChangedSymbol:
    name: str
    kind: Literal["file", "function", "class", "import"]
    change_type: str  # See classifier.py for full type list
    signature_before: str | None
    signature_after: str | None


@dataclass
class ChangedFile:
    path: str
    language: str  # 'python' | 'typescript' | 'javascript' | 'unknown'
    diff: str  # Raw unified diff
    content_before: str
    content_after: str
    changed_symbols: list[ChangedSymbol] = field(default_factory=list)


@dataclass
class BlastRadiusEntry:
    path: str
    distance: int  # 1 = directly imports a changed file
    imported_symbols: list[str]  # Which specific symbols it uses from the changed file
    churn_score: float | None  # Commits touching this file in last 90 days


@dataclass
class InterfaceChange:
    file: str
    symbol: str
    before: str
    after: str
    callers: list[str]  # Files that import this symbol


@dataclass
class Decision:
    description: str  # What approach was chosen
    rationale: str  # Why, as inferred from the code
    risk: str  # What breaks if the rationale is wrong


@dataclass
class Assumption:
    description: str  # What must be true for this design to be correct
    location: str  # File and function where the assumption is baked in
    risk: str  # Consequence if the assumption is violated


@dataclass
class Anomaly:
    description: str
    location: str
    severity: str  # 'low' | 'medium' | 'high'


@dataclass
class TestGap:
    behaviour: str  # The untested behaviour, in plain English
    location: str  # File and function


@dataclass
class AIAnalysis:
    summary: str = ""
    decisions: list[Decision] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    test_gaps: list[TestGap] = field(default_factory=list)


@dataclass
class RefsResult:
    """Resolved commit references and associated PR metadata for the pipeline."""
    base: str
    head: str
    pr_title: str | None = None
    fetch_pr_number: int | None = None   # set when a real GitHub PR was resolved
    fetch_base_ref: str | None = None    # branch name of the PR base
    fetch_remote: str = "origin"         # remote to fetch from if commits are missing


@dataclass
class ImpactReport:
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFile]
    blast_radius: list[BlastRadiusEntry]
    interface_changes: list[InterfaceChange]
    ai_analysis: AIAnalysis
