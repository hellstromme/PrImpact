# PrImpact

Analyse the impact of a code change between two commits. Given a base and head SHA, PrImpact builds a blast radius graph, classifies changed symbols, and runs three AI calls to produce a Markdown report covering summary, decisions, assumptions, anomalies, and test gaps.

## Requirements

- Python ≥ 3.11
- git (accessible in PATH)
- `ANTHROPIC_API_KEY` environment variable

## Install

```bash
pip install -e .
```

## Usage

```bash
python -m pr_impact.cli analyse \
  --repo /path/to/repo \
  --base <base-sha> \
  --head <head-sha>
```

Write output to files instead of stdout:

```bash
python -m pr_impact.cli analyse \
  --repo /path/to/repo \
  --base <base-sha> \
  --head <head-sha> \
  --output report.md \
  --json report.json
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | required | Path to the local git repository |
| `--base` | required | Base commit SHA |
| `--head` | required | Head commit SHA |
| `--output` | stdout | Write Markdown report to this file |
| `--json` | none | Write JSON sidecar to this file |
| `--max-depth` | 3 | BFS depth for blast radius (cap at 3 recommended) |

## Environment variable

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Output

The Markdown report contains:

- **Summary** — what the change does, in plain English
- **Blast Radius** — files downstream of the change, with BFS distance and churn score
- **Decisions and Assumptions** — design choices inferred from the diff, with rationale and risks
- **Anomalies** — patterns that are inconsistent with the surrounding codebase
- **Test Gaps** — behaviours that are changed but not covered by existing tests

Progress and warnings are written to stderr. The Markdown report goes to stdout (or `--output`).

## Development

```bash
pip install -e ".[dev]"

# Tests
python -m pytest

# Lint
python -m ruff check pr_impact/ tests/
python -m ruff format pr_impact/ tests/

# Type check
python -m pyright pr_impact/
```
