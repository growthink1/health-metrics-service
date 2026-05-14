# Dashboard Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js 14 dashboard that consumes the 5 REST endpoints shipped by Plan 1, then deploy to Railway.

**Architecture:** New repo `~/code/health-metrics-dashboard` (separate from `health-metrics-service`). Next.js 14 app router + TypeScript strict + Tailwind + shadcn/ui + Recharts. Server components render pages from the backend API; client components handle interactivity (window selector, log entry, hover). Console-palette dark theme (matches Jarvis L1-L5). No auth in v1 — single-user, private deploy.

**Tech Stack:** Next.js 14, TypeScript, Tailwind, shadcn/ui, Recharts, Vitest (unit), Playwright (smoke). Deploy: Railway (alongside the existing backend service).

**Spec reference:** `docs/superpowers/specs/2026-05-14-health-metrics-dashboard-design.md` (in the `health-metrics-service` repo).

**Backend state at start:** `growthink1/health-metrics-service` HEAD `f75fc5e` on `main`, tag `v0.2.0-dashboard-backend`. 5 REST endpoints live. Whoop OAuth bootstrap completed (token rotates correctly). Known follow-up (does NOT block this plan): Whoop API v1→v2 migration for `/recovery`, `/sleep`, `/workout` — handled in a separate small PR; the dashboard can still render today using `/cycle` data plus any backfilled rows.

---

## File structure (new repo `~/code/health-metrics-dashboard`)

```
health-metrics-dashboard/
├── app/
│   ├── layout.tsx                  # root layout, NavHeader, fonts, global styles
│   ├── page.tsx                    # `/` grid page
│   ├── metric/[name]/page.tsx      # `/metric/{hrv|rhr|sleep_min|strain|weight_lbs|recovery}`
│   ├── workouts/page.tsx           # `/workouts`
│   └── globals.css                 # Tailwind base + Console palette CSS vars
├── components/
│   ├── NavHeader.tsx
│   ├── TodayStrip.tsx              # 4-cell strip on `/`
│   ├── NarrationLine.tsx           # one-sentence Claude narration
│   ├── LogPanel.tsx                # inline manual-log entry (client)
│   ├── SparklineTile.tsx           # one metric tile in the grid
│   ├── WindowSelector.tsx          # 7/14/30/90 day pills (client, URL state)
│   ├── MetricChart.tsx             # Recharts LineChart / BarChart with ±1σ band
│   ├── DayByDayTable.tsx
│   ├── WorkoutTable.tsx
│   └── ui/                         # shadcn/ui generated components
├── lib/
│   ├── api.ts                      # typed fetch wrappers for backend endpoints
│   ├── types.ts                    # shared TypeScript types for API responses
│   └── format.ts                   # date/number formatting helpers
├── tests/
│   ├── api.test.ts                 # Vitest for api.ts (mocks fetch)
│   └── e2e/dashboard.spec.ts       # Playwright smoke: load, see tiles, drill in
├── .env.local.example
├── next.config.mjs
├── tailwind.config.ts
├── tsconfig.json                   # strict: true
├── playwright.config.ts
├── vitest.config.ts
├── package.json
└── README.md
```

Each task produces a committed unit of work. Frontend tests are pragmatic: Vitest for `lib/` (pure functions, easy to test), Playwright for one E2E smoke at the end. Component rendering is verified by the dev server + visual check.

---

## Task 1: Scaffold Next.js repo + GitHub remote

**Files:**
- Create new directory: `/Users/ironforgeai/code/health-metrics-dashboard/`
- New: full Next.js skeleton (auto-generated)

- [ ] **Step 1.1: Verify the parent directory exists**

```bash
ls /Users/ironforgeai/code/ | grep health-metrics
```
Expected: shows `health-metrics-service`. Confirm `health-metrics-dashboard` does NOT yet exist.

- [ ] **Step 1.2: Create the Next.js app**

```bash
cd /Users/ironforgeai/code
npx --yes create-next-app@latest health-metrics-dashboard \
  --typescript --tailwind --eslint --app --src-dir false --import-alias '@/*' --use-npm
```

When prompted, accept all defaults. This creates `/Users/ironforgeai/code/health-metrics-dashboard/` with Next.js 14 + TS + Tailwind + ESLint, app router, no `src/` directory, `@/*` import alias.

- [ ] **Step 1.3: Add Recharts + Vitest + Playwright + shadcn/ui peer deps**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm install recharts
npm install --save-dev vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @playwright/test
npx playwright install chromium
```

- [ ] **Step 1.4: Initialize shadcn/ui**

```bash
npx --yes shadcn@latest init --yes --defaults
# Then add the components we know we'll need:
npx --yes shadcn@latest add button card input label table dialog
```

This creates `components/ui/` with the chosen components and `lib/utils.ts` (cn helper). If `lib/utils.ts` already exists, leave shadcn's version in place — we'll add other helpers to a separate `lib/format.ts`.

- [ ] **Step 1.5: Create the GitHub repo + initial push**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
# Default Next.js scaffold initializes a git repo + initial commit
git status  # confirm clean state on main/master
gh repo create growthink1/health-metrics-dashboard --public --source . --remote origin --description "Next.js dashboard for health-metrics-service"
git push -u origin HEAD
```

- [ ] **Step 1.6: Verify the dev server boots**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -sf http://localhost:3000/ -o /dev/null && echo "DEV OK" || echo "DEV FAILED"
kill $DEV_PID 2>/dev/null
```
Expected: `DEV OK`.

- [ ] **Step 1.7: Commit + push the Recharts/test/shadcn additions**

```bash
git add -A
git commit -m "chore: add recharts, vitest, playwright, shadcn/ui base components"
git push
```

---

## Task 2: Design system — Console palette + fonts

**Files:**
- Modify: `app/globals.css`
- Modify: `tailwind.config.ts`
- Modify: `app/layout.tsx` (font wiring)

- [ ] **Step 2.1: Replace `app/globals.css` with Console-palette CSS variables**

Open `app/globals.css`. Replace its content with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg: #0a0e14;
  --surface: #0e1422;
  --border: #1f3a5f;
  --text: #d4d4d4;
  --text-muted: #5a8db5;
  --accent-primary: #7eb5e0;
  --accent-warm: #e2a04a;
  --accent-good: #5ad4a8;
  --accent-bad: #e25a4a;
  --accent-strain: #d44a8a;
}

html, body {
  background-color: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, sans-serif;
}

.font-mono {
  font-family: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
}
```

