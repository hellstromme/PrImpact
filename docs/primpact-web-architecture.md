# PrImpact Web Architecture Design

**Version:** 1.0  
**Covers:** Milestones 2–5 (Web Server & Shell, Core Screens, Secondary Screens, Settings)  
**Stack:** React 18 + Vite + TypeScript + TanStack Query + SCSS Modules + CSS Custom Properties  
**Last updated:** 2026-04-13

---

## 1. Overview

The PrImpact frontend is a single-page application built with React 18, Vite, and TypeScript, served as a static bundle out of `pr_impact/web/static/` by the existing FastAPI server. The application presents a persistent left sidebar shell — always visible — containing global navigation and a context-aware per-analysis section that activates when a run is loaded. State is managed exclusively through TanStack Query for all server data (run list, report JSON) and two React contexts for lightweight app-level coordination (active run ID, sidebar collapse state). There is no Redux, Zustand, or any other client state library.

Styling uses **SCSS Modules** (`.module.scss`) with CSS custom properties. SCSS handles authoring concerns — nesting, mixins, token maps, and `@each` loops for variant generation — while CSS custom properties carry the design tokens at runtime so they remain inspectable and overrideable in DevTools. There is no utility framework and no CSS-in-JS.

The built bundle is committed to `pr_impact/web/static/` so that users installing via `pip install primpact[web]` get a working UI without requiring Node.js. During development, Vite runs on port 5173 and proxies all `/api/*` requests to FastAPI on port 8080, providing full HMR while hitting real backend data.

---

## 2. Frontend Folder Structure

```
frontend/
├── index.html                    # Vite entry — <div id="root">, font preloads
├── vite.config.ts
├── tsconfig.json
├── package.json
│
└── src/
    │
    ├── main.tsx                  # React.createRoot, QueryClientProvider, RouterProvider
    ├── router.tsx                # All route definitions (React Router v6)
    │
    ├── styles/
    │   ├── _tokens.scss          # SCSS variable map + CSS custom property export
    │   ├── _mixins.scss          # Reusable patterns: ghost-border, elevation, chip-variant
    │   ├── _reset.scss           # Minimal reset (box-sizing, margin removal)
    │   ├── _typography.scss      # Font-face declarations, .font-display/.font-mono/.font-ui
    │   └── global.scss           # Body background, ::selection, scrollbar, phosphor grain
    │
    ├── lib/
    │   ├── api.ts                # Typed fetch wrapper — all API calls live here
    │   ├── queryKeys.ts          # Centralised TanStack Query key factory
    │   ├── formatters.ts         # Date, SHA truncation, severity → label, blast radius %
    │   └── types.ts              # TypeScript mirrors of Python dataclasses
    │
    ├── context/
    │   ├── ActiveRunContext.tsx  # { activeRunId, setActiveRunId } — drives sidebar per-run nav
    │   └── SidebarContext.tsx    # { collapsed, setCollapsed } — icon-only narrow mode
    │
    ├── components/
    │   ├── layout/
    │   │   ├── AppShell.tsx      # Sidebar + <Outlet> — wraps all routes
    │   │   ├── Sidebar.tsx       # Logo, New Analysis CTA, per-run nav, global nav
    │   │   └── SidebarNavItem.tsx
    │   │
    │   ├── ui/
    │   │   ├── StatusChip.tsx    # CLEAN / BLOCKER verdict chips
    │   │   ├── SeverityChip.tsx  # High / Medium / Low — colour derived from severity value
    │   │   ├── BlastSparkline.tsx # Inline horizontal bar, amber→red gradient
    │   │   ├── MonoText.tsx      # Monospace span with optional copy-to-clipboard
    │   │   ├── CodeBlock.tsx     # Syntax-highlighted pre/code with line numbers (highlight.js)
    │   │   ├── DiffBlock.tsx     # Before/after split view for InterfaceChange
    │   │   ├── LoadingSpinner.tsx
    │   │   ├── ErrorBoundary.tsx
    │   │   └── EmptyState.tsx
    │   │
    │   └── forms/
    │       ├── AnalysisForm.tsx  # Repo path + PR number + Run button
    │       └── FilterBar.tsx     # Reusable severity/type filter row
    │
    ├── screens/
    │   ├── Dashboard/
    │   │   ├── Dashboard.tsx
    │   │   ├── HeroForm.tsx      # POST /api/analyse → poll → navigate
    │   │   ├── StatsRow.tsx      # Three bento stat cards
    │   │   └── RecentRunsList.tsx
    │   │
    │   ├── Report/
    │   │   ├── ReportShell.tsx   # Data boundary — fetches /api/runs/:id/report
    │   │   ├── ReportContext.tsx  # Distributes ImpactReport to all tab descendants
    │   │   ├── ReportHeader.tsx  # PR title, commit range, Agent Verdict chip
    │   │   ├── Summary/          # SummaryTab + sub-components
    │   │   ├── BlastRadius/      # BlastRadiusTab + sub-components
    │   │   ├── Security/         # SecurityTab + SignalList + SignalDetailPanel
    │   │   ├── Dependencies/     # DependenciesTab + sub-components
    │   │   └── TestGaps/         # TestGapsTab + GapCard + BranchCoverageList
    │   │
    │   └── Settings/
    │       └── Settings.tsx      # Read-only .primpact.yml viewer
    │
    └── hooks/
        ├── useRunsList.ts
        ├── useRunSummary.ts
        ├── useReport.ts
        ├── useAnalyse.ts         # Mutation + polling + invalidation
        └── useSignalFilter.ts    # Client-side filter/sort for Security tab
```

