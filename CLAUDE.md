# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow

- **Never commit directly to `main`.** Always create a feature branch before starting any work.
- When work is ready to merge, raise a pull request back to `main` — do not merge directly.

## Project Status

v0.4 — complete. All v0.4 items are done:
- AST-based import and symbol extraction via tree-sitter (`ast_extractor.py`) ✓
- Symbol-level dependency tracking in `dependency_graph.py` (AST-first, regex fallback) ✓
- Re-export / barrel file support in import graph ✓
- Richer `ChangedSymbol` fields: `params`, `decorators`, `return_type` ✓
- Semantic equivalence detection (5th AI call via `PROMPT_SEMANTIC_EQUIVALENCE`) ✓
- Historical pattern learning via SQLite (`history.py`, `--history-db`, `--no-history`) ✓
- Historical hotspots section in Markdown + terminal report ✓

v0.3 items (complete):
- Language expansion (Java, Go, Ruby) ✓ (v0.2)
- `--pr` GitHub native input ✓ (v0.2)
- CI/CD integration (GitHub Actions + GitLab CI template, `--fail-on-severity`) ✓ (v0.2)
- SARIF output (`--sarif`) ✓ (v0.2)
- Malicious pattern signal detection (`security.py:detect_pattern_signals`) ✓
- Contextual AI security scoring (4th API call via `PROMPT_SECURITY_SIGNALS`) ✓
- Dependency integrity checks (`--check-osv`, typosquat detection, OSV lookup) ✓
- Agent verdict analysis (`--verdict`, `--verdict-json`) ✓

Next: v1.0 — Platform (web UI, persistent history, team configuration).

## Commands

```bash
# Install
pip install -e .

# Analyse a GitHub PR
pr-impact analyse --repo /path/to/repo --pr 247

# Analyse explicit SHAs
pr-impact analyse --repo /path/to/repo --base abc1234 --head def5678

# With file output
pr-impact analyse --repo /path/to/repo --pr 247 --output report.md --json report.json --sarif report.sarif

# Fail CI on high-severity anomalies
pr-impact analyse --repo /path/to/repo --pr 247 --fail-on-severity high

# Check new dependencies against OSV vulnerability database
pr-impact analyse --repo /path/to/repo --pr 247 --check-osv

# Run agent verdict analysis (exit 2 if blockers found)
pr-impact analyse --repo /path/to/repo --pr 247 --verdict --verdict-json verdict.json

# Use a custom history database path (default: <repo>/.primpact/history.db)
pr-impact analyse --repo /path/to/repo --pr 247 --history-db /path/to/history.db

# Skip history read and write
pr-impact analyse --repo /path/to/repo --pr 247 --no-history

# Required environment variables
export ANTHROPIC_API_KEY=...
export GITHUB_TOKEN=...   # optional; needed for --pr on private repos
```

## Architecture

PrImpact is a linear 8-step pipeline CLI tool. `cli.py` orchestrates the pipeline; all other modules are called by it and do not call each other (except all importing `models.py`, `ast_extractor.py`, or `history.py`).

Helper modules (`config.py`, `language_resolvers.py`, `ai_client.py`, `ai_context.py`) are exempt from this constraint — each is called by exactly one pipeline module and does not call other pipeline modules.

### Package structure

```text
pr_impact/
  cli.py                  # Entry point (click), pipeline orchestration, progress to stderr
  models.py               # Shared dataclasses — single source of truth for data contracts
  ast_extractor.py        # tree-sitter AST wrappers — extract_imports() and extract_symbols()
  history.py              # SQLite history: save_run(), load_hotspots(), load_anomaly_patterns()
  config.py               # Config file loading (~/.pr_impact/config.toml) — called by cli.py only
  git_analysis.py         # All git interaction (gitpython) — diffs, content, churn
  dependency_graph.py     # BFS blast radius calculation; delegates resolution to language_resolvers.py
  language_resolvers.py   # Per-language import resolvers + extract_imports_for_file — called by dependency_graph.py only
  classifier.py           # Changed symbol classification by impact type (AST-first, regex fallback)
  ai_layer.py             # AI analysis orchestration (3–5 calls); delegates to ai_client.py + ai_context.py
  ai_client.py            # Anthropic API glue (_call_claude, call_api, JSON parsing) — called by ai_layer.py only
  ai_context.py           # Prompt/context builders (diffs, signatures, test files) — called by ai_layer.py only
  prompts.py              # All prompt templates as string constants, no logic
  reporter.py             # Renders final Markdown, JSON, SARIF, and terminal output from ImpactReport
  github.py               # GitHub API helpers (detect remote, fetch PR, list PRs)
  security.py             # Deterministic security signal detection + dependency integrity checks
```

