# Primpact — Product Roadmap

**Working title:** Primpact  
**Mission:** Give humans genuine understanding of what a code change does to a system,
in the time it takes to drink a coffee.

---

## Guiding Principles

- **No instrumentation tax.** Every version should work on a repo with no prior setup.
- **Partial is better than nothing.** Every pipeline stage fails gracefully. A report
  with gaps is more useful than a crash.
- **Human in the loop, not human in the detail.** The tool surfaces decisions and risks.
  It does not make them. Recommendations are always advisory.
- **Output is a first-class citizen.** The Markdown report and JSON sidecar are the
  product. Internal analysis quality is only visible through output quality.
- **Malicious detection is never a guarantee.** This is stated explicitly in all user-
  facing output. Primpact is a signal, not a security audit.

---

## Version Overview

| Version | Theme | Key Capability Added |
|---------|-------|----------------------|
| v0.1 | Foundation | MVP — blast radius, AI decisions/assumptions, test gaps |
| v0.2 | Integration | GitHub/GitLab native, multi-language, CI output formats |
| v0.3 | Trust | Malicious code detection — first signals |
| v0.4 | Depth | AST-based analysis, richer anomaly detection |
| v1.0 | Platform | Web UI, persistent history, team configuration |
| v2.0 | Intelligence | Runtime telemetry integration, roadmap awareness |

---

## v0.1 — Foundation (MVP)

*The design document covers this in full. Summary only here.*

**Capabilities:**
- Regex-based import graph across Python, TypeScript, JavaScript
- BFS blast radius to depth 3
- Change classification (internal / interface / dependency / new / deleted)
- Three-prompt AI analysis: summary + decisions + assumptions, anomalies, test gaps
- Churn scoring from git history
- CLI with `--repo`, `--base`, `--head` flags
- Markdown report + JSON sidecar output

**Success criterion:** A developer unfamiliar with a module can read the report and
make a confident go/no-go decision on whether to look closer — in under ten minutes.

---

## v0.2 — Integration

**Theme:** Make Primpact run where the work already happens.

### GitHub / GitLab native input

Replace `--base` / `--head` with `--pr` flag that resolves directly from the
platform API. Primpact fetches the PR metadata, diff, and commit range itself.

```
primpact analyse --repo owner/repo --pr 247
```

Requires a `GITHUB_TOKEN` / `GITLAB_TOKEN` env var. Falls back to local git if
not available.

### CI/CD integration

A GitHub Actions step and a GitLab CI template that:
- Runs Primpact on every PR
- Posts the Markdown report as a PR comment (collapsible, with a summary header)
- Fails the step (optionally) if anomaly severity threshold is breached

The JSON sidecar is uploaded as a build artefact, enabling downstream tooling.

### Language expansion

Add support for:
- **Java** — import graph only; AI layer already handles it
- **C#** — using statements, namespace graph
- **Go** — import graph is explicit and clean; straightforward addition
- **Ruby** — require/require_relative patterns

The classifier and AI layer are language-agnostic. Only the import extractor is
language-specific — each new language is an addition to one module.

### Structured SARIF output

SARIF is the standard format for static analysis results in GitHub, Azure DevOps,
and many security tools. Adding a `--sarif` output flag means Primpact results can
be imported into existing security dashboards without custom integration work.

**Success criterion:** A team can add Primpact to a new repo in under fifteen minutes
with no local tool installation.

---

## v0.3 — Trust (Malicious Code Detection)

**Theme:** Surface code that should not be there.

This is the most sensitive capability in the roadmap. It requires careful design
because false positives have a real cost (alert fatigue, eroded trust in the tool)
and false negatives have a different real cost (missed genuine threats). The design
philosophy is: **flag signals, not verdicts**.

---

### What "malicious code in a PR" actually looks like

Malicious code in a PR is not usually the cartoonish kind. It falls into categories:

**Supply chain manipulation**
- A dependency version pinned to a compromised release
- A new dependency added that shadows a well-known package (dependency confusion)
- A dependency replaced with a fork that has a near-identical name

**Exfiltration**
- Sensitive data (credentials, tokens, PII) flowing to a new external endpoint
- Logging statements that capture data they shouldn't
- Environment variables being serialised and transmitted

**Backdoors**
- Authentication bypasses conditional on a specific input value
- Hidden admin accounts or hardcoded credentials
- Dead-looking code that activates on a specific date, hostname, or environment flag

**Obfuscation**
- Encoded strings (base64, hex) decoded at runtime
- Eval / exec of dynamic strings
- Unusual indirection: function pointers, dynamic dispatch where it's not the pattern

**Privilege escalation**
- File system access outside expected paths
- Shell execution (`subprocess`, `exec`, `spawn`) added where it didn't exist
- Network socket creation in a module that had no network access before

