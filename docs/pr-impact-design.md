# PR Impact Tool — Design Document

## Overview

A CLI tool that takes a git repository and a PR (or two commit SHAs) and produces a
structured impact report. The report tells a human what the change does to the system,
what is downstream of it, what design decisions were made, and where the risk is — in
under ten minutes of reading.

**Constraints:**
- Works on any repo with no prior instrumentation
- No runtime data required — static analysis only
- Languages supported in MVP: Python, TypeScript, JavaScript, C#
- Output: Markdown report + JSON sidecar

---

## Repository Structure

```
pr_impact/
  cli.py               # Entry point, argument parsing
  git_analysis.py      # Git interaction — extract diff, file contents, history
  dependency_graph.py  # Import graph builder across the whole codebase
  classifier.py        # Classify each changed symbol by impact type
  ai_layer.py          # Claude API calls — decisions, assumptions, anomalies
  prompts.py           # All prompt templates, kept separate from logic
  reporter.py          # Assemble and render the final report
  models.py            # Dataclasses / typed dicts used across the tool
```

---

## Data Models (`models.py`)

```python
@dataclass
class ChangedFile:
    path: str
    language: str                      # 'python' | 'typescript' | 'javascript' | 'csharp' | 'java' | 'go' | 'ruby' | 'unknown'
    diff: str                          # Raw unified diff for this file
    content_before: str
    content_after: str
    changed_symbols: list[ChangedSymbol]

@dataclass
class ChangedSymbol:
    name: str
    kind: str                          # 'function' | 'class' | 'method' | 'variable' | 'export'
    change_type: str                   # See classifier section
    signature_before: str | None
    signature_after: str | None

@dataclass
class BlastRadiusEntry:
    path: str
    distance: int                      # 1 = directly imports changed file, 2 = imports that, etc.
    imported_symbols: list[str]        # Which specific symbols it uses from the changed file
    churn_score: float | None          # Commits touching this file in last 90 days, if available

@dataclass
class RefsResult:
    base: str                           # Base commit SHA
    head: str                           # Head commit SHA
    pr_title: str | None = None         # PR title if resolved from GitHub; None otherwise
    fetch_pr_number: int | None = None  # Set when a real GitHub PR was resolved
    fetch_base_ref: str | None = None   # Branch name of the PR base (for fetching)
    fetch_remote: str = "origin"        # Remote to fetch from if commits are missing

@dataclass
class ImpactReport:
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFile]
    blast_radius: list[BlastRadiusEntry]
    interface_changes: list[InterfaceChange]
    ai_analysis: AIAnalysis

@dataclass
class InterfaceChange:
    file: str
    symbol: str
    before: str
    after: str
    callers: list[str]                 # Files that import this symbol

@dataclass
class AIAnalysis:
    summary: str                       # Plain English: what does the system do differently now
    decisions: list[Decision]
    assumptions: list[Assumption]
    anomalies: list[Anomaly]
    test_gaps: list[TestGap]

@dataclass
class Decision:
    description: str                   # What was chosen
    rationale: str                     # Why (as inferred from code)
    risk: str                          # What breaks if the rationale is wrong

@dataclass
class Assumption:
    description: str                   # What must be true for this design to be correct
    location: str                      # File/function where it's baked in
    risk: str                          # What happens if the assumption is violated

@dataclass
class Anomaly:
    description: str
    location: str
    severity: str                      # 'low' | 'medium' | 'high'

@dataclass
class TestGap:
    behaviour: str                     # The untested behaviour, in plain English
    location: str                      # Where in the code
```

---

## Module Responsibilities

### `git_analysis.py`

Responsible for all git interaction. Uses `gitpython`.

**Functions:**

