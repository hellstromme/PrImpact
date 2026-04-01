# Primpact — Architecture Document

**Version:** 0.1 (MVP)  
**Status:** Design complete, pre-implementation  
**Last updated:** 2026-03-29

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
   - 5.8 [models.py — Shared Data Types](#58-modelspy--shared-data-types)
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
  dependency_graph.py  # Regex-based import graph builder and blast radius BFS
  classifier.py        # Changed symbol classification by impact type
  ai_layer.py          # Claude API calls — summary, decisions, anomalies, test gaps
  prompts.py           # All prompt templates (separated from logic)
  reporter.py          # Assembles and renders the final Markdown and JSON report
  models.py            # Shared dataclasses used across all modules
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
    language: str           # 'python' | 'typescript' | 'javascript' | 'csharp' | 'unknown'
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
```

---

## 5. Module Specifications

### 5.1 `cli.py` — Entry Point

Uses `click` for argument parsing. Owns the pipeline orchestration loop. Prints progress to `stderr` so `stdout` stays clean for piping.

#### Command signature

```
pr-impact analyse \
  --repo /path/to/repo \
  --base abc1234 \
  --head def5678 \
  [--output report.md] \
  [--json report.json] \
  [--max-depth 3]
```

#### Responsibilities

- Parse and validate CLI arguments
- Call each pipeline step in order (see section 6)
- Write Markdown to `stdout` by default; write to `--output` file if specified
- Write JSON sidecar to `--json` file if specified
- Display progress output via `rich` to `stderr`
- Fail fast with a clear error message if `ANTHROPIC_API_KEY` is not set

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

---

### 5.3 `dependency_graph.py` — Import Graph

Builds an import graph for the whole codebase using regex-based import extraction. AST parsing is explicitly deferred to v0.4.

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

Classifies each changed file and symbol by impact type. Uses regex against diff content and file content. No AST parsing in v0.1.

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

Makes three Claude API calls per analysis run. Handles context budget management. This is the only module that performs network I/O.

#### `run_ai_analysis(changed_files, blast_radius, repo_path) -> AIAnalysis`

Assembles context for each prompt, makes three API calls in sequence, parses the JSON responses, and returns a populated `AIAnalysis` object.

The three calls map to the three prompts in `prompts.py`:

| Call | Prompt | Output fields |
|---|---|---|
| 1 | Summary + Decisions + Assumptions | `AIAnalysis.summary`, `.decisions`, `.assumptions` |
| 2 | Anomaly Detection | `AIAnalysis.anomalies` |
| 3 | Test Gap Analysis | `AIAnalysis.test_gaps` |

#### Context assembly

For each prompt call, context is assembled in this priority order:

1. **Full diffs of all changed files** — always included; truncated at 8,000 tokens if needed
2. **Signatures (not implementations) of blast radius files at distance 1** — always included
3. **Signatures of blast radius files at distance 2** — included if token budget permits
4. **Test files for changed modules** — for prompt 3 only
5. **Signatures of up to 5 neighbouring files** (same directory as each changed file) — for prompt 2 (anomaly detection)

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
```

#### `render_json(report: ImpactReport) -> str`

Serialises the full `ImpactReport` dataclass to JSON. Used as the machine-readable sidecar for downstream tooling.

---

### 5.8 `models.py` — Shared Data Types

Defines all dataclasses listed in section 4. No logic. Imported by every other module. This is the single source of truth for the data contract between pipeline stages.

---

## 6. Pipeline Orchestration

The pipeline runs as eight sequential steps inside `cli.py`. Steps 1–6 are deterministic; step 7 is the only network call.

```
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

Step 7  ai_layer.run_ai_analysis(changed_files, blast_radius, repo_path)
          → AIAnalysis

Step 8  reporter.render_markdown(report) + reporter.render_json(report)
          → str (Markdown), str (JSON)
```

The `ImpactReport` is assembled in `cli.py` after step 7, combining all outputs from steps 1–7 before passing to step 8.

**Performance target:** Steps 1–6 complete in under 5 seconds on a typical repository. Step 7 is network-bound and depends on Claude API response time.

---

## 7. External Interfaces

### Git Repository

`git_analysis.py` reads from the local git repository via `gitpython`. The repository must be accessible at the path provided to `--repo`. No write operations are performed.

### Claude API

`ai_layer.py` makes HTTPS POST requests to the Anthropic API at `https://api.anthropic.com/v1/messages`. Authentication is via the `ANTHROPIC_API_KEY` environment variable. The tool makes exactly three API calls per successful run. All calls are made sequentially; there is no parallelism in v0.1.

---

## 8. Context Budget Strategy

The Claude API context window is finite. The following rules govern what is included in each prompt call, in priority order:

| Priority | Content | Included in |
|---|---|---|
| 1 (always) | Full diffs of changed files | All three prompts |
| 2 (always) | Signatures of distance-1 blast radius files | Prompts 1 and 2 |
| 3 (if budget allows) | Signatures of distance-2 blast radius files | Prompts 1 and 2 |
| 4 (prompt-specific) | Test files for changed modules | Prompt 3 only |
| 5 (prompt-specific) | Signatures of up to 5 neighbouring files | Prompt 2 only |

Diffs are truncated at 8,000 tokens if the total exceeds the budget. Truncation is applied uniformly across all changed files rather than dropping files entirely where possible.

**Signature extraction** is used to include blast radius context efficiently. A signature contains: import statements, function and class definitions with decorators, and return type annotations — but not the function body. This is extracted with regex and reduces token cost by roughly 80% compared to including full file content.

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
gitpython      # Git interaction (get_changed_files, get_git_churn, get_pr_metadata)
anthropic      # Claude API client (ai_layer)
click          # CLI argument parsing (cli)
rich           # Progress display and formatted stderr output (cli)
```

All dependencies are available on PyPI. Python 3.11 or later is required for `str | None` union syntax.

---

## 12. Configuration and Environment

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude API calls. The tool fails fast if not set. |

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--repo` | (required) | Path to the local git repository |
| `--base` | (required) | Base commit SHA |
| `--head` | (required) | Head commit SHA |
| `--output` | (none) | Write Markdown report to this file path |
| `--json` | (none) | Write JSON sidecar to this file path |
| `--max-depth` | `3` | Maximum BFS depth for blast radius calculation |

If neither `--output` nor `--json` is provided, the Markdown report is printed to `stdout`.

---

## 13. Design Decisions and Rationale

### Regex over AST for import extraction

**Decision:** Use regex-based import extraction rather than AST parsing (tree-sitter or the language's own parser).

**Rationale:** Tree-sitter requires native binaries and adds setup complexity that conflicts with the "no instrumentation tax" principle. For MVP, regex is sufficient to extract import relationships accurately enough for blast radius calculation. The diff context lines provide enough signal for the classifier without full AST traversal.

**Risk:** Regex will miss dynamic imports, conditional imports, and barrel file re-exports. These edge cases are acceptable for v0.1. AST parsing is explicitly planned for v0.4.

### Three separate prompt calls

**Decision:** Make three separate API calls (summary+decisions+assumptions, anomalies, test gaps) rather than one large call.

**Rationale:** Each task requires different context. Anomaly detection needs neighbouring file signatures; test gap analysis needs test files; summary needs blast radius signatures. Combining all three into one prompt would require including all context types simultaneously, burning tokens on irrelevant material for each task. Separate calls also make it easier to handle partial failures gracefully.

**Risk:** Three calls increase latency and API cost by approximately 3×. At current pricing this is acceptable; at scale it may require optimisation.

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

### v0.1 known limitations

- Import extraction does not handle dynamic imports (`import()`, `__import__()`, `importlib.import_module()`)
- Re-exports via barrel files (`index.ts`) may inflate the blast radius with false positives
- Relative import resolution may be inaccurate for deeply nested package structures
- The classifier cannot distinguish between overloaded function signatures in TypeScript
- C# `using` resolution maps namespace declarations to files; a file with no `namespace` declaration is not reachable via the import graph
- C# symbol classification (interface/internal detection) is not implemented; `.cs` files produce blast radius and import graph entries but no `ChangedSymbol` records
- Churn scores do not account for file renames in git history
- No support for monorepos with multiple `package.json` or `pyproject.toml` files

### Deferred to v0.2

- Native GitHub/GitLab PR input via `--pr` flag (currently requires manual SHAs)
- GitHub Actions integration for automatic PR commenting
- Support for Java, Go, and Ruby import graphs
- SARIF output format (`--sarif` flag)

### Deferred to v0.3

- Malicious code detection (pattern signals + contextual AI scoring + dependency integrity checks)
- Dependency manifest analysis (`package.json`, `requirements.txt`, `pyproject.toml`)

### Deferred to v0.4

- AST-based import extraction via tree-sitter (replacing regex)
- Symbol-level blast radius (currently file-level only)
- Semantic equivalence detection (identifying refactors that look significant but aren't)
- Historical pattern learning via local SQLite database

### Explicitly out of scope (all versions)

- **Not a linter.** Style, formatting, and code quality rules are other tools' jobs.
- **Not a SAST scanner.** Semgrep, CodeQL, and bandit do comprehensive vulnerability scanning. Primpact does contextual anomaly detection. They are complementary.
- **Not a test runner.** Primpact identifies test gaps; it does not run or generate tests.
- **Not a code review replacement.** It is a pre-review triage tool. The human still reviews. Primpact decides where they look and what questions they ask.
- **Not a guarantee.** Especially for anomaly and (future) malicious code detection. Primpact raises signals. It does not provide assurance.
