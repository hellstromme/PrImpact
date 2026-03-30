from dataclasses import dataclass, field


@dataclass
class ChangedSymbol:
    name: str
    kind: str  # 'function' | 'class' | 'method' | 'variable' | 'export'
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
class ImpactReport:
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFile]
    blast_radius: list[BlastRadiusEntry]
    interface_changes: list[InterfaceChange]
    ai_analysis: AIAnalysis