```python
def get_changed_files(repo_path: str, base_sha: str, head_sha: str) -> list[ChangedFile]
```
- Get the diff between base and head
- For each changed file: extract the raw diff, and the full file content at both commits
- Filter to supported languages only (Python, TypeScript, JavaScript, C#)
- Resolve language from file extension (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.cs`)

```python
def get_git_churn(repo_path: str, path: str, days: int = 90) -> float
```
- Count commits touching this file in the last N days
- Returns 0.0 if git history is not available
- Used to annotate blast radius entries with historical risk signal

```python
def get_pr_metadata(repo_path: str, base_sha: str, head_sha: str) -> dict
```
- Commit messages between base and head
- Author info
- Returns a dict — best-effort, failures should not break the pipeline

---

### `dependency_graph.py`

Builds an import graph for the whole codebase using **regex-based import extraction**
(not AST parsing — sufficient for MVP, avoids tree-sitter setup complexity).

**Import patterns to extract:**

Python:
- `import X`
- `from X import Y`
- `from .X import Y` (relative)

TypeScript / JavaScript:
- `import X from 'Y'`
- `import { X } from 'Y'`
- `const X = require('Y')`
- `export { X } from 'Y'` (re-exports — important to track)

C#:
- `using Namespace;` (resolved via a pre-built namespace→files map scanned from `namespace` declarations)

Java:
- `import fully.qualified.Class;`
- `import fully.qualified.*;` (wildcard imports)
- Source roots resolved via Maven/Gradle conventions

Go:
- Standard `import` blocks; module-path resolved via `go.mod`; `vendor/` excluded

Ruby:
- `require 'name'` and `require_relative 'path'`; falls back to `lib/` convention

**Functions:**

```python
def build_import_graph(repo_path: str, language_filter: list[str]) -> dict[str, list[str]]
```
- Walk the repo (respect .gitignore)
- For every file, extract its imports
- Resolve relative imports to absolute repo paths
- Returns: `{ file_path: [list of files it imports] }`
- Invert this to get `{ file_path: [list of files that import it] }` — the reverse graph

```python
def get_blast_radius(
    reverse_graph: dict[str, list[str]],
    changed_files: list[str],
    max_depth: int = 3
) -> list[BlastRadiusEntry]
```
- BFS from each changed file through the reverse graph
- Collect all files reachable within max_depth
- Tag each with distance
- Deduplicate (a file reachable via multiple paths gets the shortest distance)
- max_depth of 3 is sufficient for MVP — beyond that, "everything depends on everything"
  becomes noise

```python
def get_imported_symbols(file_path: str, imported_from: str) -> list[str]
```
- For a given file, extract which named symbols it imports from a specific source
- Used to populate BlastRadiusEntry.imported_symbols

---

### `classifier.py`

Classifies each change by impact type. Uses regex against the diff and file content.
No AST required for MVP — diff context lines are sufficient.

**Change types:**

| Type | Definition |
|---|---|
| `internal` | Implementation changed, public signature unchanged, no import changes |
| `interface_changed` | A public/exported function or class signature changed |
| `interface_added` | New export added |
| `interface_removed` | Existing export removed |
| `dependency_added` | File now imports something it didn't |
| `dependency_removed` | File no longer imports something it did |
| `new_file` | File didn't exist at base |
| `deleted_file` | File existed at base, gone at head |

**Functions:**

```python
def classify_changed_file(file: ChangedFile) -> list[ChangedSymbol]
```
- Extract function/class definitions that appear in the diff
- Compare their signatures between before and after
- Detect export keyword presence/absence to determine if public
- Return a ChangedSymbol for each, with appropriate change_type

```python
def get_interface_changes(
    changed_files: list[ChangedFile],
    reverse_graph: dict[str, list[str]]
) -> list[InterfaceChange]
```
- For each symbol with change_type in {interface_changed, interface_removed}
- Look up which files import it via the reverse graph
- Populate InterfaceChange.callers

---

### `prompts.py`

All prompt templates live here. No logic — pure strings with format placeholders.
This separation matters: prompts will need iteration independently of code logic.

**Three prompts required for MVP:**

**Prompt 1: Summary + Decisions + Assumptions**

Decisions and assumptions are conceptually distinct:

- A **decision** is something the author *chose* — an approach that could have been done differently, where the rationale is visible in the code. The risk field captures what breaks if that rationale turns out to be wrong.
- An **assumption** is something the author *took for granted* — a precondition about the world that the code depends on but does not verify or enforce. The risk field captures the consequence if that precondition is violated at runtime.

Decisions invite debate about approach. Assumptions invite questions about whether the precondition is actually guaranteed elsewhere in the system.

```
You are analysing a code change. Your job is to extract design information that a
human reviewer needs in order to evaluate whether the change fits their system and roadmap.

You will be given:
- The diff for each changed file
- The public interface of files in the blast radius (signatures only, not implementations)

Respond in JSON matching this schema exactly:
{
  "summary": "string — what the system now does differently, in plain English, 2-3 sentences",
  "decisions": [
    {
      "description": "what approach was chosen",
      "rationale": "why, as inferred from the code",
      "risk": "what breaks if the rationale is wrong"
    }
  ],
  "assumptions": [
    {
      "description": "what must be true for this design to be correct",
      "location": "file and function where this is baked in",
      "risk": "consequence if assumption is violated"
    }
  ]
}

Changed files (full diff):
{changed_files_diff}

Blast radius interfaces (signatures only):
{blast_radius_signatures}
```

**Prompt 2: Anomaly Detection**

```
You are reviewing a code change for structural anomalies — patterns that deviate from
the conventions visible in the surrounding codebase.

You will be given:
- The diff for each changed file
- Examples of the established patterns in nearby files (signatures and import structure)

Identify deviations that a reviewer should be aware of. Do not flag style differences.
Flag things that suggest the change may not fit the architecture — unusual coupling,
bypassed abstractions, patterns used in a context where they're not normally used.

Respond in JSON:
{
  "anomalies": [
    {
      "description": "what is unusual",
      "location": "file and approximate line",
      "severity": "low | medium | high"
    }
  ]
}

Changed files:
{changed_files_diff}

Established patterns in neighbouring files:
{neighbouring_signatures}
```

**Prompt 3: Test Gap Analysis**

```
You are analysing a code change to identify behaviours that are not covered by tests.

You will be given:
- The diff for each changed file
- The test files that exist in the repo for the changed modules (if any)

Identify changed or new behaviours that have no corresponding test. Focus on:
- New code paths (especially error paths and edge cases)
- Changed logic in existing functions
- New exported symbols with no test file

Do not flag missing tests for trivial getters/setters or purely structural changes.

Respond in JSON:
{
  "test_gaps": [
    {
      "behaviour": "description of the untested behaviour in plain English",
      "location": "file and function"
    }
  ]
}

Changed files:
{changed_files_diff}

Existing test files for these modules:
{test_files}
```

---

### `ai_layer.py`

Calls the Claude API using the prompts above. Handles context budget management.

**Functions:**

```python
def run_ai_analysis(
    changed_files: list[ChangedFile],
    blast_radius: list[BlastRadiusEntry],
    repo_path: str
) -> AIAnalysis
```
- Assemble context for each prompt (see context budget below)
- Make three API calls (summary/decisions/assumptions, anomalies, test gaps)
- Parse JSON responses
- Return populated AIAnalysis

**Context budget strategy:**

The context window is limited. Priority order for what to include:

1. Full diffs of changed files — always include, truncate at 8000 tokens if needed
2. Signatures (not implementations) of blast radius files at distance 1 — always include
3. Signatures of blast radius files at distance 2 — include if budget allows
4. Test files for changed modules — for prompt 3 only
5. Neighbouring file signatures for anomaly detection — sample up to 5 files from the
   same directory as each changed file

**Signature extraction:**
A "signature" is: imports, function/class/method definitions with their decorators,
but not the body. This can be extracted with regex for MVP. Gives the model
enough to reason about design fit without burning tokens on implementation.

**Error handling:**
- API calls should be retried once on failure
- If JSON parsing fails, return empty lists for that analysis section rather than
  crashing — a partial report is better than no report
- Log raw API responses to a temp file for debugging

---

### `reporter.py`

Assembles the final output from the populated ImpactReport model.

**Functions:**

```python
def render_markdown(report: ImpactReport) -> str
```

Output structure:

```markdown
# PR Impact Report
{pr_title} · {base_sha[0:7]}..{head_sha[0:7]}

## Summary
{ai_analysis.summary}

## Blast Radius
Direct changes: N files
Downstream risk: N files across N dependency hops

| File | Distance | Uses | Churn |
|------|----------|------|-------|
...

## Interface Changes
For each InterfaceChange:
  ### `Symbol` in `file`
  **Before:** signature
  **After:** signature
  **Callers:** list of files

## Decisions & Assumptions
For each Decision and Assumption from ai_analysis

## Anomalies
For each Anomaly — severity as emoji (🟡 medium, 🔴 high)

## Test Gaps
For each TestGap
```

```python
def render_json(report: ImpactReport) -> str
```
- JSON serialisation of the full ImpactReport dataclass
- Used as the machine-readable sidecar

```python
def render_sarif(report: ImpactReport) -> str
```
- SARIF 2.1.0 serialisation of anomalies and test gaps
- Enables import into GitHub Advanced Security, SonarQube, and similar tools

---

### `cli.py`

Entry point. Uses `click`.

```
pr-impact analyse \
  --repo /path/to/repo \
  [--pr 247 | --base abc1234 --head def5678] \
  [--output report.md] \
  [--json report.json] \
  [--sarif report.sarif] \
  [--max-depth 3] \
  [--fail-on-severity high]
```

- Orchestrates the pipeline: git → graph → classify → ai → report
- Prints the markdown report to stdout by default
- Writes files if --output / --json flags provided
- Progress output to stderr so stdout stays clean for piping

---

## Pipeline Orchestration (in `cli.py`)

```
1. git_analysis.get_changed_files()
2. dependency_graph.build_import_graph()
3. dependency_graph.get_blast_radius()
4. classifier.classify_changed_file() for each file
5. classifier.get_interface_changes()
6. git_analysis.get_git_churn() for each blast radius entry
7. ai_layer.run_ai_analysis()
8. reporter.render_markdown() + reporter.render_json()
```

Steps 1–6 are deterministic and fast. Step 7 is the only network call.
Steps 1–6 should complete in under 5 seconds on a typical repo.

---

## Environment & Dependencies

```
gitpython      # git interaction
anthropic      # Claude API
click          # CLI
rich           # Progress display and formatted output
```

**API key:** Read from `ANTHROPIC_API_KEY` environment variable. Fail fast with a
clear error message if not set.

**Model:** `claude-sonnet-4-5` — good balance of reasoning quality and cost for
this use case.

---

## What This Is Not

- Tree-sitter AST parsing (deferred to v0.4 — would improve classifier accuracy)
- Runtime / dynamic call graph analysis
- Performance impact modelling
- Roadmap / backlog integration (deferred to v2.0)
- Web UI or dashboard (deferred to v1.0)
- Malicious code detection (deferred to v0.3)
