# Primpact — Architecture Document

**Version:** 0.4  
**Status:** Current  
**Last updated:** 2026-04-11

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [System Overview](#2-system-overview)
3. [Repository Structure](#3-repository-structure)
4. [Data Models](#4-data-models)
5. [Module Specifications](#5-module-specifications)
   - 5.1 [cli.py — Entry Point](#51-clipy--entry-point)
   - 5.2 [git_analysis.py — Git Interaction](#52-git_analysispy--git-interaction)
   - 5.3 [dependency_graph.py — Import Graph](#53-dependency_graphpy--import-graph)
   - 5.4 [classifier.py — Change Classification](#54-classifierpy--change-classification)
   - 5.5 [ai_layer.py — Claude API Integration](#55-ai_layerpy--claude-api-integration)
   - 5.6 [prompts.py — Prompt Templates](#56-promptspy--prompt-templates)
   - 5.7 [reporter.py — Output Rendering](#57-reporterpy--output-rendering)
   - 5.8 [github.py — GitHub API Helpers](#58-githubpy--github-api-helpers)
   - 5.9 [models.py — Shared Data Types](#59-modelspy--shared-data-types)
   - 5.10 [security.py — Security Signal Detection](#510-securitypy--security-signal-detection)
   - 5.11 [ast_extractor.py — AST Wrappers](#511-ast_extractorpy--ast-wrappers)
   - 5.12 [history.py — Historical Pattern Learning](#512-historypy--historical-pattern-learning)
6. [Pipeline Orchestration](#6-pipeline-orchestration)
7. [External Interfaces](#7-external-interfaces)
8. [Context Budget Strategy](#8-context-budget-strategy)
9. [Error Handling Strategy](#9-error-handling-strategy)
10. [Output Format](#10-output-format)
11. [Dependencies](#11-dependencies)
12. [Configuration and Environment](#12-configuration-and-environment)
13. [Design Decisions and Rationale](#13-design-decisions-and-rationale)
14. [Known Limitations and Deferred Work](#14-known-limitations-and-deferred-work)

---

## 1. Purpose and Scope

Primpact is a CLI tool that takes a git repository and a pair of commit SHAs (or a pull request) and produces a structured impact report. The report tells a human reviewer what a code change does to the system, what is downstream of it, what design decisions were made, and where the risk is — in under ten minutes of reading.

### Goals

- **No instrumentation tax.** Works on any repository with no prior setup.
- **Static analysis only.** No runtime data required.
- **Partial is better than nothing.** Every pipeline stage degrades gracefully; a report with gaps is more useful than a crash.
- **Output is the product.** The Markdown report and JSON sidecar are first-class deliverables.

### Scope of v0.1

- Languages supported: Python, TypeScript, JavaScript, C#
- Input: local git repository + two commit SHAs
- Output: Markdown report + JSON sidecar
- AI analysis via Anthropic Claude API (three prompt calls per run)

---

## 2. System Overview

Primpact operates as a linear eight-step pipeline. Steps 1–6 are deterministic, CPU-bound, and complete in under five seconds on a typical repository. Step 7 is the only network call (the Claude API). Step 8 renders the final output.

```
Git repository
      │
      ▼
┌─────────────────────────────────────────────────┐
│                    cli.py                       │
│            (pipeline orchestrator)              │
└────┬──────────┬──────────┬──────────┬───────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
git_analysis  dep_graph  classifier  ai_layer ◄── prompts.py
     │          │          │          │
     └──────────┴──────────┴──────────┘
                           │
                      models.py
                  (shared data types)
                           │
                           ▼
                       reporter.py
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
         report.md                report.json
```

---

## 3. Repository Structure

```
pr_impact/
  cli.py               # Entry point, argument parsing, pipeline orchestration
  git_analysis.py      # Git interaction — diffs, file contents, commit history
  dependency_graph.py  # AST-first import graph builder and blast radius BFS
  classifier.py        # Changed symbol classification by impact type (AST-first, regex fallback)
  ai_layer.py          # Claude API calls — summary, decisions, anomalies, test gaps, security scoring, semantic equivalence
  prompts.py           # All prompt templates (separated from logic)
  reporter.py          # Assembles and renders the final Markdown, JSON, and SARIF reports
  github.py            # GitHub API helpers — PR resolution, remote detection, PR listing
  ast_extractor.py     # tree-sitter AST wrappers — extract_imports() and extract_symbols()
  history.py           # SQLite history: save_run(), load_hotspots(), load_anomaly_patterns()
  models.py            # Shared dataclasses used across all modules
  security.py          # Deterministic security signal detection and dependency integrity checks
```

---

## 4. Data Models

All models are defined in `models.py` as Python dataclasses. Every module imports from this file; no module imports another module's internal types.

### `ChangedFile`

Represents a single file that was modified between `base_sha` and `head_sha`.

```python
@dataclass
class ChangedFile:
    path: str
    language: str           # 'python' | 'typescript' | 'javascript' | 'csharp' | 'java' | 'go' | 'ruby' | 'unknown'
    diff: str               # Raw unified diff
    content_before: str     # Full file content at base_sha
    content_after: str      # Full file content at head_sha
    changed_symbols: list[ChangedSymbol]
```

### `ChangedSymbol`

A function, class, method, variable, or export that appears in the diff.

```python
@dataclass
class ChangedSymbol:
    name: str
    kind: str               # 'function' | 'class' | 'method' | 'variable' | 'export'
    change_type: str        # See classifier section for full type list
    signature_before: str | None
    signature_after: str | None
    params: list[str]           # Parameter names extracted from signature (v0.4)
    decorators: list[str]       # Decorator names (v0.4)
    return_type: str | None     # Return type annotation if present (v0.4)
```

### `BlastRadiusEntry`

A file that is downstream of the changed files, reachable via the import graph.

```python
@dataclass
class BlastRadiusEntry:
    path: str
    distance: int           # 1 = directly imports a changed file, 2 = one hop further, etc.
    imported_symbols: list[str]   # Which specific symbols it uses from the changed file
    churn_score: float | None     # Commits touching this file in last 90 days
```

### `InterfaceChange`

A public or exported symbol whose signature changed, along with its callers.

```python
@dataclass
class InterfaceChange:
    file: str
    symbol: str
    before: str
    after: str
    callers: list[str]      # Files that import this symbol
```

### `SecuritySignal`

A security pattern detected in the diff, AI-scored for contextual risk.

```python
@dataclass
class SecuritySignal:
    description: str
    file_path: str
    line_number: int | None
    signal_type: str   # "network_call" | "credential" | "encoded_payload" | "dynamic_exec" | "shell_invoke" | "suspicious_import"
    severity: str      # "high" | "medium" | "low"
    why_unusual: str
    suggested_action: str
```

### `DependencyIssue`

An issue detected in a package manifest change (typosquat, version pin change, or known CVE).

```python
@dataclass
class DependencyIssue:
    package_name: str
    issue_type: str   # "typosquat" | "version_change" | "vulnerability"
    description: str
    severity: str     # "high" | "medium" | "low"
```

### `VerdictBlocker`

A specific, agent-fixable defect identified by the verdict prompt.

```python
@dataclass
class VerdictBlocker:
    category: str   # "test_gap" | "security_signal" | "dependency_issue" | "anomaly"
    description: str
    location: str
```

### `Verdict`

The output of the optional `--verdict` analysis call.

```python
@dataclass
class Verdict:
    status: str              # "clean" | "has_blockers"
    agent_should_continue: bool
    rationale: str
    blockers: list[VerdictBlocker]
```

### `SemanticVerdict`

The result of the semantic equivalence check for a single changed symbol (v0.4).

```python
@dataclass
class SemanticVerdict:
    file: str
    symbol: str
    verdict: str    # "equivalent" | "risky" | "normal"
    reason: str     # Plain-English explanation from the model
```

### `HistoricalHotspot`

A file that has appeared repeatedly in blast radii across past runs (v0.4).

```python
@dataclass
class HistoricalHotspot:
    file: str
    appearances: int    # Number of past runs in which this file appeared in the blast radius
```

### `AIAnalysis`

The structured output from the Claude API layer.

```python
@dataclass
class AIAnalysis:
    summary: str                  # Plain English: what does the system do differently now
    decisions: list[Decision]
    assumptions: list[Assumption]
    anomalies: list[Anomaly]
    test_gaps: list[TestGap]
    security_signals: list[SecuritySignal]  # AI-scored; populated by call 4 when signals detected
    semantic_verdicts: list[SemanticVerdict]  # Populated by call 5 when substantial diffs present (v0.4)
```

### `Decision`

An inferred design decision extracted from the code change.

```python
@dataclass
class Decision:
    description: str        # What approach was chosen
    rationale: str          # Why, as inferred from the code
    risk: str               # What breaks if the rationale is wrong
```

### `Assumption`

A precondition that must be true for the design to be correct.

```python
@dataclass
class Assumption:
    description: str        # What must be true for this design to be correct
    location: str           # File and function where the assumption is baked in
    risk: str               # Consequence if the assumption is violated
```

### `Anomaly`

A structural deviation from the patterns visible in the surrounding codebase.

```python
@dataclass
class Anomaly:
    description: str
    location: str
    severity: str           # 'low' | 'medium' | 'high'
```

### `TestGap`

A changed or new behaviour with no corresponding test coverage.

```python
@dataclass
class TestGap:
    behaviour: str          # The untested behaviour, in plain English
    location: str           # File and function
```

### `RefsResult`

Resolved commit references plus optional PR metadata, returned by the GitHub PR resolution
logic before pipeline execution.

```python
@dataclass
class RefsResult:
    base: str                           # Base commit SHA
    head: str                           # Head commit SHA
    pr_title: str | None = None         # PR title if resolved from GitHub; None otherwise
    fetch_pr_number: int | None = None  # Set when a real GitHub PR was resolved
    fetch_base_ref: str | None = None   # Branch name of the PR base (for fetching)
    fetch_remote: str = "origin"        # Remote to fetch from if commits are missing
```

### `ImpactReport`

The top-level model that aggregates all analysis results.

```python
@dataclass
class ImpactReport:
    pr_title: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFile]
    blast_radius: list[BlastRadiusEntry]
    interface_changes: list[InterfaceChange]
    ai_analysis: AIAnalysis
    dependency_issues: list[DependencyIssue]  # Deterministic; populated before any AI call
    historical_hotspots: list[HistoricalHotspot]  # Files frequently in blast radius across past runs (v0.4)
```

---

## 5. Module Specifications

### 5.1 `cli.py` — Entry Point

Uses `click` for argument parsing. Owns the pipeline orchestration loop. Prints progress to `stderr` so `stdout` stays clean for piping.

#### Command signature

```
pr-impact analyse \
  --repo /path/to/repo \
  [--pr 247 | --base abc1234 --head def5678] \
  [--output report.md] \
  [--json report.json] \
  [--sarif report.sarif] \
  [--max-depth 3] \
  [--fail-on-severity high] \
  [--check-osv] \
  [--verdict] [--verdict-json verdict.json] \
  [--history-db /path/to/history.db] [--no-history]
```

#### Responsibilities

- Parse and validate CLI arguments
- Load config from `~/.pr_impact/config.toml` if present (sets `ANTHROPIC_API_KEY` and/or `GITHUB_TOKEN` if not already in env)
- Resolve commit refs: if `--pr` is given, call `github.fetch_pr()` to get base/head SHAs; if in an interactive terminal with no explicit refs, list open PRs for the user to pick; otherwise fall back to `HEAD~1..HEAD`
- Call each pipeline step in order (see section 6)
- Write Markdown to `stdout` by default; write to `--output` file if specified
- Write JSON sidecar to `--json` file if specified
- Write SARIF 2.1.0 report to `--sarif` file if specified
- Display progress output via `rich` to `stderr`
- Fail fast with a clear error message if `ANTHROPIC_API_KEY` is not set
- Exit with code 1 if `--fail-on-severity` threshold is met by any anomaly
- Load historical hotspots and anomaly patterns from the history database before the pipeline (unless `--no-history`)
- Save the completed `ImpactReport` to the history database after step 8 (unless `--no-history`)

---

### 5.2 `git_analysis.py` — Git Interaction

Uses `gitpython`. Responsible for all interaction with the local git repository. No other module calls git directly.

#### `get_changed_files(repo_path, base_sha, head_sha) -> list[ChangedFile]`

- Compute the diff between `base_sha` and `head_sha`
- For each changed file: extract the unified diff, and the full file content at both commits
- Filter to supported languages only (Python, TypeScript, JavaScript, C#)
- Resolve language from file extension (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.cs`)
- Return a list of populated `ChangedFile` objects with `changed_symbols` initially empty (populated later by `classifier.py`)

#### `get_git_churn(repo_path, path, days=90) -> float`

- Count the number of commits touching the given file path in the last `days` days
- Returns `0.0` if git history is unavailable or the path does not exist in history
- Used to annotate `BlastRadiusEntry.churn_score` after blast radius calculation

#### `get_pr_metadata(repo_path, base_sha, head_sha) -> dict`

- Retrieve commit messages between `base_sha` and `head_sha`
- Retrieve author information from the commits
- Returns a best-effort dict; failures in this function must not break the main pipeline

#### `ensure_commits_present(repo_path, base_sha, head_sha, remote_name, pr_number=None, base_ref=None, repo=None)`

- Checks whether both SHAs are present in the local git history
- If the head SHA is missing and `pr_number` is set, fetches `refs/pull/{pr_number}/head` from `remote_name`
- If the base SHA is missing and `base_ref` is set, fetches that branch ref from `remote_name`
- Raises `RuntimeError` if the fetch fails or SHAs remain absent after fetching; the caller (`cli.py`) catches this and logs it as a warning

---

### 5.3 `dependency_graph.py` — Import Graph

Builds an import graph for the whole codebase. Uses `ast_extractor.py` for AST-based import extraction where available, falling back to regex for unsupported languages or parse failures.

#### Import patterns extracted

**Python:**
- `import X`
- `from X import Y`
- `from .X import Y` (relative imports, resolved to absolute repo paths)

**TypeScript and JavaScript:**
- `import X from 'Y'`
- `import { X } from 'Y'`
- `const X = require('Y')`
- `export { X } from 'Y'` (re-exports — tracked as a dependency edge)

**C#:**
- `using Namespace;` (resolved via a pre-built namespace→files map; a namespace may span multiple files)

**Java:**
- `import fully.qualified.Class;`
- `import fully.qualified.*;` (wildcard imports)
- Source roots resolved via Maven (`src/main/java`) and Gradle conventions

**Go:**
- Standard `import` blocks (single and grouped)
- Module-path resolution via `go.mod` to map import paths to local file paths
- `vendor/` directory excluded from graph

**Ruby:**
- `require 'name'` and `require_relative 'path'`
- Falls back to `lib/` directory convention for gem-style layouts

#### `build_import_graph(repo_path, language_filter) -> dict[str, list[str]]`

- Walk the repository, respecting `.gitignore`
- For every file, extract its imports using the patterns above
- Resolve relative imports to absolute repo-relative paths
- Return the forward graph: `{ file_path: [list of files it imports] }`
- The inverse (reverse) graph `{ file_path: [files that import it] }` is derived by callers

#### `get_blast_radius(reverse_graph, changed_files, max_depth=3) -> list[BlastRadiusEntry]`

- Run BFS from each changed file through the reverse import graph
- Collect all files reachable within `max_depth` hops
- Tag each result with its shortest-path `distance` from any changed file
- Deduplicate: if a file is reachable via multiple paths, keep the shortest distance
- The default `max_depth` of 3 is sufficient for MVP; beyond 3, the signal-to-noise ratio degrades

#### `get_imported_symbols(file_path, imported_from) -> list[str]`

- For a given file, extract the specific named symbols it imports from a particular source module
- Used to populate `BlastRadiusEntry.imported_symbols`

---

### 5.4 `classifier.py` — Change Classification

Classifies each changed file and symbol by impact type. Uses `ast_extractor.py` for symbol extraction where available, falling back to regex against diff content and file content.

#### Change types

| Type | Definition |
|---|---|
| `internal` | Implementation changed, public signature unchanged, no import changes |
| `interface_changed` | A public or exported function or class signature changed |
| `interface_added` | New export added |
| `interface_removed` | Existing export removed |
| `dependency_added` | File now imports something it did not before |
| `dependency_removed` | File no longer imports something it previously did |
| `new_file` | File did not exist at `base_sha` |
| `deleted_file` | File existed at `base_sha` and is absent at `head_sha` |

#### `classify_changed_file(file: ChangedFile) -> list[ChangedSymbol]`

- Extract function and class definitions that appear in the diff
- Compare signatures between `content_before` and `content_after`
- Detect the presence or absence of export keywords to determine visibility
- Return a `ChangedSymbol` for each, with the appropriate `change_type`
- Mutates `file.changed_symbols` in place and also returns the list

#### `get_interface_changes(changed_files, reverse_graph) -> list[InterfaceChange]`

- Iterate over all symbols with `change_type` in `{interface_changed, interface_removed}`
- Look up which files import the containing file via `reverse_graph`
- Populate `InterfaceChange.callers` with those file paths
- Return the list of `InterfaceChange` objects

---

### 5.5 `ai_layer.py` — Claude API Integration

Makes three to five Claude API calls per analysis run. Handles context budget management. This is the only module that performs network I/O.

#### `run_ai_analysis(changed_files, blast_radius, repo_path, pattern_signals=None, anomaly_history=None, hotspots=None) -> AIAnalysis`

Assembles context for each prompt, makes API calls in sequence, parses the JSON responses, and returns a populated `AIAnalysis` object.

| Call | Prompt | Output fields | When |
|---|---|---|---|
| 1 | Summary + Decisions + Assumptions | `AIAnalysis.summary`, `.decisions`, `.assumptions` | Always |
| 2 | Anomaly Detection | `AIAnalysis.anomalies` | Always |
| 3 | Test Gap Analysis | `AIAnalysis.test_gaps` | Always |
| 4 | Security Signal Scoring | `AIAnalysis.security_signals` | Only when `pattern_signals` is non-empty |
| 5 | Semantic Equivalence | `AIAnalysis.semantic_verdicts` | Only when substantial diffs present (v0.4) |

Call 4 takes the raw pattern signals from `security.py` and asks the model to assess each in the context of the file's stated purpose and existing patterns, adjusting severity and adding contextual explanation. If the AI call fails or returns unexpected JSON, `security_signals` falls back to the raw `pattern_signals`.

Call 5 receives before/after signatures for changed symbols and classifies each as `"equivalent"` (a refactor or reformat with no behavioural change), `"risky"` (a small-looking diff that alters branching logic or state), or `"normal"` (a genuine, non-trivial change). This lets the report direct reviewer attention to what actually matters. Historical anomaly patterns from `history.py` are included as additional context when available.

#### `run_verdict_analysis(ai_analysis, dependency_issues) -> Verdict`

A separate, optional call made after the main pipeline (only when `--verdict` or `--verdict-json` is given). Uses `PROMPT_VERDICT` to classify findings as BLOCKERS (agent-fixable defects) or OBSERVATIONS (design commentary for humans). Returns a `Verdict` with `agent_should_continue` set to `True` only when actionable blockers exist.

#### Context assembly

For each prompt call, context is assembled in this priority order:

1. **Full diffs of all changed files** — always included; truncated at 8,000 tokens if needed
2. **Signatures (not implementations) of blast radius files at distance 1** — always included
3. **Signatures of blast radius files at distance 2** — included if token budget permits
4. **Test files for changed modules** — for prompt 3 only
5. **Signatures of up to 5 neighbouring files** (same directory as each changed file) — for prompt 2 (anomaly detection); also includes signatures of changed files *before* the PR to establish baseline patterns
6. **Pattern signals + file context (before-signatures of affected files)** — for prompt 4 (security scoring) only

A "signature" is defined as: all import statements, all function and class definitions with their decorators, but not the function body. This is extracted with regex. It gives the model enough context to reason about design fit without burning tokens on implementation detail.

#### Retry and failure behaviour

- Each API call is retried once on failure
- If JSON parsing fails for a call, the corresponding fields in `AIAnalysis` are set to empty lists rather than raising an exception
- Raw API responses are logged to a temporary file for debugging
- A partial `AIAnalysis` (with some empty fields) is returned rather than propagating the error

#### Model

`claude-sonnet-4-5` — chosen for the balance of reasoning quality and cost for this use case. Configurable via environment variable in future versions.

---

### 5.6 `prompts.py` — Prompt Templates

Contains all prompt templates as string constants with format placeholders. Contains no logic. Separated from `ai_layer.py` so prompts can be iterated independently of the calling code.

#### Prompt 1: Summary, Decisions, and Assumptions

Receives: full diffs of changed files; public signatures of blast radius files.

Instructs the model to return JSON with three fields: `summary` (2–3 sentence plain-English description of what the system does differently), `decisions` (list of inferred design choices with rationale and risk), and `assumptions` (list of preconditions baked into the design with their consequences if violated).

**Conceptual distinction between decisions and assumptions:**

A **decision** is something the author *chose* — an approach that could have been done differently, where the rationale is visible in the code. The risk of a decision is what breaks if that rationale turns out to be wrong. Example: choosing JWT over session cookies is a decision; the risk is that revocation requires a denylist that may not exist.

An **assumption** is something the author *took for granted* — a precondition about the world that the code depends on being true, but does not verify or enforce. The risk of an assumption is the consequence if that precondition is violated at runtime. Example: assuming all callers will pass an `Authorization` header is an assumption baked into a middleware function; the risk is silent 401s for clients that use a different auth mechanism.

The distinction matters for reviewers: decisions invite debate about approach, while assumptions invite questions about whether the precondition is actually guaranteed elsewhere in the system.

#### Prompt 2: Anomaly Detection

Receives: full diffs of changed files; signatures and import structure of neighbouring files.

Instructs the model to identify structural deviations from the conventions visible in the surrounding codebase — unusual coupling, bypassed abstractions, patterns used in contexts where they are not normally found. Style differences are explicitly excluded. Returns JSON with an `anomalies` list, each with `description`, `location`, and `severity`.

#### Prompt 3: Test Gap Analysis

Receives: full diffs of changed files; existing test files for the changed modules.

Instructs the model to identify changed or new behaviours with no corresponding test. Focus areas are: new code paths (especially error paths and edge cases), changed logic in existing functions, and new exported symbols with no test file. Trivial getters, setters, and purely structural changes are explicitly excluded. Returns JSON with a `test_gaps` list.

#### Prompt 4: Security Signal Scoring

Receives: raw pattern signals from `security.py`; before-signatures of the affected files; full diff context.

Instructs the model to assess whether each signal is consistent with the purpose and existing patterns of the file it appears in. Returns JSON with a `security_signals` list, each with an adjusted `severity` and a `why_unusual` and `suggested_action` field.

#### Prompt 5: Semantic Equivalence (v0.4)

Receives: before/after signatures and diffs for each substantially changed symbol; historical anomaly patterns when available.

Instructs the model to classify each changed symbol as `equivalent` (refactor/reformat with no behavioural change), `risky` (small-looking diff that alters branching, state, or data flow), or `normal` (genuine non-trivial change). Returns JSON with a `semantic_verdicts` list. `equivalent` verdicts are called out in the report to save reviewer attention; `risky` verdicts are surfaced prominently.

---

### 5.7 `reporter.py` — Output Rendering

Assembles the final output from a fully populated `ImpactReport` model.

#### `render_markdown(report: ImpactReport) -> str`

Renders the human-readable Markdown report. Structure:

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
### `Symbol` in `file`
**Before:** {signature_before}
**After:** {signature_after}
**Callers:** {list of files}

## Decisions and Assumptions
{each Decision and Assumption from ai_analysis}

## Anomalies
{each Anomaly — 🟡 for medium, 🔴 for high severity}

## Test Gaps
{each TestGap}

## Semantic Equivalence
{each SemanticVerdict — "equivalent" collapsed, "risky" highlighted}

## Historical Hotspots
{each HistoricalHotspot — files frequently appearing in past blast radii}
```

#### `render_json(report: ImpactReport) -> str`

Serialises the full `ImpactReport` dataclass to JSON. Used as the machine-readable sidecar for downstream tooling.

#### `render_sarif(report: ImpactReport) -> str`

Serialises anomalies and test gaps to SARIF 2.1.0 format for ingestion by GitHub Advanced
Security, Azure DevOps, SonarQube, and similar tools. Anomalies map to SARIF results with
level `error` (high), `warning` (medium), or `note` (low). Test gaps map to `note`-level
results. Location strings of the form `file.py:12` or `file.py:function_name` are parsed
to populate SARIF physical locations.

#### `render_terminal(report: ImpactReport, console, ...) -> None`

Renders a Rich-formatted summary to the terminal (stderr). Uses panels, tables, and
severity emoji (🔴 high, 🟡 medium, 🔵 low). Called by `cli.py` after the pipeline
completes; does not affect the file outputs.

---

### 5.8 `github.py` — GitHub API Helpers

Handles all GitHub API interaction. No other module calls the GitHub API directly.

#### `detect_github_remote(remotes) -> tuple[str, str, str] | None`

- Inspects git remote URLs to detect a GitHub-hosted origin
- Returns `(owner, repo_name, remote_name)` triple if found; `None` if no GitHub remote is configured
- Prefers the `origin` remote over others; `remote_name` is passed to `ensure_commits_present` for fetching

#### `fetch_pr(owner, repo, number, token) -> dict`

- Calls the GitHub REST API to retrieve the raw PR object
- Returns the GitHub API response dict; the caller (`cli.py`) constructs a `RefsResult` from it
- Requires a valid `GITHUB_TOKEN` for private repositories

#### `fetch_open_prs(owner, repo, token) -> list[dict]`

- Lists open pull requests for the repository
- Used by `cli.py` for interactive PR selection in a terminal session

---

### 5.9 `models.py` — Shared Data Types

Defines all dataclasses listed in section 4. No logic. Imported by every other module. This is the single source of truth for the data contract between pipeline stages.

---

### 5.10 `security.py` — Security Signal Detection

Deterministic, regex-based security analysis. No network I/O except the optional OSV lookup. Called by `cli.py` before the AI layer; its output is passed to `ai_layer.run_ai_analysis` for contextual scoring.

#### `detect_pattern_signals(changed_files) -> list[SecuritySignal]`

Scans added lines in each changed file's diff against a table of high-signal patterns:

| Signal type | Examples |
|---|---|
| `network_call` | Hardcoded IP addresses, `requests.get`, `fetch()`, `socket.connect`, `net.Dial` |
| `credential` | Strings assigned to variables named `api_key`, `password`, `token`, etc. |
| `encoded_payload` | `base64.b64decode`, `atob()`, `Buffer.from(x, 'hex')` |
| `dynamic_exec` | `eval()`, `exec()`, `new Function()`, `subprocess` with `shell=True` |
| `shell_invoke` | `os.system`, `subprocess.run`, `child_process.exec`, `os/exec.Command` |
| `suspicious_import` | New imports of `socket`, `ctypes`, `child_process`, `dgram`, `pty` |

Severity is downgraded automatically when the same pattern already existed in the file before the PR, or when the file is in an infrastructure path (`build/`, `deploy/`, `scripts/`, etc.).

#### `check_dependency_integrity(changed_files, osv_check=False) -> list[DependencyIssue]`

Checks changes to package manifest files (`requirements.txt`, `pyproject.toml`, `package.json`, `Gemfile`, `go.mod`):

- **Typosquat detection** — computes Levenshtein distance between new package names and a curated list of top packages per ecosystem. Flags names within edit distance ≤ 2 of a popular package.
- **Version change flagging** — identifies packages present in both removed and added lines (i.e., version pin changed). Flagged as `low` severity for reviewer awareness.
- **OSV vulnerability lookup** — when `osv_check=True` (enabled by `--check-osv`), queries the OSV API (`api.osv.dev`) for known CVEs in newly added packages. Disabled by default to avoid unintended network calls.

Per-file failures are skipped silently; unexpected top-level errors propagate to `cli.py` which logs a warning.

---

### 5.11 `ast_extractor.py` — AST Wrappers

Wraps the `tree-sitter` library to provide language-aware AST parsing. Called by `dependency_graph.py` and `classifier.py`; neither module calls `tree-sitter` directly.

#### `extract_imports(file_path, content, language) -> list[str] | None`

- Parse `content` using the appropriate tree-sitter grammar for `language`
- Walk the AST to extract import paths/module names
- Returns a list of resolved import strings, or `None` if parsing fails (callers fall back to regex)
- Handles dynamic imports, re-exports, and barrel file patterns correctly

#### `extract_symbols(file_path, content, language) -> list[ChangedSymbol] | None`

- Parse `content` and extract all top-level function and class definitions
- Populate `name`, `kind`, `params`, `decorators`, and `return_type` from AST nodes
- Returns `None` if parsing fails (callers fall back to regex)

Failures are never propagated: the function always returns `None` rather than raising, so callers can fall back to regex without error handling.

---

### 5.12 `history.py` — Historical Pattern Learning

Maintains a local SQLite database of past analysis runs. History is best-effort: all functions in this module catch their own exceptions and never affect exit codes or pipeline output.

Default database path: `<repo>/.primpact/history.db`. Overridable via `--history-db`. Skipped entirely when `--no-history` is set.

#### `save_run(db_path, report) -> None`

- Persist the current `ImpactReport` to the history database after a successful run
- Records: run timestamp, base/head SHAs, changed files, blast radius entries, anomaly descriptions

#### `load_hotspots(db_path, limit=10) -> list[HistoricalHotspot]`

- Query the database for files that have appeared most frequently in past blast radii
- Returns up to `limit` `HistoricalHotspot` entries ordered by appearance count descending
- Returns an empty list if the database does not exist or the query fails

#### `load_anomaly_patterns(db_path) -> list[str]`

- Return a list of anomaly description strings from past runs
- Used to give the anomaly detection prompt (call 2) a sense of what has been flagged before, helping calibrate what is truly unusual for this codebase vs. a recurring known pattern
- Returns an empty list on any failure

---

## 6. Pipeline Orchestration

The pipeline runs as eight sequential steps inside `cli.py`. Steps 1–6b are deterministic; step 7 is the only network call.

```text
Pre-run  history.load_hotspots(db_path)
           → list[HistoricalHotspot]  (passed as hotspots to ai_layer)
         history.load_anomaly_patterns(db_path)
           → list[str]  (passed as anomaly_history to ai_layer)

Step 1  git_analysis.get_changed_files()
          → list[ChangedFile] (changed_symbols empty)

Step 2  dependency_graph.build_import_graph()
          → forward_graph: dict[str, list[str]]
          → reverse_graph: dict[str, list[str]] (inverted)

Step 3  dependency_graph.get_blast_radius(reverse_graph, changed_file_paths)
          → list[BlastRadiusEntry] (churn_score None at this stage)

Step 4  classifier.classify_changed_file(file) for each ChangedFile
          → populates ChangedFile.changed_symbols in place

Step 5  classifier.get_interface_changes(changed_files, reverse_graph)
          → list[InterfaceChange]

Step 6  git_analysis.get_git_churn(repo_path, entry.path) for each BlastRadiusEntry
          → populates BlastRadiusEntry.churn_score in place

Step 6a security.detect_pattern_signals(changed_files)
          → list[SecuritySignal] (deterministic regex scan)

Step 6b security.check_dependency_integrity(changed_files, osv_check)
          → list[DependencyIssue]

Step 7  ai_layer.run_ai_analysis(changed_files, blast_radius, repo_path, pattern_signals, anomaly_history, hotspots)
          → AIAnalysis (3 API calls; 4 when pattern_signals is non-empty; 5 when substantial diffs present)

Step 8  reporter.render_markdown(report) + reporter.render_json(report) + reporter.render_sarif(report)
          → str (Markdown), str (JSON), str (SARIF)

Post-run history.save_run(db_path, report)
           (best-effort; failures are silently swallowed)

Optional ai_layer.run_verdict_analysis(ai_analysis, dependency_issues)
          → Verdict  (only when --verdict or --verdict-json is given; 1 additional API call)
```

The `ImpactReport` is assembled in `cli.py` after step 7, combining all outputs from steps 1–7 (including historical hotspots) before passing to step 8.

**Performance target:** Steps 1–6b complete in under 5 seconds on a typical repository. Step 7 is network-bound and depends on Claude API response time.

---

## 7. External Interfaces

### Git Repository

`git_analysis.py` reads from the local git repository via `gitpython`. The repository must be accessible at the path provided to `--repo`. No write operations are performed.

### Claude API

`ai_layer.py` makes HTTPS POST requests to the Anthropic API at `https://api.anthropic.com/v1/messages`. Authentication is via the `ANTHROPIC_API_KEY` environment variable. The tool makes three to five API calls per successful run (always 3; +1 when security signals are detected; +1 when substantial diffs are present). All calls are made sequentially.

### GitHub API

`github.py` makes HTTPS requests to `https://api.github.com` when `--pr` is used or when
listing open PRs for interactive selection. Authentication is via the `GITHUB_TOKEN`
environment variable (or config file). Public repositories work without a token; private
repositories require one. No calls are made if neither `--pr` nor interactive PR selection
is triggered.

---

## 8. Context Budget Strategy

The Claude API context window is finite. The following rules govern what is included in each prompt call, in priority order:

| Priority | Content | Included in |
|---|---|---|
| 1 (always) | Full diffs of changed files | All prompts |
| 2 (always) | Signatures of distance-1 blast radius files | Prompts 1 and 2 |
| 3 (if budget allows) | Signatures of distance-2 blast radius files | Prompts 1 and 2 |
| 4 (prompt-specific) | Test files for changed modules | Prompt 3 only |
| 5 (prompt-specific) | Signatures of up to 5 neighbouring files | Prompt 2 only |
| 6 (prompt-specific) | Pattern signals + before-signatures | Prompt 4 only |
| 7 (prompt-specific) | Before/after signatures + historical anomaly patterns | Prompt 5 only |

Diffs are truncated at 8,000 tokens if the total exceeds the budget. Truncation is applied uniformly across all changed files rather than dropping files entirely where possible.

**Signature extraction** is used to include blast radius context efficiently. A signature contains: import statements, function and class definitions with decorators, and return type annotations — but not the function body. Since v0.4 this is extracted via `ast_extractor.py` (tree-sitter) with a regex fallback, reducing token cost by roughly 80% compared to including full file content.

---

## 9. Error Handling Strategy

The principle is: a partial report is always better than no report.

| Failure point | Behaviour |
|---|---|
| Git repository not found | Fail fast with clear error message |
| `ANTHROPIC_API_KEY` not set | Fail fast with clear error message |
| File not found at a given SHA | Skip that file, log warning to stderr |
| Import extraction fails for a file | Skip that file's imports, continue |
| Blast radius BFS times out | Return partial blast radius with note in report |
| Claude API call fails | Retry once; on second failure, return empty list for that analysis section |
| Claude API returns invalid JSON | Return empty list for that analysis section, log raw response to temp file |
| `render_markdown` fails | Propagate exception (this should never fail given valid models) |

Failures in steps 1–6 are logged to `stderr` and produce partial data. Failures in step 7 produce an `AIAnalysis` with empty fields. The report is always rendered; fields that could not be populated are omitted from the output with an explanatory note.

---

## 10. Output Format

### Markdown report

Human-readable. Intended to be read in a PR review interface or a Markdown viewer. Designed to be readable in under ten minutes. Sections are ordered by reviewer priority: summary first, then blast radius, then interface changes, then decisions and assumptions, then anomalies, then test gaps.

### JSON sidecar

Machine-readable. Full serialisation of the `ImpactReport` dataclass. Intended for downstream tooling — CI dashboards, integration with PR platforms (v0.2), and security tooling via SARIF (v0.2).

---

## 11. Dependencies

```
gitpython           # Git interaction (get_changed_files, get_git_churn, get_pr_metadata)
anthropic           # Claude API client (ai_layer)
click               # CLI argument parsing (cli)
rich                # Progress display and formatted stderr output (cli)
tree-sitter         # AST parsing core (ast_extractor)
tree-sitter-python  # Python grammar for tree-sitter (ast_extractor)
tree-sitter-typescript  # TypeScript/TSX grammar (ast_extractor)
tree-sitter-javascript  # JavaScript grammar (ast_extractor)
tree-sitter-c-sharp     # C# grammar (ast_extractor)
tree-sitter-java        # Java grammar (ast_extractor)
tree-sitter-go          # Go grammar (ast_extractor)
tree-sitter-ruby        # Ruby grammar (ast_extractor)
```

All dependencies are available on PyPI. Python 3.11 or later is required for `str | None` union syntax.

---

## 12. Configuration and Environment

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude API calls. The tool fails fast if not set. |
| `GITHUB_TOKEN` | No | GitHub personal access token. Required for `--pr` on private repos. |

Both can be set in `~/.pr_impact/config.toml` as an alternative to environment variables:

```toml
anthropic_api_key = "sk-ant-..."
github_token = "ghp-..."
```

Environment variables take precedence over the config file. The config file is read once at
startup; its values are applied only if the corresponding env var is not already set.

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--repo` | (required) | Path to the local git repository |
| `--pr` | (none) | GitHub PR number; resolves base/head SHAs automatically |
| `--base` | `HEAD~1` | Base commit SHA (ignored if `--pr` is given) |
| `--head` | `HEAD` | Head commit SHA (ignored if `--pr` is given) |
| `--output` | (none) | Write Markdown report to this file path |
| `--json` | (none) | Write JSON sidecar to this file path |
| `--sarif` | (none) | Write SARIF 2.1.0 report to this file path |
| `--max-depth` | `3` | Maximum BFS depth for blast radius calculation |
| `--fail-on-severity` | `none` | Exit 1 if any anomaly meets or exceeds this level (`low`/`medium`/`high`) |
| `--check-osv` | off | Query the OSV API for CVEs in newly added dependencies (requires network) |
| `--verdict` | off | Run agent verdict analysis after the main pipeline; exit 2 if blockers found |
| `--verdict-json` | (none) | Write verdict JSON to this file path (implies `--verdict`) |
| `--history-db` | `<repo>/.primpact/history.db` | Path to the SQLite history database (v0.4) |
| `--no-history` | off | Skip reading and writing the history database for this run (v0.4) |

If neither `--output` nor `--json` is provided, the Markdown report is printed to `stdout`.

---

## 13. Design Decisions and Rationale

### AST-first, regex fallback for import and symbol extraction

**Decision:** Use tree-sitter AST parsing as the primary extraction method, with regex as a fallback for parse failures or unsupported edge cases.

**Rationale:** AST parsing correctly handles dynamic imports, conditional imports, and barrel file re-exports that regex misses. tree-sitter grammars are available as PyPI packages, keeping the setup cost within the "no instrumentation tax" principle. Regex fallback ensures graceful degradation: if a grammar is unavailable or parsing fails, the tool still produces a useful (if less accurate) report.

**Risk:** tree-sitter adds native binary dependencies per language. Grammar packages must be kept in sync with the languages supported. The fallback path means some test surfaces are harder to cover exhaustively.

### Three to five separate prompt calls

**Decision:** Make separate API calls per analysis type (summary+decisions+assumptions, anomalies, test gaps, security scoring, semantic equivalence) rather than one large call.

**Rationale:** Each task requires different context. Anomaly detection needs neighbouring file signatures; test gap analysis needs test files; summary needs blast radius signatures; semantic equivalence needs before/after signatures. Combining all into one prompt would require all context types simultaneously, burning tokens on irrelevant material for each task. Separate calls also make it easier to handle partial failures gracefully — a failed security scoring call does not invalidate the anomaly analysis.

**Risk:** Up to five calls increase latency and API cost. Calls 4 and 5 are conditional so the worst case is bounded. At current pricing this is acceptable; at scale it may require optimisation.

### BFS depth capped at 3

**Decision:** The blast radius BFS stops at depth 3.

**Rationale:** Beyond depth 3, the graph typically reaches "utility" modules that everything depends on (logging, config, common types). Including them produces noise rather than signal. Depth 3 captures the meaningful downstream surface without overwhelming the reviewer.

**Risk:** In highly modular codebases, a genuinely affected module might be at depth 4 or 5. This is a known false-negative trade-off for v0.1.

### Signatures rather than full file content in prompts

**Decision:** Send only function/class signatures (not implementations) for blast radius context.

**Rationale:** The model needs to understand the design interface to assess fit and risk. It does not need to read the implementation of every downstream file. Signatures reduce token consumption by ~80% while preserving the design-relevant information.

**Risk:** The model may miss implementation-level anomalies in blast radius files. This is acceptable — those files are not changed in the PR being analysed.

### Prompts separated from logic

**Decision:** All prompt templates live in `prompts.py` with no logic, separate from `ai_layer.py`.

**Rationale:** Prompts will need iteration on a different cadence than the code logic around them. Keeping them in one file makes it easy to compare, version, and improve all prompts together without touching calling code.

### Single model for all calls

**Decision:** Use `claude-sonnet-4-5` for all three prompt calls.

**Rationale:** Consistent reasoning quality across all sections. The cost-per-token is acceptable for the analysis depth required.

---

## 14. Known Limitations and Deferred Work

### Current known limitations

- The classifier cannot distinguish between overloaded function signatures in TypeScript
- C# `using` resolution maps namespace declarations to files; a file with no `namespace` declaration is not reachable via the import graph
- C# symbol classification (interface/internal detection) is not implemented; `.cs` files produce blast radius and import graph entries but no `ChangedSymbol` records
- Churn scores do not account for file renames in git history
- No support for monorepos with multiple `package.json` or `pyproject.toml` files

### Delivered in v0.2

- Native GitHub PR input via `--pr` flag
- GitHub Actions and GitLab CI integration templates
- Support for Java, Go, and Ruby import graphs
- SARIF 2.1.0 output (`--sarif` flag)
- `--fail-on-severity` threshold flag
- Config file (`~/.pr_impact/config.toml`)
- Interactive PR selection in terminal sessions

### Delivered in v0.3

- Malicious pattern signal detection (`security.py`) — regex scan of added diff lines
- Contextual AI security scoring — 4th API call (`PROMPT_SECURITY_SIGNALS`) when signals detected
- Dependency integrity checks — typosquat detection, version-change flagging, optional OSV CVE lookup
- Agent verdict analysis — `PROMPT_VERDICT` / `--verdict` / `--verdict-json` for agentic loop control
- SARIF output extended to include `primpact/security-signal` and `primpact/dependency-issue` rules

### Delivered in v0.4

- AST-based import and symbol extraction via tree-sitter (`ast_extractor.py`), with regex fallback
- Re-export / barrel file support in import graph
- Richer `ChangedSymbol` fields: `params`, `decorators`, `return_type`
- Semantic equivalence detection — 5th AI call (`PROMPT_SEMANTIC_EQUIVALENCE`) classifies changes as equivalent/risky/normal
- Historical pattern learning via SQLite (`history.py`, `--history-db`, `--no-history`)
- Historical hotspots section in Markdown and terminal reports

### Explicitly out of scope (all versions)

- **Not a linter.** Style, formatting, and code quality rules are other tools' jobs.
- **Not a SAST scanner.** Semgrep, CodeQL, and bandit do comprehensive vulnerability scanning. Primpact does contextual anomaly detection. They are complementary.
- **Not a test runner.** Primpact identifies test gaps; it does not run or generate tests.
- **Not a code review replacement.** It is a pre-review triage tool. The human still reviews. Primpact decides where they look and what questions they ask.
- **Not a guarantee.** Especially for anomaly and (future) malicious code detection. Primpact raises signals. It does not provide assurance.