- [ ] **Step 2.2: Extend Tailwind config with the palette tokens**

Open `tailwind.config.ts`. In the `theme.extend` block, add:

```ts
colors: {
  bg: 'var(--bg)',
  surface: 'var(--surface)',
  border: 'var(--border)',
  text: 'var(--text)',
  'text-muted': 'var(--text-muted)',
  'accent-primary': 'var(--accent-primary)',
  'accent-warm': 'var(--accent-warm)',
  'accent-good': 'var(--accent-good)',
  'accent-bad': 'var(--accent-bad)',
  'accent-strain': 'var(--accent-strain)',
},
```

- [ ] **Step 2.3: Wire Inter + JetBrains Mono via `next/font`**

Replace `app/layout.tsx` with:

```tsx
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "health-metrics",
  description: "Hugo's personal health metrics dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-screen bg-bg text-text">{children}</body>
    </html>
  );
}
```

- [ ] **Step 2.4: Smoke + commit**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s http://localhost:3000/ | grep -i 'bg-bg\|--bg' >/dev/null && echo "PALETTE WIRED" || echo "MISSING"
kill $DEV_PID 2>/dev/null
git add app/globals.css tailwind.config.ts app/layout.tsx
git commit -m "feat: Console palette + Inter/JetBrains Mono fonts"
git push
```

---

## Task 3: API client + TypeScript types

**Files:**
- Create: `lib/api.ts`
- Create: `lib/types.ts`
- Create: `lib/format.ts`
- Create: `tests/api.test.ts`
- Create: `vitest.config.ts`
- Create: `.env.local.example`

- [ ] **Step 3.1: Define TypeScript types in `lib/types.ts`**

```ts
export type Recommendation = "deficit" | "deficit_conservative" | "maintenance" | "deload";

export interface TodayStripData {
  recommendation: Recommendation;
  suggested_kcal: number | null;
  suggested_training_mod: string | null;
  today_hrv_ms: number | null;
  hrv_z_3d_avg: number;
  log_status: string;  // "complete" | "all_missing" | comma-separated *_missing tokens
}

export interface DashboardTodayResponse {
  as_of: string;
  metric_date: string;
  today_strip: TodayStripData;
  narration: string | null;
  narration_generated_at: string | null;
  rationale: string[];
}

export interface SeriesPoint {
  date: string;
  value: number | null;
  z?: number | null;
}

export interface GridTile {
  metric: "hrv" | "rhr" | "sleep_min" | "strain" | "recovery" | "weight_lbs";
  current: number | null;
  series: SeriesPoint[];
}

export interface DashboardGridResponse {
  n_days: number;
  tiles: GridTile[];
}

export interface MetricDetailResponse {
  metric: string;
  n_days: number;
  series: SeriesPoint[];
  stats: { mean: number; std: number; slope_per_day: number; z_today: number | null };
  baseline: { mean: number; lower_1sd: number; upper_1sd: number };
}

export interface ManualLogPayload {
  user_id?: string;
  date: string;  // ISO yyyy-mm-dd
  subjective_energy?: number;
  subjective_mood?: number;
  subjective_hunger?: number;
  weight_lbs?: number;
  kcal_consumed?: number;
  protein_g?: number;
  fat_g?: number;
  carbs_g?: number;
  notes?: string;
}

export interface ManualLogResponse {
  logged_date: string;
  fields_updated: string[];
  completeness: { subjective: boolean; weight: boolean; nutrition: boolean };
  next_required_inputs: string[];
}

export interface Workout {
  date: string;
  source: string;
  source_id: string;
  type: string | null;
  started_at: string;
  duration_min: number;
  strain: number | null;
  kcal: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  zones: Record<string, number | null>;
}

export interface WorkoutsResponse {
  n_days: number;
  workouts: Workout[];
}
```

- [ ] **Step 3.2: Implement `lib/api.ts`**

```ts
import type {
  DashboardTodayResponse,
  DashboardGridResponse,
  MetricDetailResponse,
  ManualLogPayload,
  ManualLogResponse,
  WorkoutsResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function _get<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!resp.ok) {
    throw new Error(`API ${path} failed: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

export async function fetchDashboardToday(userId = "hugo", asOf?: string) {
  const qs = new URLSearchParams({ user_id: userId, ...(asOf ? { as_of: asOf } : {}) });
  return _get<DashboardTodayResponse>(`/api/dashboard/today?${qs}`);
}

export async function fetchDashboardGrid(userId = "hugo", days = 14, asOf?: string) {
  const qs = new URLSearchParams({
    user_id: userId,
    days: String(days),
    ...(asOf ? { as_of: asOf } : {}),
  });
  return _get<DashboardGridResponse>(`/api/dashboard/grid?${qs}`);
}

export async function fetchMetricDetail(name: string, userId = "hugo", days = 14, asOf?: string) {
  const qs = new URLSearchParams({
    user_id: userId,
    days: String(days),
    ...(asOf ? { as_of: asOf } : {}),
  });
  return _get<MetricDetailResponse>(`/api/metric/${encodeURIComponent(name)}?${qs}`);
}

export async function fetchWorkouts(
  userId = "hugo",
  days = 30,
  workoutType?: string,
  asOf?: string,
) {
  const qs = new URLSearchParams({
    user_id: userId,
    days: String(days),
    ...(workoutType ? { workout_type: workoutType } : {}),
    ...(asOf ? { as_of: asOf } : {}),
  });
  return _get<WorkoutsResponse>(`/api/workouts?${qs}`);
}

export async function postManualLog(payload: ManualLogPayload): Promise<ManualLogResponse> {
  const resp = await fetch(`${API_BASE}/api/manual-log`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`POST /api/manual-log failed: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<ManualLogResponse>;
}
```

- [ ] **Step 3.3: Add `lib/format.ts` helpers**

```ts
export function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function formatHours(min: number | null): string {
  if (min === null) return "—";
  return `${(min / 60).toFixed(1)}h`;
}

export function formatZ(z: number | null | undefined): string {
  if (z === null || z === undefined) return "";
  const arrow = z < 0 ? "↓" : z > 0 ? "↑" : "·";
  return `${arrow}${Math.abs(z).toFixed(1)}σ`;
}

export function recommendationLabel(rec: string): string {
  return rec.replace(/_/g, " ");
}
```

- [ ] **Step 3.4: Add `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true },
  resolve: { alias: { "@": new URL("./", import.meta.url).pathname } },
});
```

Update `package.json` `"scripts"` to include:
```json
"test": "vitest run",
"test:watch": "vitest",
"e2e": "playwright test"
```

- [ ] **Step 3.5: Write `tests/api.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchDashboardToday,
  fetchDashboardGrid,
  postManualLog,
} from "@/lib/api";