---

## 3. Routing

React Router v6 with `createBrowserRouter`. Layout nesting:

```
AppShell                          ← always rendered (sidebar + outlet)
├── /                             → Dashboard
├── /runs/:id                     → ReportShell (fetches report, provides ReportContext)
│   ├── (index)                   → redirect to ./summary
│   ├── summary                   → SummaryTab
│   ├── blast-radius              → BlastRadiusTab
│   ├── security                  → SecurityTab
│   ├── dependencies              → DependenciesTab
│   └── test-gaps                 → TestGapsTab
├── /settings                     → Settings
└── *                             → redirect to /
```

`ReportShell` is the data boundary. It fires `useReport(id)` and renders a loading state, error state, or `ReportHeader` + child `<Outlet>` once data is available. It sets `activeRunId` in `ActiveRunContext` on mount so the sidebar activates the per-run nav section even on direct deep-link navigation.

Tab navigation is handled by the sidebar links, not a tab bar component — consistent with the design mockups.

---

## 4. Component Architecture

### Shared vs screen-specific

A component lives in `src/components/` when it is used by more than one screen, has no direct dependency on `ImpactReport` types, and represents a design primitive. Screen-specific sub-components live inside their screen folder.

### Key component interfaces

```typescript
interface StatusChipProps {
  verdict: "clean" | "has_blockers";
  size?: "sm" | "md";
}

interface SeverityChipProps {
  severity: "high" | "medium" | "low";
}

interface BlastSparklineProps {
  value: number;         // 0–100, percentage of max churn in the run
  variant?: "amber" | "gradient";
}

interface FileImpactTableProps {
  entries: BlastRadiusEntry[];
  maxChurn: number;      // normalises sparkline widths
}

interface SignalListProps {
  signals: SecuritySignal[];
  dependencyIssues: DependencyIssue[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  severityFilter: "all" | "high" | "medium" | "low";
}

interface DiffBlockProps {
  before: string;
  after: string;
  language: string;
  label?: string;
}
```

### ReportContext

`ReportShell` fetches once and provides `ImpactReport` to all tab descendants, avoiding per-tab refetches and prop drilling.

```typescript
const ReportContext = createContext<ImpactReport | null>(null);

function useReportContext(): ImpactReport {
  const ctx = useContext(ReportContext);
  if (!ctx) throw new Error("useReportContext must be used inside ReportShell");
  return ctx;
}
```