### Pipeline steps (in order, all in cli.py)

1. `git_analysis.get_changed_files()` → `list[ChangedFile]`
2. `dependency_graph.build_import_graph()` → forward + reverse import graphs
3. `dependency_graph.get_blast_radius(reverse_graph, ...)` → `list[BlastRadiusEntry]`
4. `classifier.classify_changed_file(file)` for each file → returns `list[ChangedSymbol]`; caller assigns to `file.changed_symbols`
5. `classifier.get_interface_changes(changed_files, reverse_graph)` → `list[InterfaceChange]`
6. `git_analysis.get_git_churn(...)` for each blast radius entry → populates `BlastRadiusEntry.churn_score` in place
6a. `security.detect_pattern_signals(changed_files)` → `list[SecuritySignal]` (deterministic regex scan)
6b. `security.check_dependency_integrity(changed_files)` → `list[DependencyIssue]`
7. `ai_layer.run_ai_analysis(...)` → `AIAnalysis` (3 API calls; 4 when security signals present)
8. `reporter.render_markdown()` + `reporter.render_json()` + `reporter.render_sarif()` → output

Steps 1–6b are deterministic and CPU-bound (target: <5s). Step 7 is the only network call.

### Key data models (`models.py`)

- `ImpactReport` — top-level aggregation passed to reporter; includes `historical_hotspots`
- `ChangedFile` — file path, language, diff, before/after content, `changed_symbols: list[ChangedSymbol]`
- `ChangedSymbol` — name, kind, `change_type` (see classifier), before/after signatures, `params`, `decorators`, `return_type`
- `BlastRadiusEntry` — path, distance (BFS hops), imported symbols, churn score
- `InterfaceChange` — public symbol with changed signature + list of caller files
- `AIAnalysis` — summary, decisions, assumptions, anomalies, test gaps, security signals, `semantic_verdicts`
- `SemanticVerdict` — file, symbol, verdict ("equivalent"/"risky"/"normal"), reason
- `SecuritySignal` — description, file path, line number, signal type, severity, why unusual, suggested action
- `DependencyIssue` — package name, issue type (typosquat/version_change/vulnerability), description, severity
- `HistoricalHotspot` — file, appearances count
- `Verdict` — status, `agent_should_continue` bool, rationale, list of `VerdictBlocker`

### AI layer (3–5 calls per run)

| Call | Prompt | Context included | Condition |
|------|--------|-----------------|-----------|
| 1 | Summary + Decisions + Assumptions | Diffs + blast radius signatures | Always |
| 2 | Anomaly Detection | Diffs + before-signatures + neighbouring file signatures + historical context | Always |
| 3 | Test Gap Analysis | Diffs + existing test files | Always |
| 4 | Security Signal Scoring | Pattern signals + diffs + file context | Only when signals detected |
| 5 | Semantic Equivalence | Diffs + before/after signatures | Only when substantial diffs present |

Model: `claude-sonnet-4-5`. Prompts are in `prompts.py` (iterable independently of logic).

Context priority order: full diffs (always, truncated at 8k tokens) → distance-1 signatures → distance-2 signatures → prompt-specific files. Signatures = imports + function/class definitions without bodies (~80% token savings vs full file).

### Design constraints to preserve

- **AST-first, regex fallback** — `ast_extractor.py` wraps tree-sitter; callers fall back to regex if `None` is returned
- **BFS depth cap at 3** — beyond 3 hops reaches utility modules (noise, not signal)
- **Graceful degradation** — every stage catches its own failures; partial reports are always better than crashes; empty lists over exceptions for AI fields
- **stdout clean** — Markdown output to stdout, progress/warnings to stderr
- **Shared modules** — `models.py`, `ast_extractor.py`, and `history.py` may be imported by pipeline modules; cross-pipeline-module imports are forbidden. Helper modules (`config.py`, `language_resolvers.py`, `ai_client.py`, `ai_context.py`) are each imported by exactly one pipeline module.
- **History is best-effort** — `history.py` failures are silently swallowed and never affect exit codes

### Supported languages

Python (`.py`), TypeScript (`.ts`, `.tsx`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`),
C# (`.cs`), Java (`.java`), Go (`.go`), Ruby (`.rb`)