const originalFetch = globalThis.fetch;

describe("api client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://test";
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("fetchDashboardToday calls the right URL", async () => {
    const mockFetch = vi.fn(async () =>
      new Response(JSON.stringify({ metric_date: "2026-05-13" }), { status: 200 }),
    );
    globalThis.fetch = mockFetch as typeof fetch;
    await fetchDashboardToday("hugo", "2026-05-13");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/dashboard/today?user_id=hugo&as_of=2026-05-13"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("fetchDashboardGrid passes days param", async () => {
    const mockFetch = vi.fn(async () =>
      new Response(JSON.stringify({ n_days: 14, tiles: [] }), { status: 200 }),
    );
    globalThis.fetch = mockFetch as typeof fetch;
    await fetchDashboardGrid("hugo", 30);
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("days=30"),
      expect.anything(),
    );
  });

  it("postManualLog sends JSON body", async () => {
    const mockFetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          logged_date: "2026-05-14",
          fields_updated: ["weight_lbs"],
          completeness: { subjective: false, weight: true, nutrition: false },
          next_required_inputs: [],
        }),
        { status: 200 },
      ),
    );
    globalThis.fetch = mockFetch as typeof fetch;
    await postManualLog({ user_id: "hugo", date: "2026-05-14", weight_lbs: 218.4 });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/manual-log"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("fetchDashboardToday throws on non-200", async () => {
    globalThis.fetch = (async () =>
      new Response("nope", { status: 500 })) as typeof fetch;
    await expect(fetchDashboardToday("hugo")).rejects.toThrow(/failed: 500/);
  });
});
```

- [ ] **Step 3.6: Create `.env.local.example`**

```bash
# Backend base URL. In dev: http://localhost:8000
# In prod (Railway): the deployed service URL, e.g. https://health-metrics-service.up.railway.app
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Also create `.env.local` (gitignored) with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.

- [ ] **Step 3.7: Run tests**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run test 2>&1 | tail -20
```
Expected: 4 tests pass.

- [ ] **Step 3.8: Commit + push**

```bash
git add lib/ tests/ vitest.config.ts package.json package-lock.json .env.local.example
git commit -m "feat: API client + types + Vitest harness"
git push
```

---

## Task 4: NavHeader + `/` skeleton (no live data yet)

**Files:**
- Create: `components/NavHeader.tsx`
- Modify: `app/page.tsx`
- Modify: `app/layout.tsx` (add NavHeader)

- [ ] **Step 4.1: Write `components/NavHeader.tsx`**

```tsx
import Link from "next/link";

export function NavHeader() {
  return (
    <header className="border-b border-border px-6 py-3 flex items-center justify-between bg-surface">
      <div className="font-mono text-lg text-accent-primary">health-metrics</div>
      <nav className="flex gap-6 text-sm">
        <Link href="/" className="text-text-muted hover:text-text">Grid</Link>
        <Link href="/workouts" className="text-text-muted hover:text-text">Workouts</Link>
        <span className="text-text-muted opacity-40 cursor-not-allowed">Settings</span>
      </nav>
    </header>
  );
}
```

- [ ] **Step 4.2: Mount NavHeader in `app/layout.tsx`**

In `app/layout.tsx`, import and wrap children:

```tsx
import { NavHeader } from "@/components/NavHeader";
// ... inside <body>:
<body className="min-h-screen bg-bg text-text">
  <NavHeader />
  <main className="p-6">{children}</main>
</body>
```

- [ ] **Step 4.3: Replace `app/page.tsx` with a placeholder**

```tsx
export default function GridPage() {
  return (
    <div className="space-y-6">
      <div className="text-sm text-text-muted font-mono">Today: loading…</div>
      <div className="border border-border rounded p-6 bg-surface">
        Grid placeholder — TodayStrip + tiles wired in Task 5/6
      </div>
    </div>
  );
}
```

- [ ] **Step 4.4: Smoke + commit**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s http://localhost:3000/ | grep -E 'health-metrics|Grid placeholder' >/dev/null && echo "OK" || echo "FAIL"
kill $DEV_PID 2>/dev/null
git add components/NavHeader.tsx app/layout.tsx app/page.tsx
git commit -m "feat: NavHeader + grid page skeleton"
git push
```

---

## Task 5: TodayStrip + NarrationLine wired to live `/api/dashboard/today`

**Files:**
- Create: `components/TodayStrip.tsx`
- Create: `components/NarrationLine.tsx`
- Modify: `app/page.tsx`

**Prerequisite:** Backend running at `http://localhost:8000` (the existing `health-metrics-service`). Start it with:
```bash
cd ~/code/health-metrics-service && source .venv/bin/activate && uvicorn src.health_metrics.main:app --port 8000 > /tmp/hms.log 2>&1 &
```