---

## 5. State and Data Fetching

### TanStack Query key factory

```typescript
// src/lib/queryKeys.ts
export const queryKeys = {
  runs: {
    all:     (repo: string) => ["runs", repo] as const,
    summary: (id: string)   => ["runs", id, "summary"] as const,
    report:  (id: string)   => ["runs", id, "report"] as const,
  },
  analyse: {
    status: (id: string) => ["analyse", id, "status"] as const,
  },
} as const;
```

### Caching policy

| Query | staleTime | Notes |
|---|---|---|
| `useRunsList` | 30 s | Refetched on window focus; invalidated after new analysis completes |
| `useReport` | `Infinity` | A completed report is immutable — fetched once per session |
| `useAnalyse` status | N/A | `refetchInterval: 2000` while pending; stops on complete/failed |

### State location decisions

| Data | Where | Why |
|---|---|---|
| `ImpactReport` | TanStack Query cache + ReportContext | Server state, immutable once complete |
| `RunSummary[]` | TanStack Query cache | Benefits from background refetch |
| Active run ID | `ActiveRunContext` | Pure UI — drives sidebar, not server state |
| Sidebar collapsed | `SidebarContext` | UI preference only |
| Security filter | `useSignalFilter` local state | Scoped to Security tab; correct to reset on navigation |
| Test gap "addressed" | `useState` in GapCard | v1.0 only — no persistence layer |

---

## 6. API Client

All network calls go through `src/lib/api.ts`. Nothing in the component tree calls `fetch()` directly.

```typescript
class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail?.error ?? body?.error ?? res.statusText;
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail, `API ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getRuns: (repo: string, limit = 50, offset = 0) =>
    apiFetch<RunSummary[]>(`/runs?repo=${encodeURIComponent(repo)}&limit=${limit}&offset=${offset}`),
  getRunSummary: (id: string) =>
    apiFetch<RunSummary>(`/runs/${id}`),
  getReport: (id: string) =>
    apiFetch<ImpactReport>(`/runs/${id}/report`),
  triggerAnalyse: (body: AnalyseRequest) =>
    apiFetch<{ run_id: string; status: "pending" }>(`/analyse`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getAnalyseStatus: (runId: string) =>
    apiFetch<{ run_id: string; status: "pending" | "complete" | "failed" }>(`/analyse/${runId}/status`),
};
```

`ApiError` surfaces through TanStack Query's `error` property. `ReportShell` renders a 404-specific "Run not found" state and a network-failure retry button. `ErrorBoundary` wraps each tab outlet separately so a secondary fetch failure in one tab does not crash the report view.

---

## 7. Styling Approach — SCSS Modules + CSS Custom Properties

### Why SCSS over plain CSS

CSS custom properties carry the design tokens at runtime (inspectable in DevTools, overrideable). SCSS handles everything the authoring side needs:

- **Nesting** — component styles stay readable without BEM suffix chaining
- **Token maps** — severity colours defined once as a Sass map, generated with `@each`; typo in a token name is a compile error, not a silent no-op
- **Mixins** — repeated patterns (ghost border, tonal elevation hover, chip base style) become single `@include` calls, guaranteeing consistency
- **`@use` imports** — tokens and mixins are explicitly imported per module file, with no global leak

Vite supports SCSS with no configuration beyond `npm install -D sass`.

### Token file structure

```scss
// src/styles/_tokens.scss
// Defines SCSS variables AND exports them as CSS custom properties.
// SCSS variables are used inside .module.scss files for compile-time safety.
// Custom properties are used at runtime for DevTools visibility.

$surface:                   #10141a;
$surface-container-low:     #181c22;
$surface-container:         #1e232a;
$surface-container-high:    #252b33;
$surface-container-highest: #2d3440;
$surface-container-lowest:  #0a0e14;

$primary:                   #6fdd78;
$primary-container:         #34a547;
$on-primary-fixed:          #0a1f0c;
$secondary:                 #f0b429;
$tertiary:                  #e05c5c;
$tertiary-container:        #fe554d;

