# PrImpact v1.0 — Implementation Plan

**Version:** 1.0  
**Status:** Draft  
**Last updated:** 2026-04-12  
**Theme:** From CLI tool to team tool

---

## Table of Contents

1. [Scope](#1-scope)
2. [Architecture](#2-architecture)
3. [Delivery Milestones](#3-delivery-milestones)
4. [Milestone 1 — API Layer](#4-milestone-1--api-layer)
5. [Milestone 2 — Web Server & Shell](#5-milestone-2--web-server--shell)
6. [Milestone 3 — Core Screens](#6-milestone-3--core-screens)
7. [Milestone 4 — Secondary Screens](#7-milestone-4--secondary-screens)
8. [Milestone 5 — Team Configuration](#8-milestone-5--team-configuration)
9. [Milestone 6 — Primpact-as-a-Service](#9-milestone-6--primpact-as-a-service)
10. [Technical Decisions](#10-technical-decisions)
11. [What v1.0 Does Not Include](#11-what-v10-does-not-include)

---

## 1. Scope

v1.0 turns PrImpact from a single-developer CLI tool into a team-facing platform. Three capabilities are added:

1. **Web UI** — a local web server (`primpact serve`) that makes analysis results browsable, navigable, and shareable without reading Markdown files.
2. **Team configuration** — a `.primpact.yml` file in the repo root that lets teams configure sensitivity thresholds, suppress known-safe patterns, and set blast radius depth per module.
3. **Primpact-as-a-Service** — an optional self-hosted server that receives GitHub/GitLab webhook events and runs analysis automatically, posting results as PR comments.

The guiding constraint from the roadmap applies: **no instrumentation tax**. The web UI reads from the existing SQLite history database introduced in v0.4. No new schema is required to view past results. New runs automatically appear.

### In scope for v1.0

- `primpact serve` command launching a local web server
- Dashboard screen (run history, quick-launch form)
- Impact Report screen (Summary, Blast Radius, Security, Dependencies, Test Gaps tabs)
- `.primpact.yml` team configuration file + loader
- Webhook server mode (`primpact server`) for GitHub/GitLab integration

### Deferred to v1.1

- Global Impact Trees screen (requires cross-PR architectural graph — no current data source)
- Blast radius interactive node graph (table view ships in v1.0; graph is v1.1)
- "Mute Signal" / "Assign Reviewer" persistence (requires team auth layer)
- Coverage Sparkline chart with historical trend data

---

## 2. Architecture

### New components

```
primpact/
  web/
    server.py          # FastAPI app — route definitions, lifespan, CORS
    api/
      runs.py          # GET /api/runs, GET /api/runs/{id}
      report.py        # GET /api/runs/{id}/report  (full ImpactReport as JSON)
      config.py        # GET/POST /api/config
    static/            # Built frontend assets (JS bundle, CSS, fonts)
    templates/
      index.html       # Single-page app shell (served for all non-API routes)
  config_file.py       # .primpact.yml loader — called by cli.py only
  webhook.py           # GitHub/GitLab webhook handler — called by server.py only
```

```
frontend/              # Source for the React frontend (built to web/static/)
  src/
    components/        # Reusable UI components (chips, sparklines, code blocks)
    screens/           # One file per screen
    lib/               # API client, data formatters
  package.json
  vite.config.ts
```

### Data flow

```
CLI run  ──→  history.py (SQLite)  ──→  web/api/runs.py  ──→  Frontend
                                   ──→  web/api/report.py ──→  Frontend
```

The web server does not run the analysis pipeline. It reads completed runs from the history database. Running a new analysis from the web UI dashboard triggers a `primpact analyse` subprocess; its output is saved to the history DB and immediately queryable.

### History database additions

The existing `history.py` module stores aggregate data per run (hotspots, anomaly patterns). The web UI needs the **full `ImpactReport`** to be stored per run. A new table `runs` is added:

```sql
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,   -- UUID generated at run time
    repo_path   TEXT NOT NULL,
    pr_number   INTEGER,
    base_sha    TEXT NOT NULL,
    head_sha    TEXT NOT NULL,
    pr_title    TEXT,
    created_at  TEXT NOT NULL,      -- ISO 8601
    report_json TEXT NOT NULL       -- full ImpactReport serialised to JSON
);
```

`history.py` gets two new functions:
- `save_run(run_id, report, repo_path, pr_number)` — serialises and stores a run
- `load_runs(repo_path, limit)` → `list[RunSummary]` — returns run list for the dashboard
- `load_run(run_id)` → `ImpactReport | None` — rehydrates a single run

These are additive and do not change the existing `save_run()` / `load_hotspots()` / `load_anomaly_patterns()` API.

---

## 3. Delivery Milestones

| # | Milestone | Deliverable | Key output |
|---|---|---|---|
| 1 | API Layer | History DB extension + REST API | `GET /api/runs`, `GET /api/runs/{id}/report` |
| 2 | Web Server & Shell | `primpact serve`, SPA shell, sidebar nav | App loads, sidebar renders |
| 3 | Core Screens | Dashboard + Impact Report (all tabs) | Primary user journey complete |
| 4 | Secondary Screens | Dependency Shift, Test Gaps, Security detail | All v1.0 screens complete |
| 5 | Team Configuration | `.primpact.yml` loader + UI settings page | Config file honoured by pipeline |
| 6 | Primpact-as-a-Service | Webhook server, auto-post PR comments | GitHub/GitLab zero-config integration |

Each milestone is independently shippable. Milestones 1–4 deliver the web UI. Milestones 5–6 deliver the team features.

---

## 4. Milestone 1 — API Layer

**Goal:** Persist full reports to SQLite and expose them via a REST API.

### 4.1 History database — `history.py`

Add the `runs` table and three new functions. Existing functions and schema are untouched. Migration is additive: a `CREATE TABLE IF NOT EXISTS` on startup is sufficient.

New `RunSummary` dataclass (in `models.py`):

```python
@dataclass
class RunSummary:
    id: str
    repo_path: str
    pr_number: int | None
    pr_title: str | None
    base_sha: str
    head_sha: str
    created_at: str        # ISO 8601
    verdict: str | None    # "clean" | "has_blockers" | None
    blast_radius_count: int
    anomaly_count: int
    signal_count: int
```

Serialisation: `ImpactReport` → JSON uses `dataclasses.asdict()`. Deserialisation rehydrates via the existing `render_json()` output structure (already tested). A new `report_from_dict()` function in `reporter.py` performs the inverse.

### 4.2 CLI integration — `cli.py`

After step 8 (reporter), if history is active, call `save_run()` with the full report. This is inside the existing history try/except wrapper so failures are silent.

A `--run-id` flag is added (auto-generated UUID if not provided) so the web UI can link directly to a run that was triggered from the CLI.

### 4.3 REST API — `web/api/`

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/runs` | List runs for a repo. Query params: `repo` (path), `limit` (default 50), `offset` |
| `GET` | `/api/runs/{id}` | Single `RunSummary` |
| `GET` | `/api/runs/{id}/report` | Full `ImpactReport` JSON |
| `POST` | `/api/analyse` | Trigger a new analysis (spawns `primpact analyse` subprocess, returns run ID immediately) |
| `GET` | `/api/analyse/{run_id}/status` | Polling endpoint — `pending` / `complete` / `failed` |

All responses are JSON. Errors follow `{"error": "message"}` shape.

### 4.4 Tests

- `tests/test_history_runs.py` — `save_run` / `load_runs` / `load_run` round-trip
- `tests/test_api_runs.py` — FastAPI TestClient covering list, single run, and 404

---

## 5. Milestone 2 — Web Server & Shell

**Goal:** `primpact serve` starts a local server; the browser loads the app shell with sidebar navigation.

### 5.1 CLI command — `cli.py`

```bash
primpact serve [--port 8080] [--host localhost] [--open]
```

- Starts the FastAPI server via `uvicorn`
- `--open` opens the browser automatically
- Prints the URL to stderr on startup

### 5.2 Frontend stack

| Choice | Rationale |
|---|---|
| **React** | Component model suits the Kinetic Terminal design system; large ecosystem for graph libs needed in v1.1 |
| **Vite** | Fast build, HMR for development, simple config |
| **TypeScript** | Type safety across API client and component props |
| **TanStack Query** | Data fetching, caching, and background refetch for the run list |
| **No CSS framework** | The Kinetic Terminal design system is bespoke; a utility framework would require extensive overrides. CSS custom properties map directly to the design token table. |

### 5.3 Design token implementation

The colour table from `docs/primpact-web-ui-design.md` maps directly to CSS custom properties:

```css
:root {
  --surface:                   #10141a;
  --surface-container-low:     #181c22;
  --surface-container:         #1e232a;
  --surface-container-high:    #252b33;
  --surface-container-highest: #2d3440;
  --primary:                   #6fdd78;
  --primary-container:         #34a547;
  --on-primary-fixed:          #0a1f0c;
  --secondary:                 #f0b429;
  --tertiary:                  #e05c5c;
  --on-surface:                #dfe2eb;
  --outline-variant:           #3e4a3d;
}
```

Typography loaded via Google Fonts: Space Grotesk, Inter, JetBrains Mono.

### 5.4 App shell

Single HTML entry point (`web/templates/index.html`) served for all non-API routes. The React app renders the sidebar and a content outlet. React Router handles client-side navigation.

Sidebar structure matches `docs/primpact-web-ui-design.md` Section 3 exactly. The per-analysis section items (Summary, Blast Radius, Security, Dependencies, Test Gaps) are visible only when a run is active. Impact Trees is hidden (v1.1).

### 5.5 Build integration

`primpact serve` checks whether `web/static/` contains a built bundle. If not (development mode), it starts the Vite dev server in parallel and proxies frontend requests. The production build (`npm run build`) outputs to `web/static/` and is committed to the repo so users don't need Node installed to run the server.

---

## 6. Milestone 3 — Core Screens

**Goal:** Dashboard and Impact Report (all five tabs) are fully functional.

### 6.1 Dashboard screen

**Route:** `/`

**Components:**
- `HeroForm` — repo path + PR number inputs, "Run Analysis" button. On submit: calls `POST /api/analyse`, polls `/api/analyse/{id}/status`, redirects to `/runs/{id}` on completion.
- `StatsRow` — "Active Analyses" (runs in last 24h), "Avg Blast Radius" (mean blast_radius_count across recent runs), "Blockers Resolved" (runs that moved from `has_blockers` to `clean`). Computed client-side from the runs list.
- `RecentRunsList` — calls `GET /api/runs`, renders each as a row: status chip (CLEAN/BLOCKER), PR title, author, relative timestamp, blast radius sparkline. Clicking a row navigates to `/runs/{id}`.

### 6.2 Impact Report screen

**Route:** `/runs/:id`

**Data source:** `GET /api/runs/{id}/report` — full `ImpactReport` JSON, fetched once and cached by TanStack Query.

The sidebar activates the per-analysis section. Five tabs render in the content area:

#### Tab 1 — Summary

Maps directly to `primpact_impact_report_22` design:

| UI element | Data source |
|---|---|
| PR title + commit range | `ImpactReport.pr_title`, `.base_sha`, `.head_sha` |
| Agent Verdict chip | `ImpactReport.ai_analysis` → verdict (computed from `Verdict.status`) |
| Confidence score | `Verdict.rationale` (parse percentage if present, else omit) |
| Executive Summary | `AIAnalysis.summary` |
| Security Signals counts | Count `SecuritySignal` by severity; count `DependencyIssue` by severity |
| Blast Radius summary | `len(blast_radius)`, distinct modules grouped by path prefix |
| Interface Changes | `InterfaceChange[]` — before/after code blocks |
| Decisions cards | `AIAnalysis.decisions` — description, rationale, risk chip |
| Assumptions cards | `AIAnalysis.assumptions` — description, location, risk chip |

#### Tab 2 — Blast Radius

Maps to `primpact_blast_radius_analysis` design:

| UI element | Data source |
|---|---|
| File count / module count | `len(blast_radius)` / grouped by path prefix |
| Interface Breaking Change alert | `InterfaceChange[]` — shown when non-empty |
| Max propagation | `max(entry.distance for entry in blast_radius)` |
| File Impact Profile table | `BlastRadiusEntry[]` — path, distance chip, imported_symbols count, churn sparkline |

The node graph visualisation is **omitted in v1.0**. The table is the primary surface.

#### Tab 3 — Security

Maps to `primpact_security_anomalies` design:

| UI element | Data source |
|---|---|
| Signal list | `AIAnalysis.security_signals` + `ImpactReport.dependency_issues` |
| Severity filter | Client-side filter on `signal.severity` |
| Signal detail panel | Selected `SecuritySignal` — description, `why_unusual`, `suggested_action`, `location` |
| Code evidence block | `location.file` + `location.line` — fetches a snippet via `GET /api/runs/{id}/snippet?file=&line=` |

"Mute Signal" and "Assign Reviewer" buttons render but are disabled with a tooltip ("Coming in a future release") — the design is preserved without requiring a persistence layer.

#### Tab 4 — Dependencies

Maps to `primpact_dependency_shift_analysis` design:

| UI element | Data source |
|---|---|
| Added / Removed / Updated counts | `DependencyIssue.issue_type` grouping |
| Dependency Detail View table | `DependencyIssue[]` — package_name, issue_type chip, description, severity, license |
| Risk Profile | Counts by severity (typosquat = high, version_change = low, vulnerability = high) |

Graph Shift Topology is **omitted in v1.0**.

#### Tab 5 — Test Gaps

Maps to `primpact_test_gaps` design:

| UI element | Data source |
|---|---|
| Coverage Debt count | `len(AIAnalysis.test_gaps)` |
| Risk Factor chip | `max(gap.severity for gap in test_gaps)` |
| Priority filter | Client-side filter on `gap.severity` |
| Gap cards | `TestGap[]` — behaviour, location, severity chip, gap_type tag |
| Footer stats | Total gaps / resolved (gaps marked addressed — client-side state only in v1.0) / new (always 0 in v1.0) |

---

## 7. Milestone 4 — Secondary Screens

**Goal:** Security detail, Dependency detail, and Test Gaps screens are full-page routes (not just tabs), enabling deep-link sharing.

Each tab from Milestone 3 gets its own route:

| Route | Screen |
|---|---|
| `/runs/:id/summary` | Impact Report Summary |
| `/runs/:id/blast-radius` | Blast Radius Analysis |
| `/runs/:id/security` | Security Signals & Anomalies |
| `/runs/:id/dependencies` | Dependency Shift Analysis |
| `/runs/:id/test-gaps` | Test Gaps |

`/runs/:id` redirects to `/runs/:id/summary`.

The sidebar nav highlights the correct item based on the active route. Sharing a URL drops the recipient directly into the right tab.

**Additional endpoint for Security tab:**

```
GET /api/runs/{id}/snippet?file={path}&line={n}&context=5
```

Returns the 5 lines around `line` from the file as it existed at `head_sha`, fetched from git. Used to populate the code evidence block in the Security detail panel.

---

## 8. Milestone 5 — Team Configuration

**Goal:** Teams can configure PrImpact behaviour via a `.primpact.yml` file in the repo root.

### 8.1 Configuration schema

```yaml
# .primpact.yml

# Modules that trigger extra AI scrutiny when changed
high_sensitivity_modules:
  - src/auth/
  - src/payments/
  - core/crypto/

# Suppress specific signal types in specific paths
suppressed_signals:
  - signal_type: shell_invoke
    path_prefix: tools/
    reason: "Build tools intentionally use subprocess"

# Per-module blast radius depth (overrides the global default of 3)
blast_radius_depth:
  src/utils/: 2       # utils are so widely imported that depth-3 is noise
  src/auth/: 4        # auth changes warrant deeper tracing

# CI failure threshold (overrides --fail-on-severity flag)
fail_on_severity: high

# Anomaly severity thresholds
anomaly_thresholds:
  interface_change: medium
  new_network_call: high
```

### 8.2 Implementation

New module: `pr_impact/config_file.py`

- `load_config_file(repo_path: str) -> PrImpactConfig | None` — reads `.primpact.yml` from repo root. Returns `None` if absent. Called by `cli.py` only.
- `PrImpactConfig` dataclass with the schema above.

The config is passed into the pipeline where relevant:
- `blast_radius_depth` per-module overrides the `max_depth` parameter in `dependency_graph.get_blast_radius()`
- `suppressed_signals` is applied in `security.detect_pattern_signals()` as a post-filter
- `high_sensitivity_modules` is injected into the AI prompts as additional context
- `fail_on_severity` overrides the CLI flag (CLI flag takes precedence if explicitly provided)

### 8.3 Web UI settings page

**Route:** `/settings`

Sidebar item: "Settings" (below Support).

A read-only view of the active `.primpact.yml` for the current repo. No in-browser editing — the file lives in the repo and is edited there. The page displays:
- Which file was loaded (path)
- Each config section rendered as a summary table
- A "no config file found" state with a copyable starter template

---

## 9. Milestone 6 — Primpact-as-a-Service

**Goal:** Teams can self-host a server that automatically analyses PRs and posts results as comments.

### 9.1 `primpact server` command

```bash
primpact server [--port 8080] [--host 0.0.0.0] [--repos /path/to/repos]
```

Starts the same FastAPI server as `primpact serve` plus:
- Webhook endpoint: `POST /webhook/github` and `POST /webhook/gitlab`
- A background job queue (simple `asyncio.Queue`) for incoming analysis requests
- Worker task that processes jobs sequentially per repo

### 9.2 GitHub webhook integration

On `pull_request` events (opened, synchronize, reopened):
1. Clone/fetch the repo if not present under `--repos`
2. Run `primpact analyse` as a subprocess
3. On completion, post the Markdown report as a PR comment via the GitHub API (upsert — one comment per PR, updated on re-run)
4. If `fail_on_severity` is configured and met, post a failing commit status

Required secrets (configured as env vars or in a `primpact-server.yml`):
- `GITHUB_TOKEN` — for posting comments and statuses
- `WEBHOOK_SECRET` — for validating GitHub webhook payloads (HMAC-SHA256)

### 9.3 GitLab webhook integration

Same pattern using GitLab's Merge Request webhook (`merge_request` events) and the GitLab Notes API for posting comments.

### 9.4 Security

- Webhook payloads are validated with HMAC-SHA256 before processing
- Repos are cloned into an isolated directory; no symlinks are followed
- The server never executes code from the repo — it only reads file content

---

## 10. Technical Decisions

### Python version

Minimum Python 3.11 (already required by the CLI). No change.

### Web framework

**FastAPI** — already familiar from the Python ecosystem, async-native, automatic OpenAPI docs at `/api/docs` which helps during development. Served via `uvicorn`.

### Frontend framework

**React 18** with **Vite** and **TypeScript**. The Kinetic Terminal design system is fully custom CSS (no component library), so framework choice is driven by ecosystem maturity for the graph visualisation needed in v1.1 (likely D3 or React Flow).

### Frontend build

The built bundle (`web/static/`) is committed to the repository. This means users installing via `pip install primpact` get a working web UI without needing Node.js. The bundle is rebuilt and committed as part of the release process.

### State management

TanStack Query for server state (run list, report data). No global client state library — React context is sufficient for the active run ID and sidebar state.

### Test strategy

| Layer | Tool |
|---|---|
| API routes | FastAPI `TestClient` (sync, in-process) |
| History DB | Pytest with a tmp-path SQLite fixture |
| Frontend | Vitest + React Testing Library for component tests |
| End-to-end | Playwright — Dashboard → run analysis → Impact Report navigation |

Playwright tests run against a test server with a seeded history database. No live Claude API calls in tests — the seeded DB contains pre-computed `ImpactReport` JSON.

### Packaging

New optional dependency group: `primpact[web]` installs `fastapi`, `uvicorn`, and `websockets`. The base `primpact` install remains CLI-only.

```toml
[project.optional-dependencies]
web = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "websockets>=12"]
```

`primpact serve` raises a clear error with install instructions if FastAPI is not present.

---

## 11. What v1.0 Does Not Include

| Feature | Why deferred |
|---|---|
| Global Impact Trees screen | Requires cross-PR architectural graph with no current data source |
| Blast radius interactive node graph | Table view sufficient for v1.0; graph library selection (D3 / React Flow) is a v1.1 decision |
| "Mute Signal" / "Assign Reviewer" | Requires team identity and a persistence layer for suppressions |
| Coverage Sparkline with historical trend | Requires aggregating test gap data across runs — not yet stored |
| Authentication / multi-user | v1.0 is single-user local or single-team self-hosted; auth is v2.0 |
| Runtime telemetry | v2.0 |
| Roadmap integration | v2.0 |
