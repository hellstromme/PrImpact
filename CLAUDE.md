# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow

- **Never commit directly to `main`.** Always create a feature branch before starting any work.
- When work is ready to merge, raise a pull request back to `main` — do not merge directly.

## Project Status

v0.2 — active development. v0.1 MVP is complete. v0.2 language expansion (Java, Go, Ruby),
`--pr` GitHub native input, and CI/CD integration (GitHub Actions + GitLab CI template,
`--fail-on-severity`) are done. SARIF output is the remaining v0.2 item.

## Commands

```bash
# Install
pip install -e .

# Analyse a GitHub PR
pr-impact analyse --repo /path/to/repo --pr 247

# Analyse explicit SHAs
pr-impact analyse --repo /path/to/repo --base abc1234 --head def5678

# With file output
pr-impact analyse --repo /path/to/repo --pr 247 --output report.md --json report.json

# Fail CI on high-severity anomalies
pr-impact analyse --repo /path/to/repo --pr 247 --fail-on-severity high

# Required environment variables
export ANTHROPIC_API_KEY=...
export GITHUB_TOKEN=...   # optional; needed for --pr on private repos
```

## Architecture

PrImpact is a linear 8-step pipeline CLI tool. `cli.py` orchestrates the pipeline; all other modules are called by it and do not call each other (except all importing `models.py`).

### Package structure

```
pr_impact/
  cli.py               # Entry point (click), pipeline orchestration, progress to stderr
  models.py            # Shared dataclasses — single source of truth for data contracts
  git_analysis.py      # All git interaction (gitpython) — diffs, content, churn
  dependency_graph.py  # Regex-based import graph + BFS blast radius calculation
  classifier.py        # Changed symbol classification by impact type (regex, no AST)
  ai_layer.py          # Three Claude API calls — the only network I/O
  prompts.py           # All prompt templates as string constants, no logic
  reporter.py          # Renders final Markdown and JSON from ImpactReport
  github.py            # GitHub API helpers (detect remote, fetch PR, list PRs)
```

### Pipeline steps (in order, all in cli.py)

1. `git_analysis.get_changed_files()` → `list[ChangedFile]`
2. `dependency_graph.build_import_graph()` → forward + reverse import graphs
3. `dependency_graph.get_blast_radius(reverse_graph, ...)` → `list[BlastRadiusEntry]`
4. `classifier.classify_changed_file(file)` for each file → populates `ChangedFile.changed_symbols` in place
5. `classifier.get_interface_changes(changed_files, reverse_graph)` → `list[InterfaceChange]`
6. `git_analysis.get_git_churn(...)` for each blast radius entry → populates `BlastRadiusEntry.churn_score` in place
7. `ai_layer.run_ai_analysis(...)` → `AIAnalysis` (3 sequential API calls)
8. `reporter.render_markdown()` + `reporter.render_json()` → output

Steps 1–6 are deterministic and CPU-bound (target: <5s). Step 7 is the only network call.

### Key data models (`models.py`)

- `ImpactReport` — top-level aggregation passed to reporter
- `ChangedFile` — file path, language, diff, before/after content, `changed_symbols: list[ChangedSymbol]`
- `ChangedSymbol` — name, kind, `change_type` (see classifier), before/after signatures
- `BlastRadiusEntry` — path, distance (BFS hops), imported symbols, churn score
- `InterfaceChange` — public symbol with changed signature + list of caller files
- `AIAnalysis` — summary, decisions, assumptions, anomalies, test gaps

### AI layer (3 calls per run)

| Call | Prompt | Context included |
|------|--------|-----------------|
| 1 | Summary + Decisions + Assumptions | Diffs + blast radius signatures |
| 2 | Anomaly Detection | Diffs + neighbouring file signatures |
| 3 | Test Gap Analysis | Diffs + existing test files |

Model: `claude-sonnet-4-5`. Prompts are in `prompts.py` (iterable independently of logic).

Context priority order: full diffs (always, truncated at 8k tokens) → distance-1 signatures → distance-2 signatures → prompt-specific files. Signatures = imports + function/class definitions without bodies (~80% token savings vs full file).

### Design constraints to preserve

- **Regex over AST** — no tree-sitter in v0.1; AST planned for v0.4
- **BFS depth cap at 3** — beyond 3 hops reaches utility modules (noise, not signal)
- **Graceful degradation** — every stage catches its own failures; partial reports are always better than crashes; empty lists over exceptions for AI fields
- **stdout clean** — Markdown output to stdout, progress/warnings to stderr
- **No module cross-imports** — all modules import `models.py` but not each other

### Supported languages

Python (`.py`), TypeScript (`.ts`, `.tsx`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`),
C# (`.cs`), Java (`.java`), Go (`.go`), Ruby (`.rb`)