$on-surface:                #dfe2eb;
$on-surface-variant:        #becab9;
$outline:                   #889484;
$outline-variant:           #3e4a3d;

// Severity colour map — used with @each to generate chip variants
$severity-colors: (
  "high":   ($tertiary,   $tertiary-container),
  "medium": ($secondary,  rgba($secondary, 0.15)),
  "low":    ($on-surface-variant, rgba($on-surface-variant, 0.1)),
);

// Export as custom properties so they're available in plain CSS contexts
:root {
  --surface:                   #{$surface};
  --surface-container-low:     #{$surface-container-low};
  --surface-container:         #{$surface-container};
  --surface-container-high:    #{$surface-container-high};
  --surface-container-highest: #{$surface-container-highest};
  --primary:                   #{$primary};
  --primary-container:         #{$primary-container};
  --secondary:                 #{$secondary};
  --tertiary:                  #{$tertiary};
  --on-surface:                #{$on-surface};
  --on-surface-variant:        #{$on-surface-variant};
  --outline-variant:           #{$outline-variant};
  --font-display: "Space Grotesk", sans-serif;
  --font-ui:      "Inter", sans-serif;
  --font-mono:    "JetBrains Mono", monospace;
}
```

### Mixin library

```scss
// src/styles/_mixins.scss
@use "tokens" as t;

// Ghost border: design constraint — no solid 1px borders on surfaces
@mixin ghost-border($alpha: 0.15) {
  border: 1.5px solid rgba(t.$outline-variant, $alpha);
}

// Chip base style — all chips share this foundation
@mixin chip-base {
  display: inline-flex;
  align-items: center;
  padding: 0.1875rem 0.375rem;
  border-radius: 0.1875rem;
  font-family: var(--font-ui);
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  white-space: nowrap;
}

// Tonal elevation hover — surface lifts one step on interact
@mixin tonal-hover {
  transition: background-color 150ms ease;
  &:hover { background-color: var(--surface-container-high); }
}

// Glassmorphism overlay — used for modals and detail panels
@mixin glass-overlay {
  background: rgba(t.$surface-container, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
```

### Example: SeverityChip with @each

```scss
// SeverityChip.module.scss
@use "@/styles/tokens" as t;
@use "@/styles/mixins" as m;

.chip {
  @include m.chip-base;
}

// Generate .high, .medium, .low from the severity map
@each $level, $colors in t.$severity-colors {
  $fg: nth($colors, 1);
  $bg: nth($colors, 2);

  .#{$level} {
    color: $fg;
    background-color: $bg;
  }
}
```

### File conventions

- Every component has a co-located `.module.scss` file (`Sidebar.tsx` → `Sidebar.module.scss`)
- All modules `@use` tokens and mixins explicitly — no `@import`, no global variables
- No hardcoded hex values in `.module.scss` files — only SCSS variables from `_tokens.scss` or `var(--*)` custom properties
- No `box-shadow` on interactive elements (elevation is tonal only); `box-shadow` is permitted only on modal overlays

---

## 8. Build Pipeline

### Vite config

```typescript
// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },

  css: {
    modules: { localsConvention: "camelCase" },
    preprocessorOptions: {
      scss: {
        // Make tokens and mixins available without explicit @use in every file
        // (they still need @use for SCSS variables; this only adds globals for CSS custom properties)
        additionalData: `@use "@/styles/tokens" as t;`,
      },
    },
  },

  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: true },
    },
  },

  build: {
    outDir: "../pr_impact/web/static",
    emptyOutDir: true,
    target: "es2022",
    rollupOptions: {
      output: {
        manualChunks: {
          vendor:    ["react", "react-dom", "react-router-dom"],
          query:     ["@tanstack/react-query"],
          highlight: ["highlight.js"],
        },
      },
    },
  },
});
```

### How the bundle lands in the Python package

`npm run build` outputs to `../pr_impact/web/static/`. `server.py` (Milestone 2) serves this directory:

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import pathlib

STATIC_DIR = pathlib.Path(__file__).parent / "static"

app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

@app.get("/{full_path:path}", include_in_schema=False)
def spa_shell(full_path: str):
    return FileResponse(str(STATIC_DIR / "index.html"))
```