**Logic bombs**
- Conditions that trigger on specific dates or after N executions
- Environment-specific behaviour (acts differently in CI vs production)

---

### Detection approach

Primpact is not a signature-based scanner (those already exist — semgrep, bandit,
CodeQL). It adds a complementary layer: **contextual and behavioural analysis**,
asking not just "is this pattern present" but "is this pattern surprising *in this
codebase and this PR*?"

**Layer 1 — Pattern signals (deterministic)**

Regex and AST-based detection of high-signal patterns in the diff:

| Signal | Examples |
|--------|---------|
| New network calls | Requests to IPs/domains not present in rest of codebase |
| Credential patterns | Strings matching API key / token / password patterns |
| Encoded payloads | base64.decode, Buffer.from(x, 'hex'), atob() on non-trivial strings |
| Dynamic execution | eval, exec, Function(), subprocess with shell=True |
| New file permissions | chmod calls, setuid patterns |
| Shell invocation | Any new use of os.system, child_process.exec, etc. |
| Suspicious imports | New imports of network, crypto, or os modules in unexpected contexts |

These are scored, not binary. A `subprocess` call in a build script is expected.
The same call added to a payment processing module is a high-severity signal.

**Layer 2 — Contextual scoring (AI-assisted)**

The pattern signals are fed to the AI layer with a dedicated prompt. The model's
job is to assess whether each signal is consistent with the purpose and existing
patterns of the file it appears in.

This is where Primpact goes beyond a standard scanner. Semgrep can tell you there's
a `subprocess` call. Primpact can tell you "this `subprocess` call was added to the
authentication module and there are no other shell invocations in that module or its
neighbours."

**Layer 3 — Dependency integrity (for package manifest changes)**

When a PR modifies `package.json`, `requirements.txt`, `pyproject.toml`,
`Gemfile`, etc.:
- Check new dependencies against known typosquatting patterns
  (edit distance from popular packages)
- Flag pinned versions that changed without a corresponding changelog reference
- Flag packages with very low download counts added alongside high-trust packages
- Cross-reference against OSV (Open Source Vulnerability database) — free, no API
  key required

---

### Malicious detection output section

Added to the report as a dedicated section, after anomalies:

```markdown
## Security Signals

> ⚠️ Primpact is not a security audit. These are signals for human review,
> not verdicts. Treat HIGH signals as "requires explanation", not "is malicious".

### 🔴 HIGH — New external network call in authentication module
**File:** src/auth/session.ts · line 47
**Signal:** HTTP POST to hardcoded IP address (203.0.113.42) added in a module
with no prior network access.
**Why this is unusual:** No other file in /src/auth/ makes external calls.
The destination is not present anywhere else in the codebase.
**Suggested action:** Confirm with the PR author what this endpoint is and why
it's needed in this module.

### 🟡 MEDIUM — base64-decoded string passed to eval()
**File:** scripts/deploy.py · line 12
**Signal:** A base64 string is decoded and executed via exec().
**Why this is unusual:** This is a known obfuscation pattern. It may be
legitimate (some deployment scripts use this for config encoding).
**Suggested action:** Verify the decoded content and confirm it's intentional.

### 🟢 LOW — New subprocess call
**File:** tools/build.py · line 34
**Signal:** subprocess.run() added.
**Why this is low risk:** This file already contains 6 subprocess calls.
The new call follows the same pattern. Included for completeness.
```

---

### What Primpact explicitly does not do in v0.3

- It does not block merges automatically based on security signals
- It does not contact any external service with the code content (privacy)
- It does not claim to be a replacement for dedicated security tooling
- It does not scan the full codebase — only the diff and its immediate context

---

### Integration with existing security tooling

Primpact's SARIF output (from v0.2) means security signals can be imported into
GitHub Advanced Security, SonarQube, or similar without custom integration.

The recommended posture: run Primpact alongside, not instead of, semgrep or bandit.
They catch different things. Primpact's value is contextual; theirs is comprehensive.

---

**v0.3 success criterion:** A HIGH signal from Primpact, on a codebase the team
knows well, should prompt the reviewer to ask a question they would not have thought
to ask from reading the diff alone — at least 80% of the time.

---

## v0.4 — Depth

**Theme:** Improve analysis quality without changing the user-facing interface.

### AST-based import and symbol extraction

Replace regex import extraction with tree-sitter AST parsing. Impact:
- Correct handling of dynamic imports, re-exports, and barrel files
- Accurate symbol-level dependency tracking (not just file-level)
- Better classifier accuracy for interface vs internal changes
- Enables symbol-level blast radius: "this specific function is called in 7 places"

