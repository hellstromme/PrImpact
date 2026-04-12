# PrImpact — Web UI Design Document

**Version:** 0.1  
**Status:** Draft  
**Last updated:** 2026-04-12  
**Design source:** `design/stitch_primpact_web_ui/`

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design System — The Kinetic Terminal](#2-design-system--the-kinetic-terminal)
   - 2.1 [Colour & Tonal Architecture](#21-colour--tonal-architecture)
   - 2.2 [Typography](#22-typography)
   - 2.3 [Elevation & Depth](#23-elevation--depth)
   - 2.4 [Components](#24-components)
3. [Navigation Model](#3-navigation-model)
4. [Screens](#4-screens)
   - 4.1 [Dashboard](#41-dashboard)
   - 4.2 [Impact Report — Summary](#42-impact-report--summary)
   - 4.3 [Blast Radius Analysis](#43-blast-radius-analysis)
   - 4.4 [Security Signals & Anomalies](#44-security-signals--anomalies)
   - 4.5 [Dependency Shift Analysis](#45-dependency-shift-analysis)
   - 4.6 [Test Gaps](#46-test-gaps)
   - 4.7 [Global Impact Trees](#47-global-impact-trees)
5. [Data Model Alignment](#5-data-model-alignment)
6. [Phasing](#6-phasing)

---

## 1. Overview

The PrImpact web UI is the v1.0 platform layer built on top of the existing CLI analysis pipeline. It translates the CLI's Markdown/JSON output into a persistent, navigable, team-facing interface.

The creative north star is **"The Kinetic Terminal"** — the browser treated as a high-performance IDE. The design bridges command-line efficiency with editorial clarity. Data feels etched into the interface rather than displayed on it.

Design assets are in `design/stitch_primpact_web_ui/`. Each screen folder contains:
- `screen.png` — visual mockup
- `code.html` — Stitch-generated HTML prototype

---

## 2. Design System — The Kinetic Terminal

### 2.1 Colour & Tonal Architecture

The palette is rooted in a "Deep Dark" philosophy: ocular comfort during long debugging sessions, with high-chroma accents signalling urgency.

**Surface hierarchy** — depth is a functional tool, not a decorative one:

| Level | Token | Hex | Usage |
|---|---|---|---|
| 0 | `surface` | `#10141a` | Primary application background |
| 1 | `surface_container_low` | `#181c22` | Section backgrounds |
| 2 | `surface_container` | `#1e232a` | Main content areas |
| 3 | `surface_container_high` | `#252b33` | Hover / focused cards |
| 4 | `surface_container_highest` | `#2d3440` | Glassmorphism overlays |

**Accent colours:**

| Token | Hex | Usage |
|---|---|---|
| `primary` | `#6fdd78` | Primary actions, CLEAN status, active nav |
| `primary_container` | `#34a547` | Button gradient end, chip backgrounds |
| `on_primary_fixed` | `#0a1f0c` | Text on primary/green surfaces |
| `secondary` | `#f0b429` | Amber — medium severity, warnings |
| `tertiary` | `#e05c5c` | Red — critical severity, blockers |
| `on_surface` | `#dfe2eb` | All body text (never pure `#ffffff`) |
| `outline_variant` | `#3e4a3d` | Ghost borders at 15% opacity |

**Rules:**
- **No 1px solid borders.** Sections are separated by surface value shifts, not drawn lines.
- **Ghost borders** (`outline_variant` at 15% opacity) are the only permitted outline — for high-density containers only.
- **No pure white.** All "white" text uses `on_surface` (`#dfe2eb`).
- **Glassmorphism** for floating overlays: `surface_container_highest` at 80% opacity with `backdrop-blur: 12px`.
- **Gradient buttons:** `primary` → `primary_container` linear gradient for a "machined" finish.
- **Noise texture:** 2% opacity noise over `surface_container_lowest` to simulate phosphor grain on deep background areas.

### 2.2 Typography

Dual-axis approach separating human intent from machine output:

| Role | Typeface | Usage |
|---|---|---|
| Display / Impact Scores | Space Grotesk | Large numbers, hero metrics |
| Titles & Body | Inter | UI labels, prose, explanatory text |
| System Data | JetBrains Mono / Fira Code | SHAs, file paths, code snippets, versions, timestamps |

**Scale:**
- `label-sm`: 0.6875rem — metadata in dense tables (last commit, author, churn)
- `label-md`: 0.8125rem — secondary metadata
- `body`: 0.9375rem — body prose
- `title`: 1.125rem — section headings
- `display`: 2rem+ — dashboard hero metrics

The monospace font is a **semantic signal**, not just aesthetic: any string that is "system-generated data" (not human-written prose) must use it.

### 2.3 Elevation & Depth

Elevation is achieved through tonal shifts, not drop shadows. Shadows are reserved for modal dialogs only.

- **Lifting a card:** transition background to `surface_container_highest` — "glow from within"
- **Modal shadow:** `box-shadow: 0 20px 40px rgba(10, 14, 20, 0.5)` — diffuse, derived from `surface_container_lowest`
- **No standard drop shadows** on interactive elements

### 2.4 Components

**Buttons**
- *Primary:* `primary` → `primary_container` gradient background. Text in `on_primary_fixed`. Border-radius `0.25rem`.
- *Tertiary:* No background. Text in `primary`. On hover: `surface_container_high` background.

**Status Chips**
- *CLEAN:* `primary_container` background, `on_primary_fixed` text
- *BLOCKER:* `tertiary_container` background, 1px ghost border of `tertiary`
- *Severity (HIGH/MEDIUM/LOW):* `tertiary` / `secondary` / `primary_container` respectively
- Chips are strictly **rectangular** (`0.1875rem` radius) — the "Hacker Sleek" constraint. No pill shapes.

**Cards & Impact Lists**
- No horizontal rules. Items separated by 12px vertical space and alternating `surface` / `surface_container_low` backgrounds.
- `label-md` for metadata to maximise data density above the fold.

**Input Fields**
- Underline-only style, or `surface_container_highest` fill.
- Focus state: 1px `primary` glow, no offset ring.

**Signature Component — The Impact Sparkline**
- A micro bar-chart placed inline within list and table rows.
- Gradient from `secondary` (amber) to `tertiary` (red) to visualise blast radius across churn history.
- Used in: Blast Radius file table, Dependency Shift table, Test Gaps coverage panel.

---

## 3. Navigation Model

The application uses a **single persistent left sidebar** for all navigation. There is no top navigation bar.

**Sidebar structure:**

```
[Logo / Brand]
[Agent / Analysis context]

  + NEW ANALYSIS          ← primary CTA

  SUMMARY
  BLAST RADIUS
  SECURITY
  DEPENDENCIES
  TEST GAPS

  ───────────────
  IMPACT TREES    ← global view (cross-analysis)
  HISTORY         ← run history

  ───────────────
  DOCUMENTATION
  SUPPORT
```

- The active section is highlighted with a `primary` left-border accent and `surface_container_high` background.
- The sidebar collapses to icons on narrow viewports.
- **Impact Trees** and **History** are global views not tied to a single analysis run; they sit below the per-analysis sections.
- The Dashboard (unauthenticated/landing state) uses the same sidebar shell with the per-analysis section items hidden until an analysis is loaded.

---

## 4. Screens

### 4.1 Dashboard

**File:** `design/stitch_primpact_web_ui/primpact_dashboard/`

The landing screen. Shown when no analysis is active.

**Key elements:**
- Hero headline: "Analyze the impact of your **code changes**" — with `primary` green accent on the second phrase
- Subheadline: "Instantly visualize blast radius, security regressions, and dependency shifts across your entire repository."
- **Analysis input form:** two fields (repository path / PR number, or base/head SHAs) with a "Run Analysis" primary button
- **Stats row:** Active Analyses · Avg Blast Radius · Blockers Resolved — persisted from history
- **Recent Reports list:** shows last N analyses with status chip (CLEAN/BLOCKER), PR title, author, timestamp, and a blast radius sparkline
- "View Archive" link to History

**Notes:**
- The "How it works" section (AST Diffing, Dependency Resolution) and the Dynamic Impact Trees feature panel are placeholder/marketing content. Replace with real content or remove for v1.0.

---

### 4.2 Impact Report — Summary

**File:** `design/stitch_primpact_web_ui/primpact_impact_report_22/`

The top-level view for a single analysis run. This is the first screen shown after an analysis completes.

**Key elements:**
- **Header:** PR number + commit range + branch, with Agent Verdict chip (CLEAN / BLOCKER) and Confidence Score (`98.4%`) prominently placed top-right
- **Executive Summary:** AI-generated prose block (`AIAnalysis.summary`), with inline highlights for key changed symbols (monospace)
- **Signal pills:** No Breaking Changes · +12% Throughput — derived from `AIAnalysis.decisions`
- **Security Signals summary:** three counts (Info / Medium / Low) with "View Security Details" link
- **Blast Radius summary:** total file count + module count, with a short file table (path, distance chip, uses count, churn sparkline)
- **Interface Changes:** side-by-side before/after code block showing changed public signatures (`InterfaceChange`)
- **Decisions & Assumptions cards:** grid of `AIAnalysis.decisions` and `AIAnalysis.assumptions` — each card shows the decision, risk level chip, and rationale

**Data sources:** `ImpactReport`, `AIAnalysis`, `Verdict`, `InterfaceChange`, `BlastRadiusEntry`

---

### 4.3 Blast Radius Analysis

**File:** `design/stitch_primpact_web_ui/primpact_blast_radius_analysis/`

Detailed view of the dependency propagation from changed files.

**Key elements:**
- **Visualisation panel (centre):** node graph showing direct and transitive dependents. Nodes sized by usage count. Labelled with direct file count. Toggle: DOWNSTREAM / MODULES.
- **Impacted Modules panel (right):** list of affected modules with severity chip — maps to grouped `BlastRadiusEntry` records
- **Interface Breaking Change alert:** prominent warning panel showing the before/after function signature diff when `InterfaceChange` records are present. Includes a "Review Diff" link.
- **Metrics row:** Max Propagation (hops) · Downstream Risk · API Surface Delta
- **File Impact Profile table (below):** full list of affected files with columns: file path · distance chip · uses count · churn sparkline · open action

**Notes:**
- The node graph is a supporting visual — the file table is the primary analytical surface. Implement the table first; the graph is a v1.1 enhancement.
- Do not show "KINETIC_TERMINAL" in the application header — this was a design artefact in the mockup.

**Data sources:** `BlastRadiusEntry`, `InterfaceChange`

---

### 4.4 Security Signals & Anomalies

**File:** `design/stitch_primpact_web_ui/primpact_security_anomalies/`

Lists all `SecuritySignal` and `DependencyIssue` records for the run.

**Key elements:**
- **Filter bar:** Severity (All / High / Medium / Low) · Type (All Signals / Security / Dependency) · search input · anomaly count badge
- **Signal list (left):** each item shows severity chip, signal title, file path + line number (`SecuritySignal.file_path`, `.line_number`), and a one-line description. Selected item highlighted.
- **Signal detail panel (right):**
  - Title, severity chip, signal identifier
  - Impact category + detection source (`SecuritySignal.signal_type`)
  - False-positive rate
  - **Code Evidence block:** syntax-highlighted snippet around the offending line, with the problematic value highlighted in `tertiary`
  - **Analysis Reasoning:** AI-generated explanation (`SecuritySignal.why_unusual`)
  - **Suggested Action:** numbered remediation steps (`SecuritySignal.suggested_action`)
  - Actions: "Mute Signal" (suppress) · "Assign Reviewer" (team collaboration — v1.0 platform feature)
- **Scan Health indicator:** top-right, shows Active / Degraded

**Data sources:** `SecuritySignal`, `DependencyIssue`

---

### 4.5 Dependency Shift Analysis

**File:** `design/stitch_primpact_web_ui/primpact_dependency_shift_analysis/`

Shows what changed in the dependency manifest (new packages, version bumps, removals).

**Key elements:**
- **Summary stats header:** Added · Removed · Updated counts
- **Graph Shift Topology:** before/after node graph showing the dependency tree before and after the PR. Current (new) nodes highlighted in `primary`.
- **Risk Profile panel (right):** Typosquatting Risk · Supply Chain Changes · Source Transparency — each with a severity bar
- **Verified Sources badge:** green check for packages verified against known registries
- **Dependency Detail View table:** each row shows package name · status chip (ADDED/UPDATED/REMOVED) · version (old → new in monospace) · license · impact sparkline · churn indicator · expand action

**Notes:**
- License column is not in the current `DependencyIssue` model. Add a `license` field to `DependencyIssue` or fetch it at analysis time from the package registry.

**Data sources:** `DependencyIssue`

---

### 4.6 Test Gaps

**File:** `design/stitch_primpact_web_ui/primpact_test_gaps/`

Surfaces AI-identified behaviours that lack test coverage in the PR.

**Key elements:**
- **Header stats:** Coverage Debt (behaviour count) · Risk Factor chip (CRITICAL / HIGH / MEDIUM)
- **Priority filter tabs:** ALL · HIGHRISE · MEDIUM
- **Coverage Sparkling chart (right):** bar chart showing path coverage across modules
- **Gap cards:** two-column grid. Each card shows:
  - Severity icon + title
  - Prose description of the untested behaviour
  - Affected file paths (monospace)
  - Impact tags (e.g., SECURITY\_CRITICAL, FUNCTIONAL\_GAP)
  - Priority chip · test type label
  - "Mark as Addressed" toggle
- **Logical Branches (No Coverage) list:** table of specific code branches with file + line reference and branch condition description. Rows are checkable.
- **Footer stats:** Total Gaps · Resolved · New Hazards

**Notes:**
- `AIAnalysis.test_gaps` is currently a free-text list. To support this screen a structured `TestGap` model is needed (fields: title, description, affected_files, severity, gap_type, branch_condition).

**Data sources:** `AIAnalysis.test_gaps` (requires model enhancement — see [Section 5](#5-data-model-alignment))

---

### 4.7 Global Impact Trees

**File:** `design/stitch_primpact_web_ui/primpact_global_impact_trees/`

A persistent, cross-analysis architectural view of the codebase dependency graph. Not tied to a single PR.

**Key elements:**
- **Breadcrumb:** root > services > [selected node]
- **View toggle:** TREE · GRAPH
- **Force-directed node graph:** nodes represent modules/services. Node colour indicates health (green = stable, amber = warning, red = critical). Node size scales with coupling factor.
- **Node detail panel (right):** appears on node selection. Shows node name, path, complexity score, dependency/dependent counts, recent activity (last 2 commits affecting the node).
- **Zoom controls:** + / − / fit-to-screen
- **Bottom metrics bar:** Architectural Debt · Coupling Factor · Language Split · Impact Efficiency

**Notes:**
- This screen requires **persistent cross-PR data** — it is not producible from a single analysis run. It implies a historical database of analysis results aggregated over time.
- The bottom metrics (Architectural Debt, Coupling Factor, Impact Efficiency) are not in any current data model and require new computation.
- **This screen is out of scope for v1.0.** Target v1.1.

**Data sources:** New — requires persistent architectural graph store

---

## 5. Data Model Alignment

| Screen | Current models used | Gap / required change |
|---|---|---|
| Dashboard | `ImpactReport` (via history) | History API needed to serve recent runs list |
| Impact Report | `ImpactReport`, `AIAnalysis`, `Verdict`, `InterfaceChange`, `BlastRadiusEntry` | None — best existing alignment |
| Blast Radius | `BlastRadiusEntry`, `InterfaceChange` | None significant |
| Security | `SecuritySignal`, `DependencyIssue` | "Mute" and "Assign Reviewer" require a new persistence layer |
| Dependencies | `DependencyIssue` | Add `license: str` field to `DependencyIssue` |
| Test Gaps | `AIAnalysis.test_gaps` | `test_gaps` is currently `list[str]`. Replace with `list[TestGap]` — a new structured dataclass with `title`, `description`, `affected_files`, `severity`, `gap_type` fields |
| Impact Trees | None | New feature — requires cross-PR architectural graph store. Out of scope for v1.0. |

---

## 6. Phasing

### v1.0 — Core Web UI

Implement all screens except Global Impact Trees:

- Dashboard with run history list
- Impact Report (Summary) — the primary output screen
- Blast Radius (table view; skip node graph)
- Security Signals & Anomalies
- Dependency Shift Analysis
- Test Gaps (requires `TestGap` model enhancement)
- Sidebar navigation shell

Backend work required:
- REST API (or server-rendered) layer to serve `ImpactReport` data from SQLite history
- `TestGap` structured dataclass replacing `list[str]` in `AIAnalysis`
- `license` field on `DependencyIssue`

### v1.1 — Visualisations & Architecture

- Blast Radius node graph
- Global Impact Trees (requires cross-PR architectural graph computation)
- Coverage Sparkline chart with historical data
- Dependency Graph Shift Topology animation

### Future

- "Mute Signal" and "Assign Reviewer" — team collaboration persistence layer
- Notifications / webhooks
- Role-based access control