- [ ] **Step 5.1: Write `components/TodayStrip.tsx`**

```tsx
import type { TodayStripData } from "@/lib/types";
import { formatZ, recommendationLabel } from "@/lib/format";

interface Props {
  data: TodayStripData;
  metricDate: string;
}

function Cell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col gap-1 p-4 border border-border rounded bg-surface min-w-[140px]">
      <div className="text-[10px] uppercase tracking-wider text-text-muted">{label}</div>
      <div className="font-mono text-2xl">{value}</div>
      {sub ? <div className="text-xs text-text-muted">{sub}</div> : null}
    </div>
  );
}

export function TodayStrip({ data, metricDate }: Props) {
  const recommendation = recommendationLabel(data.recommendation);
  const kcal = data.suggested_kcal !== null ? data.suggested_kcal.toLocaleString() : "—";
  const hrv = data.today_hrv_ms !== null ? `${data.today_hrv_ms}` : "—";
  const z = formatZ(data.hrv_z_3d_avg);

  const logStatus = data.log_status === "complete"
    ? "complete"
    : data.log_status === "all_missing"
      ? "all ⚠"
      : data.log_status.split(",").map(t => t.replace("_missing", "⚠")).join(" ");

  return (
    <div className="space-y-2">
      <div className="text-xs text-text-muted font-mono">Metric date: {metricDate}</div>
      <div className="flex gap-3">
        <Cell label="Recommend" value={recommendation} sub={data.suggested_training_mod ?? undefined} />
        <Cell label="kcal" value={kcal} />
        <Cell label="HRV (today)" value={hrv} sub={z || undefined} />
        <Cell label="Log" value={logStatus} />
      </div>
    </div>
  );
}
```

- [ ] **Step 5.2: Write `components/NarrationLine.tsx`**

```tsx
interface Props { narration: string | null; }

export function NarrationLine({ narration }: Props) {
  if (!narration) {
    return (
      <div className="border-l-2 border-text-muted pl-3 italic text-sm text-text-muted">
        Narration unavailable (no API key configured).
      </div>
    );
  }
  return (
    <div className="border-l-2 border-accent-primary pl-3 italic text-sm">
      {narration}
    </div>
  );
}
```

- [ ] **Step 5.3: Rewrite `app/page.tsx` as a server component fetching `/api/dashboard/today`**

```tsx
import { TodayStrip } from "@/components/TodayStrip";
import { NarrationLine } from "@/components/NarrationLine";
import { fetchDashboardToday } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function GridPage() {
  const today = await fetchDashboardToday();
  return (
    <div className="space-y-6 max-w-5xl">
      <TodayStrip data={today.today_strip} metricDate={today.metric_date} />
      <NarrationLine narration={today.narration} />
      <div className="border border-border rounded p-6 bg-surface text-text-muted">
        Grid tiles — wired in Task 6
      </div>
    </div>
  );
}
```

- [ ] **Step 5.4: Smoke against the live backend**

```bash
# Ensure backend is running
curl -sf http://localhost:8000/api/dashboard/today?user_id=hugo&as_of=2026-05-13 >/dev/null && echo "BACKEND OK" || echo "BACKEND DOWN — start it first"

cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s http://localhost:3000/ | grep -E 'Recommend|HRV \(today\)' >/dev/null && echo "TODAY STRIP RENDERS" || echo "FAIL"
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 5.5: Commit + push**

```bash
git add components/TodayStrip.tsx components/NarrationLine.tsx app/page.tsx
git commit -m "feat: TodayStrip + NarrationLine wired to /api/dashboard/today"
git push
```

---

## Task 6: SparklineTile + grid layout + WindowSelector

**Files:**
- Create: `components/SparklineTile.tsx`
- Create: `components/WindowSelector.tsx`
- Modify: `app/page.tsx`

- [ ] **Step 6.1: Write `components/SparklineTile.tsx`**

```tsx
"use client";

import Link from "next/link";
import { ResponsiveContainer, LineChart, Line, BarChart, Bar } from "recharts";
import type { GridTile } from "@/lib/types";

const COLORS: Record<GridTile["metric"], string> = {
  hrv: "var(--accent-primary)",
  rhr: "var(--accent-warm)",
  sleep_min: "var(--accent-good)",
  strain: "var(--accent-strain)",
  recovery: "var(--accent-good)",
  weight_lbs: "var(--text-muted)",
};

const LABELS: Record<GridTile["metric"], string> = {
  hrv: "HRV",
  rhr: "RHR",
  sleep_min: "Sleep",
  strain: "Strain",
  recovery: "Recovery",
  weight_lbs: "Weight",
};

function formatCurrent(metric: GridTile["metric"], v: number | null): string {
  if (v === null) return "—";
  if (metric === "sleep_min") return `${(v / 60).toFixed(1)}h`;
  if (metric === "weight_lbs") return v.toFixed(1);
  if (metric === "strain") return v.toFixed(1);
  return Math.round(v).toString();
}