This is a significant internal change but the output format stays identical.
Users see better accuracy; they don't see the implementation change.

### Semantic equivalence detection

Use the AI layer to identify changes that look significant in the diff but are
semantically equivalent — refactors, renames, reformats. Flag these as low-risk
in the report so the reviewer's attention is not wasted on them.

Conversely, flag changes that look small in the diff but alter branching logic or
state transitions — the genuinely risky small changes that are easy to miss.

### Historical pattern learning

If Primpact has been run on a repo multiple times (via CI), accumulate a local
history of past reports. Use this to:
- Calibrate anomaly detection to the specific codebase's conventions
- Track blast radius growth over time (is this module accumulating dependents?)
- Identify files that repeatedly appear in blast radii (architectural hotspots)

Stored as a local SQLite database. No external service required.

---

## v1.0 — Platform

**Theme:** From CLI tool to team tool.

### Web UI

A local web server (`primpact serve`) that provides:
- A browsable history of all analysed PRs
- Side-by-side diff view with impact annotations inline
- Blast radius visualisation as an interactive dependency graph
- Filtering by anomaly severity, blast radius size, interface changes

The web UI reads from the SQLite history database introduced in v0.4.

### Team configuration

A `.primpact.yml` in the repo root that configures:
- Modules marked as high-sensitivity (triggers extra scrutiny in AI prompts)
- Known-safe patterns (suppress specific signal types in certain paths)
- Blast radius depth per module
- Anomaly severity thresholds for CI failure

### Primpact-as-a-service (optional, self-hosted)

A lightweight server that teams can self-host, receiving webhook events from
GitHub/GitLab and running analysis automatically. Results are posted back as PR
comments. No code leaves the team's infrastructure.

---

## v2.0 — Intelligence

**Theme:** Connect static analysis to runtime reality.

### Runtime telemetry integration

If the team provides traces or profiling data (OpenTelemetry, Datadog, etc.),
Primpact can cross-reference the static blast radius against actual call frequency:
- "This interface change affects 12 files statically, but only 3 of them call it
  in production"
- "This code path is on the hot path — 40,000 calls/minute in production"

This makes the blast radius actionable rather than theoretical.

### Roadmap / backlog integration

Connect to a linear, Jira, or GitHub Projects backlog. Given the upcoming stories,
Primpact can project change cost:
- "Story #47 will require modifications to 8 of the files touched by this PR"
- "This PR introduces a design that is architecturally incompatible with Story #52"

This is the capability described in the original design thinking — connecting
the current change to future intent.

### Continuous architecture monitoring

Move beyond per-PR analysis to continuous monitoring:
- Detect when the dependency graph is drifting from the intended architecture
- Alert when a module's blast radius grows beyond a configured threshold
- Track coupling trends over time (is the codebase becoming more or less modular?)

---

## Capability Matrix

| Capability | v0.1 | v0.2 | v0.3 | v0.4 | v1.0 | v2.0 |
|---|---|---|---|---|---|---|
| Blast radius (file level) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| AI decisions & assumptions | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Test gap analysis | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| GitHub/GitLab native | | ✓ | ✓ | ✓ | ✓ | ✓ |
| CI/CD integration | | ✓ | ✓ | ✓ | ✓ | ✓ |
| SARIF output | | ✓ | ✓ | ✓ | ✓ | ✓ |
| Malicious pattern signals | | | ✓ | ✓ | ✓ | ✓ |
| Dependency integrity checks | | | ✓ | ✓ | ✓ | ✓ |
| Contextual security scoring | | | ✓ | ✓ | ✓ | ✓ |
| AST-based analysis | | | | ✓ | ✓ | ✓ |
| Historical pattern learning | | | | ✓ | ✓ | ✓ |
| Web UI | | | | | ✓ | ✓ |
| Team configuration | | | | | ✓ | ✓ |
| Blast radius (symbol level) | | | | ✓ | ✓ | ✓ |
| Runtime telemetry | | | | | | ✓ |
| Roadmap integration | | | | | | ✓ |
| Architecture monitoring | | | | | | ✓ |

---

## What Primpact Is Not

Stated explicitly, to keep scope honest:

- **Not a linter.** Style, formatting, and code quality rules are other tools' jobs.
- **Not a SAST scanner.** Semgrep, CodeQL, and bandit do comprehensive vulnerability
  scanning. Primpact does contextual anomaly detection. They are complementary.
- **Not a test runner.** Primpact identifies test gaps; it doesn't run or generate tests.
- **Not a code review replacement.** It is a pre-review triage tool. The human still
  reviews. Primpact decides where they look and what questions they ask.
- **Not a guarantee.** Especially for malicious code detection. Primpact raises signals.
  It does not provide assurance.
