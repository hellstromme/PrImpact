from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["high", "medium", "low"]

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
    # v0.4: richer fields populated by AST extraction when available
    params: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    return_type: str | None = None


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
    severity: Severity


@dataclass
class TestGap:
    behaviour: str  # The untested behaviour, in plain English
    location: str  # File and function
    severity: Severity = "medium"
    gap_type: str = ""  # "security" | "functional" | "branch" | "other"


@dataclass
class SourceLocation:
    file: str
    line: int | None = None
    symbol: str | None = None


@dataclass
class SecuritySignal:
    description: str
    location: SourceLocation
    signal_type: str  # "network_call" | "credential" | "encoded_payload" | "dynamic_exec" | "shell_invoke" | "suspicious_import"
    severity: Severity
    why_unusual: str
    suggested_action: str


@dataclass
class DependencyIssue:
    package_name: str
    issue_type: str   # "typosquat" | "version_change" | "vulnerability"
    description: str
    severity: Severity
    license: str | None = None


@dataclass
class SemanticVerdict:
    """AI assessment of whether a changed symbol is semantically equivalent or risky."""

    file: str
    symbol: str
    verdict: str  # "equivalent" | "risky" | "normal"
    reason: str


@dataclass
class AIAnalysis:
    summary: str = ""
    decisions: list[Decision] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    test_gaps: list[TestGap] = field(default_factory=list)
    # security_signals lives here (not on ImpactReport) because it is the output
    # of the 4th Claude API call — AI-scored and context-adjusted findings.
    # dependency_issues lives on ImpactReport because it is deterministic output
    # from security.py, produced before any AI call.
    security_signals: list[SecuritySignal] = field(default_factory=list)
    # v0.4: semantic equivalence verdicts (5th AI call, optional)
    semantic_verdicts: list[SemanticVerdict] = field(default_factory=list)


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
class VerdictBlocker:
    category: str   # "test_gap" | "security_signal" | "dependency_issue" | "anomaly"
    description: str
    location: str


@dataclass
class Verdict:
    status: str              # "clean" | "has_blockers"
    agent_should_continue: bool
    rationale: str
    blockers: list[VerdictBlocker] = field(default_factory=list)


@dataclass
class GraphNode:
    id: str               # file path (unique key)
    path: str
    type: str             # "changed" | "affected"
    distance: int         # 0 for changed files
    language: str | None
    churn_score: float | None = None


@dataclass
class GraphEdge:
    source: str           # origin-side file (impact flows out from here)
    target: str           # dependent file (imports source, farther from origin)
    symbols: list[str] = field(default_factory=list)


@dataclass
class BlastGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass
class HistoricalHotspot:
    """A file that frequently appears in blast radii across past analyses."""

    file: str
    appearances: int


@dataclass
class RunSummary:
    """Lightweight summary of a persisted analysis run, used by the web UI run list."""

    id: str                  # UUID
    repo_path: str
    pr_number: int | None
    pr_title: str | None
    base_sha: str
    head_sha: str
    created_at: str          # ISO 8601
    verdict: str | None      # "clean" | "has_blockers" | None
    blast_radius_count: int
    anomaly_count: int
    signal_count: int
    merged: bool = False     # True when head_sha is an ancestor of the main branch


@dataclass
class ImpactReport:
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFile]
    blast_radius: list[BlastRadiusEntry]
    interface_changes: list[InterfaceChange]
    ai_analysis: AIAnalysis
    dependency_issues: list[DependencyIssue] = field(default_factory=list)
    # v0.4: historical hotspots from prior runs (populated when --history-db is active)
    historical_hotspots: list[HistoricalHotspot] = field(default_factory=list)
    verdict: Verdict | None = None
    blast_graph: BlastGraph | None = None


@dataclass
class SuppressedSignal:
    """A signal type suppressed in a specific path prefix."""
    signal_type: str    # matches SecuritySignal.signal_type
    path_prefix: str    # e.g. "tools/" — suppressed if file starts with this
    reason: str = ""


@dataclass
class PrImpactConfig:
    """Loaded from .primpact.yml in the repo root. All fields are optional."""
    high_sensitivity_modules: list[str] = field(default_factory=list)
    suppressed_signals: list[SuppressedSignal] = field(default_factory=list)
    blast_radius_depth: dict[str, int] = field(default_factory=dict)  # path_prefix → depth
    fail_on_severity: str | None = None   # overrides CLI flag when not "none"
    anomaly_thresholds: dict[str, str] = field(default_factory=dict)  # signal_type → severity