Static files are included in the pip package via `pyproject.toml`:

```toml
[tool.setuptools.package-data]
"pr_impact.web" = ["static/**/*"]
```

The built bundle is committed to the repository. The release process runs `npm run build` before tagging.

---

## 9. Dev Workflow

```bash
# Terminal 1 — Python API
uvicorn pr_impact.web.server:app --reload --port 8080

# Terminal 2 — Frontend (http://localhost:5173)
cd frontend && npm run dev
```

Vite proxies `/api/*` to FastAPI transparently. Full HMR. FastAPI OpenAPI docs at `http://localhost:8080/api/docs`.

### TypeScript / Python type sync

`src/lib/types.ts` mirrors `pr_impact/models.py` by hand. A header comment records the last-sync Python commit SHA. A Vitest test validates a golden JSON fixture (generated by the Python test suite) against TypeScript Zod schemas derived from `types.ts`, catching schema drift before it reaches the UI.

### Testing

| Layer | Tool |
|---|---|
| Component | Vitest + React Testing Library |
| Hooks | Vitest with real `QueryClient` (`retry: false`, `gcTime: 0`) |
| End-to-end | Playwright against `localhost:5173` with seeded history DB |

Tests live alongside their components (`Sidebar.test.tsx` next to `Sidebar.tsx`). Playwright tests live in `tests/e2e/` at the repo root alongside the Python test suite.

---

## 10. Key Decisions

### 1. SCSS Modules over plain CSS Modules

SCSS provides compile-time token safety (misspelled variable = build error, not a silent no-op), `@each` loops for variant generation from the severity map, and mixins that enforce the ghost border and tonal elevation patterns consistently. CSS custom properties are still used for runtime visibility in DevTools. Vite's SCSS support requires zero configuration beyond `npm install -D sass`.

### 2. Single `ReportContext` rather than per-tab queries

`ReportShell` fetches the full `ImpactReport` once (`staleTime: Infinity`) and distributes it via React context. Tab switching is instantaneous. The report is 20–200 KB — well within acceptable in-memory bounds for v1.0.

### 3. Committed static bundle

The `pr_impact/web/static/` build output is committed to the repo. `pip install primpact[web]` is the complete installation story for end users — no Node.js required. The bundle is rebuilt as part of the release workflow. `.gitattributes` marks `pr_impact/web/static/**` as generated to suppress diff noise in PR reviews.

### 4. Polling over WebSockets

`useAnalyse` polls `GET /api/analyse/{id}/status` at 2-second intervals via TanStack Query's `refetchInterval`, which cleans up automatically on unmount. Analysis runs take 30–120 seconds; 15–60 poll requests is negligible overhead. This avoids `websockets` as a server dependency and connection-state management for v1.0.

### 5. Hand-maintained TypeScript types

`src/lib/types.ts` mirrors `models.py` manually, validated by a Vitest fixture test rather than code generation. The Python models are stable dataclasses with no complex polymorphism, making hand-maintenance practical. The fixture test catches drift before any component is written against stale types.

---

## Critical files for implementation

| File | Role |
|---|---|
| `pr_impact/web/server.py` | Must gain `StaticFiles` mount + SPA catch-all route in Milestone 2 |
| `pr_impact/models.py` | Source of truth for all data shapes — `types.ts` must mirror it |
| `pr_impact/web/api/runs.py` | API contract for the report viewer's primary data source |
| `pr_impact/web/api/analyse.py` | Trigger + polling endpoints for the dashboard form flow |
| `frontend/src/lib/types.ts` | *(to create)* TypeScript interfaces mirroring `models.py` |
| `frontend/src/styles/_tokens.scss` | *(to create)* Single source of truth for all design tokens |
