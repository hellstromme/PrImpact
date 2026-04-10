# PrImpact

Analyse the impact of a code change. Given a PR number or two commit SHAs, PrImpact builds a blast radius graph, classifies changed symbols, and runs three AI calls to produce a Markdown report covering summary, decisions, assumptions, anomalies, and test gaps.

## Requirements

- Python ≥ 3.11
- git (accessible in PATH)
- `ANTHROPIC_API_KEY` environment variable

## Install

```bash
pip install -e .
```

## Usage

### From a GitHub PR number

```bash
pr-impact analyse --repo /path/to/repo --pr 247
```

Requires `GITHUB_TOKEN` for private repositories (public repos work without it).

### From explicit commit SHAs

```bash
pr-impact analyse --repo /path/to/repo --base abc1234 --head def5678
```

If neither `--pr` nor `--base`/`--head` are given, PrImpact analyses `HEAD~1 → HEAD`.

### Write output to files

```bash
pr-impact analyse \
  --repo /path/to/repo \
  --pr 247 \
  --output report.md \
  --json report.json
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | required | Path to the local git repository |
| `--pr` | — | GitHub PR number (resolves SHAs automatically) |
| `--base` | `HEAD~1` | Base commit SHA (ignored if `--pr` is given) |
| `--head` | `HEAD` | Head commit SHA (ignored if `--pr` is given) |
| `--output` | none | Write Markdown report to this file |
| `--json` | none | Write JSON sidecar to this file |
| `--sarif` | none | Write SARIF 2.1.0 report to this file |
| `--max-depth` | 3 | BFS depth for blast radius (cap at 3 recommended) |
| `--fail-on-severity` | `none` | Exit 1 if any anomaly meets or exceeds this level (`low`/`medium`/`high`) |

## Environment variables

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # required
export GITHUB_TOKEN=ghp-...           # optional; required for private repos with --pr
```

Both can also be set in `~/.pr_impact/config.toml`:

```toml
anthropic_api_key = "sk-ant-..."
github_token = "ghp-..."
```

## Output

The Markdown report contains:

- **Summary** — what the change does, in plain English
- **Blast Radius** — files downstream of the change, with BFS distance and churn score
- **Decisions and Assumptions** — design choices inferred from the diff, with rationale and risks
- **Anomalies** — patterns that are inconsistent with the surrounding codebase
- **Test Gaps** — behaviours that are changed but not covered by existing tests

Progress and warnings are written to stderr. The Markdown report goes to stdout (or `--output`).

## Supported languages

Python, TypeScript, JavaScript, C#, Java, Go, Ruby

## CI/CD integration

### GitHub Actions

Add `.github/workflows/pr-impact.yml` to your repository — see the template at
[`.github/workflows/pr-impact.yml`](.github/workflows/pr-impact.yml).

The workflow runs on every non-draft PR, posts the report as a collapsible PR comment,
uploads `pr_impact_report.md` and `pr_impact_report.json` as artifacts, and exits 1 if
any high-severity anomaly is found.

Required secrets: `ANTHROPIC_API_KEY`. `GITHUB_TOKEN` is provided automatically by
Actions.

### GitLab CI

See the template at [`ci/gitlab-ci-template.yml`](ci/gitlab-ci-template.yml). Copy the
`primpact` job into your `.gitlab-ci.yml`.

Required CI/CD variables: `ANTHROPIC_API_KEY`, `GITLAB_TOKEN` (a project/group access
token with `api` scope).

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