export function SparklineTile({ tile }: { tile: GridTile }) {
  const color = COLORS[tile.metric];
  const label = LABELS[tile.metric];
  const data = tile.series.map((p) => ({ ...p, value: p.value ?? 0 }));
  const isStrain = tile.metric === "strain";

  return (
    <Link
      href={`/metric/${tile.metric}`}
      className="block border border-border rounded p-4 bg-surface hover:border-accent-primary transition"
    >
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-[10px] uppercase tracking-wider text-text-muted">{label}</div>
        <div className="font-mono text-xl">{formatCurrent(tile.metric, tile.current)}</div>
      </div>
      <div className="h-12">
        <ResponsiveContainer width="100%" height="100%">
          {isStrain ? (
            <BarChart data={data}>
              <Bar dataKey="value" fill={color} radius={[2, 2, 0, 0]} />
            </BarChart>
          ) : (
            <LineChart data={data}>
              <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={false} />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </Link>
  );
}
```

- [ ] **Step 6.2: Write `components/WindowSelector.tsx`**

```tsx
"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";

const OPTIONS = [7, 14, 30, 90] as const;

export function WindowSelector({ defaultDays = 14 }: { defaultDays?: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const current = Number(params.get("days") ?? defaultDays);

  function set(days: number) {
    const next = new URLSearchParams(params);
    next.set("days", String(days));
    router.push(`${pathname}?${next}`);
  }

  return (
    <div className="flex gap-1 font-mono text-xs">
      {OPTIONS.map((d) => (
        <button
          key={d}
          onClick={() => set(d)}
          className={`px-2 py-1 border rounded ${
            d === current
              ? "border-accent-primary text-accent-primary"
              : "border-border text-text-muted hover:border-accent-primary"
          }`}
        >
          {d}d
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 6.3: Wire grid into `app/page.tsx`**

```tsx
import { TodayStrip } from "@/components/TodayStrip";
import { NarrationLine } from "@/components/NarrationLine";
import { SparklineTile } from "@/components/SparklineTile";
import { WindowSelector } from "@/components/WindowSelector";
import { fetchDashboardToday, fetchDashboardGrid } from "@/lib/api";

export const dynamic = "force-dynamic";

interface PageProps { searchParams: { days?: string } }

export default async function GridPage({ searchParams }: PageProps) {
  const days = Number(searchParams.days ?? 14);
  const [today, grid] = await Promise.all([
    fetchDashboardToday(),
    fetchDashboardGrid("hugo", days),
  ]);

  return (
    <div className="space-y-6 max-w-5xl">
      <TodayStrip data={today.today_strip} metricDate={today.metric_date} />
      <NarrationLine narration={today.narration} />

      <div className="flex items-center justify-between">
        <div className="text-xs text-text-muted font-mono">Window:</div>
        <WindowSelector defaultDays={days} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {grid.tiles.map((tile) => (
          <SparklineTile key={tile.metric} tile={tile} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 6.4: Smoke**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s 'http://localhost:3000/?days=14' | grep -E 'HRV|Strain|Sleep' >/dev/null && echo "GRID RENDERS" || echo "FAIL"
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 6.5: Commit + push**

```bash
git add components/SparklineTile.tsx components/WindowSelector.tsx app/page.tsx
git commit -m "feat: 6-tile sparkline grid + window selector"
git push
```

---

## Task 7: `/metric/[name]` drill-down with Recharts

**Files:**
- Create: `app/metric/[name]/page.tsx`
- Create: `components/MetricChart.tsx`
- Create: `components/DayByDayTable.tsx`

- [ ] **Step 7.1: Write `components/MetricChart.tsx`**

```tsx
"use client";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceArea,
  CartesianGrid,
} from "recharts";
import type { SeriesPoint } from "@/lib/types";

interface Props {
  metric: string;
  series: SeriesPoint[];
  baseline: { mean: number; lower_1sd: number; upper_1sd: number };
}

export function MetricChart({ metric, series, baseline }: Props) {
  const data = series.map((p) => ({ ...p, value: p.value ?? 0 }));
  const xDomain: [string, string] | undefined =
    data.length > 0 ? [data[0].date, data[data.length - 1].date] : undefined;
  const isStrain = metric === "strain";
  const color = isStrain ? "var(--accent-strain)" : "var(--accent-primary)";

  return (
    <div className="h-72 border border-border rounded p-3 bg-surface">
      <ResponsiveContainer width="100%" height="100%">
        {isStrain ? (
          <BarChart data={data}>
            <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
            <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: `1px solid var(--border)` }} />
            <Bar dataKey="value" fill={color} radius={[2, 2, 0, 0]} />
          </BarChart>
        ) : (
          <LineChart data={data}>
            <CartesianGrid stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
            {xDomain ? (
              <ReferenceArea
                y1={baseline.lower_1sd}
                y2={baseline.upper_1sd}
                fill="var(--accent-primary)"
                fillOpacity={0.08}
                ifOverflow="extendDomain"
              />
            ) : null}
            <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: `1px solid var(--border)` }} />
            <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={{ r: 3, fill: color }} />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 7.2: Write `components/DayByDayTable.tsx`**

```tsx
import type { SeriesPoint } from "@/lib/types";
import { formatZ } from "@/lib/format";

export function DayByDayTable({ series }: { series: SeriesPoint[] }) {
  const sorted = [...series].sort((a, b) => (a.date < b.date ? 1 : -1));
  return (
    <div className="border border-border rounded bg-surface overflow-hidden">
      <table className="w-full text-sm font-mono">
        <thead className="bg-bg/40 text-text-muted text-xs">
          <tr>
            <th className="text-left px-3 py-2">Date</th>
            <th className="text-right px-3 py-2">Value</th>
            <th className="text-right px-3 py-2">z</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <tr key={p.date} className="border-t border-border">
              <td className="px-3 py-2">{p.date}</td>
              <td className="px-3 py-2 text-right">{p.value ?? "—"}</td>
              <td className="px-3 py-2 text-right text-text-muted">{formatZ(p.z)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 7.3: Write `app/metric/[name]/page.tsx`**

```tsx
import Link from "next/link";
import { fetchMetricDetail } from "@/lib/api";
import { MetricChart } from "@/components/MetricChart";
import { DayByDayTable } from "@/components/DayByDayTable";
import { WindowSelector } from "@/components/WindowSelector";

export const dynamic = "force-dynamic";

interface PageProps {
  params: { name: string };
  searchParams: { days?: string };
}

export default async function MetricPage({ params, searchParams }: PageProps) {
  const days = Number(searchParams.days ?? 14);
  const detail = await fetchMetricDetail(params.name, "hugo", days);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <Link href="/" className="text-text-muted text-sm hover:text-text">← Back to grid</Link>
        <WindowSelector defaultDays={days} />
      </div>
      <h1 className="font-mono text-2xl uppercase">{detail.metric}</h1>
      <MetricChart metric={detail.metric} series={detail.series} baseline={detail.baseline} />
      <div className="text-sm font-mono text-text-muted">
        μ {detail.stats.mean.toFixed(1)} · σ {detail.stats.std.toFixed(2)} ·
        slope {detail.stats.slope_per_day.toFixed(3)}/day ·
        z (today) {detail.stats.z_today !== null ? detail.stats.z_today.toFixed(2) : "—"}
      </div>
      <DayByDayTable series={detail.series} />
    </div>
  );
}
```

- [ ] **Step 7.4: Smoke**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s 'http://localhost:3000/metric/hrv?days=14' | grep -E 'Back to grid|μ' >/dev/null && echo "DRILLDOWN OK" || echo "FAIL"
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 7.5: Commit + push**

```bash
git add app/metric components/MetricChart.tsx components/DayByDayTable.tsx
git commit -m "feat: /metric/[name] drill-down page with Recharts"
git push
```

---

## Task 8: LogPanel — inline manual log entry

**Files:**
- Create: `components/LogPanel.tsx`
- Modify: `app/page.tsx` (conditional render)

- [ ] **Step 8.1: Write `components/LogPanel.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { postManualLog } from "@/lib/api";

interface Props { logDate: string; logStatus: string; }

export function LogPanel({ logDate, logStatus }: Props) {
  const router = useRouter();
  const [energy, setEnergy] = useState("");
  const [mood, setMood] = useState("");
  const [hunger, setHunger] = useState("");
  const [weight, setWeight] = useState("");
  const [showMore, setShowMore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (logStatus === "complete") return null;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { user_id: "hugo", date: logDate };
      if (energy) payload.subjective_energy = Number(energy);
      if (mood) payload.subjective_mood = Number(mood);
      if (hunger) payload.subjective_hunger = Number(hunger);
      if (weight) payload.weight_lbs = Number(weight);
      await postManualLog(payload as Parameters<typeof postManualLog>[0]);
      router.refresh();
      setEnergy(""); setMood(""); setHunger(""); setWeight("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border border-border rounded p-4 bg-surface space-y-3">
      <div className="text-xs uppercase tracking-wider text-text-muted">
        Log today ({logStatus})
      </div>
      <div className="flex gap-3 flex-wrap items-end">
        <Field label="Energy (1-10)" value={energy} onChange={setEnergy} />
        <Field label="Mood (1-10)" value={mood} onChange={setMood} />
        <Field label="Hunger (1-10)" value={hunger} onChange={setHunger} />
        {showMore ? (
          <Field label="Weight (lbs)" value={weight} onChange={setWeight} step="0.1" />
        ) : (
          <button
            onClick={() => setShowMore(true)}
            className="text-xs text-accent-primary hover:underline self-end pb-2"
          >
            More: weight, kcal, macros →
          </button>
        )}
        <button
          onClick={save}
          disabled={saving}
          className="px-3 py-2 border border-accent-primary text-accent-primary rounded font-mono text-sm hover:bg-accent-primary/10 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {error ? <div className="text-xs text-accent-bad">{error}</div> : null}
    </div>
  );
}

function Field({
  label, value, onChange, step,
}: { label: string; value: string; onChange: (v: string) => void; step?: string }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] text-text-muted uppercase tracking-wider">{label}</span>
      <input
        type="number"
        step={step ?? "1"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-bg border border-border rounded px-2 py-1 font-mono text-sm w-28 text-text"
      />
    </label>
  );
}
```

- [ ] **Step 8.2: Mount LogPanel in `app/page.tsx`**

After `<NarrationLine />` and before the window selector:

```tsx
import { LogPanel } from "@/components/LogPanel";
// ...
<LogPanel logDate={today.metric_date} logStatus={today.today_strip.log_status} />
```

- [ ] **Step 8.3: Smoke — post a row, verify the panel hides**

```bash
# Backend must be running
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"user_id":"hugo","date":"2026-05-14","subjective_energy":7,"subjective_mood":7,"subjective_hunger":6}' \
  http://localhost:8000/api/manual-log
# Then reload the dashboard and confirm panel disappears (because completeness.subjective is now true)
```

- [ ] **Step 8.4: Commit + push**

```bash
git add components/LogPanel.tsx app/page.tsx
git commit -m "feat: inline LogPanel wired to POST /api/manual-log"
git push
```

---

## Task 9: `/workouts` page

**Files:**
- Create: `app/workouts/page.tsx`
- Create: `components/WorkoutTable.tsx`

- [ ] **Step 9.1: Write `components/WorkoutTable.tsx`**

```tsx
import type { Workout } from "@/lib/types";

export function WorkoutTable({ workouts }: { workouts: Workout[] }) {
  if (workouts.length === 0) {
    return (
      <div className="border border-border rounded p-6 bg-surface text-text-muted text-sm">
        No workouts in this window.
      </div>
    );
  }
  return (
    <div className="border border-border rounded bg-surface overflow-hidden">
      <table className="w-full text-sm font-mono">
        <thead className="bg-bg/40 text-text-muted text-xs">
          <tr>
            <th className="text-left px-3 py-2">Date</th>
            <th className="text-left px-3 py-2">Type</th>
            <th className="text-right px-3 py-2">Duration</th>
            <th className="text-right px-3 py-2">Strain</th>
            <th className="text-right px-3 py-2">Kcal</th>
            <th className="text-right px-3 py-2">Avg HR</th>
            <th className="text-right px-3 py-2">Max HR</th>
          </tr>
        </thead>
        <tbody>
          {workouts.map((w) => (
            <tr key={`${w.source}-${w.source_id}`} className="border-t border-border">
              <td className="px-3 py-2">{w.date}</td>
              <td className="px-3 py-2">{w.type ?? "—"}</td>
              <td className="px-3 py-2 text-right">{w.duration_min}m</td>
              <td className="px-3 py-2 text-right">{w.strain?.toFixed(1) ?? "—"}</td>
              <td className="px-3 py-2 text-right">{w.kcal ?? "—"}</td>
              <td className="px-3 py-2 text-right">{w.avg_hr ?? "—"}</td>
              <td className="px-3 py-2 text-right">{w.max_hr ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 9.2: Write `app/workouts/page.tsx`**

```tsx
import { fetchWorkouts } from "@/lib/api";
import { WorkoutTable } from "@/components/WorkoutTable";
import { WindowSelector } from "@/components/WindowSelector";

export const dynamic = "force-dynamic";

interface PageProps { searchParams: { days?: string; type?: string } }

export default async function WorkoutsPage({ searchParams }: PageProps) {
  const days = Number(searchParams.days ?? 30);
  const type = searchParams.type;
  const { workouts } = await fetchWorkouts("hugo", days, type);

  const totalStrain = workouts.reduce((s, w) => s + (w.strain ?? 0), 0);
  const totalKcal = workouts.reduce((s, w) => s + (w.kcal ?? 0), 0);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <h1 className="font-mono text-2xl uppercase">Workouts</h1>
        <WindowSelector defaultDays={days} />
      </div>
      <div className="flex gap-3 text-sm font-mono text-text-muted">
        <span>Total strain: {totalStrain.toFixed(1)}</span>
        <span>·</span>
        <span>Total kcal: {totalKcal.toLocaleString()}</span>
        <span>·</span>
        <span>Sessions: {workouts.length}</span>
      </div>
      <WorkoutTable workouts={workouts} />
    </div>
  );
}
```

- [ ] **Step 9.3: Smoke + commit**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
curl -s http://localhost:3000/workouts | grep -E 'Workouts|Total strain' >/dev/null && echo "WORKOUTS OK" || echo "FAIL"
kill $DEV_PID 2>/dev/null
git add app/workouts components/WorkoutTable.tsx
git commit -m "feat: /workouts page"
git push
```

---

## Task 10: Polish — loading, error, empty states + Playwright smoke

**Files:**
- Create: `app/loading.tsx`
- Create: `app/error.tsx`
- Create: `tests/e2e/dashboard.spec.ts`
- Create: `playwright.config.ts`

- [ ] **Step 10.1: Write `app/loading.tsx`**

```tsx
export default function Loading() {
  return (
    <div className="space-y-4 max-w-5xl">
      <div className="h-24 bg-surface border border-border rounded animate-pulse" />
      <div className="h-8 bg-surface border border-border rounded animate-pulse" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-24 bg-surface border border-border rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 10.2: Write `app/error.tsx` (client component, required by Next.js)**

```tsx
"use client";

import { useEffect } from "react";

export default function Error({
  error, reset,
}: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="border border-accent-bad rounded p-6 bg-surface max-w-2xl">
      <div className="text-accent-bad font-mono text-sm uppercase tracking-wider">Error</div>
      <div className="mt-2 text-sm font-mono">{error.message}</div>
      <button
        onClick={reset}
        className="mt-4 px-3 py-1 border border-accent-primary text-accent-primary rounded text-sm hover:bg-accent-primary/10"
      >
        Retry
      </button>
    </div>
  );
}
```

- [ ] **Step 10.3: Write `playwright.config.ts`**

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    headless: true,
  },
  projects: [{ name: "chromium", use: devices["Desktop Chrome"] }],
});
```

- [ ] **Step 10.4: Write `tests/e2e/dashboard.spec.ts`**

```ts
import { test, expect } from "@playwright/test";

test.describe("dashboard smoke", () => {
  test("grid page renders TodayStrip + 6 tiles", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/Recommend/i)).toBeVisible();
    await expect(page.getByText(/HRV \(today\)/i)).toBeVisible();
    // Six metric tiles
    const tiles = page.locator("a[href^='/metric/']");
    await expect(tiles).toHaveCount(6);
  });

  test("clicking HRV tile navigates to /metric/hrv", async ({ page }) => {
    await page.goto("/");
    await page.locator("a[href='/metric/hrv']").click();
    await expect(page).toHaveURL(/\/metric\/hrv/);
    await expect(page.getByText(/Back to grid/i)).toBeVisible();
  });

  test("workouts page renders header", async ({ page }) => {
    await page.goto("/workouts");
    await expect(page.getByRole("heading", { name: /workouts/i })).toBeVisible();
  });
});
```

- [ ] **Step 10.5: Run Playwright against the live dev server**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
# Start dev + backend
cd ~/code/health-metrics-service && source .venv/bin/activate && uvicorn src.health_metrics.main:app --port 8000 > /tmp/hms.log 2>&1 &
BACKEND_PID=$!
sleep 3
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
npm run e2e 2>&1 | tail -25
EXIT=$?
kill $DEV_PID $BACKEND_PID 2>/dev/null
exit $EXIT
```
Expected: 3 Playwright tests pass.

- [ ] **Step 10.6: Commit + push**

```bash
git add app/loading.tsx app/error.tsx playwright.config.ts tests/e2e/
git commit -m "feat: loading + error states + Playwright smoke"
git push
```

---

## Task 11: Live narration — add ANTHROPIC_API_KEY + visual verification

**Files:**
- Modify: `~/code/health-metrics-service/.env` (add `ANTHROPIC_API_KEY`)
- No dashboard code changes — verification only

- [ ] **Step 11.1: Add the Anthropic key to the backend `.env`**

Hugo: add a real `ANTHROPIC_API_KEY=...` line to `~/code/health-metrics-service/.env`. (The file is gitignored — no commit.) Then restart the backend:

```bash
pkill -f 'uvicorn src.health_metrics' 2>/dev/null
cd ~/code/health-metrics-service && source .venv/bin/activate && uvicorn src.health_metrics.main:app --port 8000 > /tmp/hms.log 2>&1 &
sleep 3
```

- [ ] **Step 11.2: Force narration regeneration by changing a signal**

The narration cache is content-addressed on the regulation signals hash. To force a fresh narration, log a different subjective value for today (changes the 3d avg):

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"user_id":"hugo","date":"2026-05-14","subjective_energy":5}' \
  http://localhost:8000/api/manual-log | head -c 200
echo
curl -s "http://localhost:8000/api/dashboard/today?user_id=hugo&as_of=2026-05-14" | python -m json.tool | grep -E 'narration|recommendation'
```

Expected: `narration` is now a real Claude-generated sentence (not `null`). Confirm it makes sense given the regulation recommendation.

- [ ] **Step 11.3: Visual verify in the dashboard**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run dev > /tmp/dashboard_smoke.log 2>&1 &
DEV_PID=$!
sleep 5
echo "Open http://localhost:3000/?days=14 in a browser. Expected: Console-palette page renders with narration line in italic + cyan left-border."
# Press enter when verified visually
read -r _
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 11.4: Document in the dashboard README**

Edit `README.md` (the one auto-generated by create-next-app — replace its content with):

```markdown
# health-metrics-dashboard

Next.js 14 frontend for [growthink1/health-metrics-service](https://github.com/growthink1/health-metrics-service).

## Dev

1. Run the backend: `cd ~/code/health-metrics-service && source .venv/bin/activate && uvicorn src.health_metrics.main:app --port 8000`
2. Run the dashboard: `npm run dev` (port 3000)
3. Open http://localhost:3000

`.env.local` needs `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.

## Tests

- `npm run test` — Vitest (API client)
- `npm run e2e` — Playwright smoke (needs dev server + backend running)

## Deploy

Deployed on Railway alongside the backend. Backend URL goes into the dashboard's `NEXT_PUBLIC_API_BASE_URL` env var.
```

Commit:
```bash
git add README.md
git commit -m "docs: dashboard README"
git push
```

---

## Task 12: Railway deploy

**Files:**
- Create: `next.config.mjs` adjustment for Railway (if needed)
- No code changes beyond config

- [ ] **Step 12.1: Add `engines` field to `package.json`**

In `package.json`:
```json
"engines": { "node": ">=20" }
```
Commit:
```bash
git add package.json
git commit -m "chore: pin node engine for Railway"
git push
```

- [ ] **Step 12.2: Create the Railway service for the dashboard**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
railway login           # if not already logged in
railway init            # create a new project OR link to existing project that has the backend
# If linking to existing project (recommended — share Postgres):
#   pick the project that hosts `health-metrics-service`
#   then: railway service create health-metrics-dashboard
railway link
```

- [ ] **Step 12.3: Set env vars on Railway**

```bash
# Replace BACKEND_URL with the actual deployed backend URL (e.g. https://health-metrics-service.up.railway.app)
railway variables set NEXT_PUBLIC_API_BASE_URL=<BACKEND_URL>
railway variables set NODE_ENV=production
```

- [ ] **Step 12.4: Deploy**

```bash
railway up
```

Watch the build. Next.js auto-detects the framework. The build should produce a server output Railway can run with `npm start`. If Railway asks for a start command, use `npm start`.

- [ ] **Step 12.5: Verify deployed dashboard**

```bash
DEPLOY_URL=$(railway domain | grep -Eo 'https://[^ ]+' | head -1)
echo "Deployed URL: $DEPLOY_URL"
curl -sf "$DEPLOY_URL/" | head -c 200
```
Expected: HTML response containing the Console palette + NavHeader. Open `$DEPLOY_URL` in a browser and confirm today's data renders.

- [ ] **Step 12.6: Tag the release**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
git tag -a v0.1.0-dashboard-frontend -m "Dashboard frontend v1: grid + drill-down + workouts + log panel + Railway deploy"
git push --tags
git log --oneline | head -15
```

- [ ] **Step 12.7: Final test sweep**

```bash
cd /Users/ironforgeai/code/health-metrics-dashboard
npm run test 2>&1 | tail -10
npm run e2e 2>&1 | tail -10
```
Expected: all Vitest tests pass, all Playwright tests pass.

---

## Validation checklist (Plan 2 exit criteria)

- [ ] `npm run build` succeeds (TS strict mode + ESLint clean)
- [ ] `npm run test` — Vitest API-client tests pass (4 expected)
- [ ] `npm run e2e` — Playwright smoke passes (3 tests)
- [ ] `http://localhost:3000/` renders TodayStrip + NarrationLine + 6 SparklineTiles
- [ ] `http://localhost:3000/metric/hrv?days=14` renders chart + day-by-day table
- [ ] `http://localhost:3000/workouts` renders header + workout table
- [ ] LogPanel only shows when log status is incomplete; hides after submit
- [ ] With `ANTHROPIC_API_KEY` set, `/api/dashboard/today` returns a real narration string (not null) and it renders in the italic cyan-border line
- [ ] WindowSelector updates the URL `?days=` param and triggers a re-fetch
- [ ] Railway-deployed URL renders the same content as localhost
- [ ] `v0.1.0-dashboard-frontend` tag pushed to `growthink1/health-metrics-dashboard`

## What's NOT in this plan (deferred to v2 per spec)

- Embedded Claude chat drawer
- Auth + multi-user
- Mobile-first responsive design
- Export (CSV / PDF)
- Compare-period view (this week vs last week)
- Workout-marker overlays on HRV/sleep charts
- Notifications when recommendation changes
- "Why this recommendation?" expandable detail view

## Out-of-plan follow-ups tracked for after Plan 2 ships

- **Whoop API v1 → v2 migration** in `health-metrics-service` (Plan-1-followup PR). `/cycle` still works at v1; `/recovery`, `/activity/sleep`, `/activity/workout` need cutover to v2. Response shapes have small differences (e.g. `sleep_id` is now UUID string). Should ship before the 30d backfill.
- **30-day historical backfill** — fetch + ingest the prior 30 days now that the token works
- **APScheduler** for unattended daily ingest
- **MCP `tools/health/`** module (Track B — Claude Desktop / Claude Code access)
- **Test-helper consolidation in backend** — the inline `_ctx` monkeypatch is duplicated across 5 backend test files
