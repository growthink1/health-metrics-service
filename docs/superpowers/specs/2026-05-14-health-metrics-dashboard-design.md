# Health Metrics Dashboard — Design

**Date:** 2026-05-14
**Owner:** Hugo
**Status:** Brainstormed — pending Hugo review
**Master backend design:** `docs/spec.md`
**Phase 1 backend status:** Tasks 1-11 complete; tokens wired; Whoop scope re-bootstrap in progress; 20/20 tests passing.

This is the v1 dashboard sitting on top of the `health-metrics-service` ingestion backend. It's a personal-use trend explorer with the daily auto-regulation recommendation surfaced inline.

---

## Goal

Give Hugo a single screen that answers two questions in five seconds:
1. **"What's happening in my data?"** — six metrics, 14-day trend, drill-down per metric.
2. **"What should I do today?"** — the rule-based auto-regulation recommendation, plus a one-sentence Claude-generated narration.

Plus a third question that pays for itself in days: **"What needs logging?"** — an inline prompt for whatever's missing from today's manual log, so the recommendation can hit "high" confidence.

## Non-goals (v1)

- Embedded Claude chat surface (deferred — MCP tools cover conversational Q&A from Claude Desktop / Code)
- Multi-user (schema is multi-user-ready; UI is single-user)
- Mobile-first layout (desktop-first; mobile is a Phase 2 concern)
- Coach/share views, export to CSV/PDF
- Goal-setting UX (recommendations come from the deterministic logic + Claude narration; Hugo doesn't configure them in the UI)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  Browser → health-metrics-dashboard (Next.js 14, separate repo)    │
│              │                                                     │
│              ▼ HTTPS                                               │
│  health-metrics-service (existing FastAPI app)                     │
│    + NEW: /api/dashboard/* REST endpoints                          │
│    + NEW: /api/manual-log (POST)                                   │
│    + NEW: /api/narration/today (calls Anthropic API, cached)       │
│              │                                                     │
│              ▼ async SQLAlchemy                                    │
│  Postgres (daily_metrics + workouts + manual_log + …)              │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Two repos:
- **`growthink1/health-metrics-service`** (existing) gains REST endpoints + the narration call. No new service.
- **`growthink1/health-metrics-dashboard`** (new) — Next.js 14 + TypeScript + shadcn/ui + Recharts + Tailwind. Deployed alongside the service on Railway.

Why a separate Next.js app (not bundling into FastAPI):
- Matches Hugo's existing patterns (Storefront, Jarvis web)
- Next.js routing handles the page-route drill-down pattern naturally (`/`, `/metric/[name]`, `/workouts`)
- Independent deploys — frontend iteration doesn't redeploy the ingest service
- Server-side rendering for clean initial load

## Tech stack

| Layer | Tech | Notes |
|---|---|---|
| Frontend framework | Next.js 14 (app router) + TypeScript | Matches Storefront/Jarvis web |
| UI primitives | shadcn/ui + Tailwind CSS | Standard Hugo stack |
| Charts | Recharts | Required by spec §"What's deferred to phase 2" |
| Data fetching | `fetch` with Next.js `revalidate` caching | No SWR/React Query in v1; revalidate handles staleness |
| Backend additions | FastAPI routes in health-metrics-service | Async SQLAlchemy queries |
| Claude narration | Anthropic Python SDK, server-side only | API key in service env; cached 1/day per user |
| Auth (v1) | None — single user, localhost / private Railway URL | Auth gate added before any external sharing |
| Deploy | Railway (two services sharing the Postgres) | |

---

## Page-level UX

### `/` — Grid page (default view)

```
┌─────────────────────────────────────────────────────────────┐
│  health-metrics                          [Today: 2026-05-14] │
├─────────────────────────────────────────────────────────────┤
│  TODAY STRIP                                                │
│  ┌──────────────┬──────────────┬──────────────┬──────────┐ │
│  │ RECOMMEND    │ KCAL         │ HRV (today)  │ LOG      │ │
│  │ maintenance  │ 2,800        │ 45  ↓1.2σ    │ subj ⚠   │ │
│  └──────────────┴──────────────┴──────────────┴──────────┘ │
│  │ HRV depressed 1.2σ over 3 days — holding deficit pause. │ ← Claude narration
├─────────────────────────────────────────────────────────────┤
│  LOG PANEL (only when subjective is missing today)          │
│  Log today's energy / mood / hunger:  [_] [_] [_]  [Save]   │
│  More: weight, kcal, macros →                                │
├─────────────────────────────────────────────────────────────┤
│  Window: [7d] [14d✓] [30d] [90d]                            │
│  ┌──────────┬──────────┬──────────┐                         │
│  │ HRV  48  │ RHR  56  │ Sleep 6.6h│  ← clickable tiles     │
│  │ ━╱╲━╲━━━│ ━━╲╱━━╲━│ ━╱━╱━╲━━│                          │
│  ├──────────┼──────────┼──────────┤                         │
│  │ Strain   │ Weight   │ Recovery │                         │
│  │ 11.2     │ 218.4    │ 65       │                         │
│  │ ▁▃▂▆▃▅▂  │ ━━━━╲━━━│ ━╱━╱╲━╱  │                         │
│  └──────────┴──────────┴──────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Today strip pulls from `GET /api/dashboard/today` (returns today's `daily_metrics` row + auto-regulation status + narration).
- Log panel only appears when today's `manual_log.subjective_energy` is NULL. Three quick inputs (1-10 each), debounced save. "More" expands to weight / kcal / macros inline.
- Window selector swaps the data range for all 6 tiles simultaneously (single query).
- Each tile is a clickable card. Hover → subtle highlight. Click → `/metric/[name]`.
- Today strip's HRV value shows the latest day's value + the 3d-avg z-score (e.g. `45 ↓1.2σ` means current HRV 45ms, depressed 1.2σ from baseline).

### `/metric/[name]` — Drill-down page

```
┌─────────────────────────────────────────────────────────────┐
│  ← Back to grid                                             │
│  HRV                          [7d] [14d✓] [30d] [90d] […]   │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐    │
│  │ ━━╲━━╱━━╲╱╲━━━╱━━╲━╱━━╲━━━╱╲━━━━╲━╱━╱╲━━╲━━━━ │    │ ← main chart (Recharts)
│  │                                                      │    │   HRV (solid blue)
│  │   ┄┄┄┄┄┄┄┄┄┄┄ baseline mean ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │    │   ± 1σ shaded
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Stats: μ 48.2 ms · σ 5.1 · slope -0.2/day · z (today) -1.2 │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Day-by-day (most recent first)                       │    │
│  │ 5/13  45 ms  ↓1.2σ                                   │    │
│  │ 5/12  47 ms  ↓0.5σ                                   │    │
│  │ 5/11  49 ms  ↑0.1σ                                   │    │
│  │ …                                                    │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

Six routes: `/metric/hrv`, `/rhr`, `/sleep`, `/strain`, `/weight`, `/recovery`. All share the same component, parameterized by metric.

**Behavior:**
- Recharts `LineChart` for HRV / RHR / Sleep / Weight / Recovery (continuous metrics). `BarChart` for Strain (per-day discrete totals; bars communicate accumulation better than a line).
- Baseline shading: `±1σ` band centered on the rolling 14d mean.
- Header window selector — independent from grid page selection.
- Day-by-day table below the chart, sorted desc, with z-score annotation per day.
- Future-ready: room for "compare to" selector, workout-marker overlays, export — but v1 is just chart + stats + table.

### `/workouts` — Workout history

Recommended default (overrule if you want different):
- Header strip: total strain this week, this month, % of sessions in each type (cycling / strength / etc.).
- Table: chronological list of workouts. Columns: date, type, duration, strain, kcal, avg HR, max HR. Click a row → workout detail modal (zone breakdown, source data).
- Filters: workout type, date range.

Linked from: top nav, Strain-tile drill-down ("recent workouts" section at bottom).

---

## Components

| Component | Purpose | Location | Notes |
|---|---|---|---|
| `<TodayStrip>` | 4-cell row above grid | `/` | Recommendation + kcal + HRV+z + log-status |
| `<NarrationLine>` | Claude-generated sentence | Under Today strip | Italic, cyan left-border |
| `<LogPanel>` | Inline manual entry | `/` (conditional) | Auto-collapse on completion |
| `<SparklineTile>` | One metric in the grid | `/` | Title + current value + 14d sparkline |
| `<MetricChart>` | Full deep-dive chart | `/metric/[name]` | Recharts, ±1σ band, baseline |
| `<DayByDayTable>` | Drill-down table | `/metric/[name]` | Per-day value + z-score |
| `<WorkoutTable>` | Workout list | `/workouts` | Filterable |
| `<WindowSelector>` | 7/14/30/90 day pills | Multiple | URL state via `?days=` |
| `<NavHeader>` | App-wide top nav | All pages | Links: grid, workouts, settings (future) |

Each component is server-rendered where possible; only interactive bits (tile hover, window selector clicks, log entry inputs) are client components.

---

## Data flow & backend endpoints

All new endpoints live in `health-metrics-service`. They return JSON, no auth in v1.

### `GET /api/dashboard/today?user_id=hugo`

Returns:
```json
{
  "as_of": "2026-05-14T08:00:00-04:00",
  "metric_date": "2026-05-13",
  "today_strip": {
    "recommendation": "maintenance",
    "suggested_kcal": 2800,
    "suggested_training_mod": "Full program, no progression push",
    "today_hrv_ms": 45,
    "hrv_z_3d_avg": -1.2,
    "log_status": "subjective_missing"
  },
  "narration": "HRV depressed 1.2σ over 3 days — holding deficit pause. If subjective energy comes back >7 tomorrow you're cleared for the cycling block.",
  "narration_generated_at": "2026-05-14T08:00:00Z"
}
```

Computes auto-regulation via existing `regulation.py` logic. Caches the narration keyed on `(user_id, metric_date, sha256(regulation_signals_canonical_json))` — pure content-addressed cache. New narration only when signals change (e.g. manual log entry shifts the 3d subjective average). No time-based expiry needed.

### `GET /api/dashboard/grid?user_id=hugo&days=14`

Returns 6 sparkline series + current values:
```json
{
  "n_days": 14,
  "tiles": [
    {"metric": "hrv", "current": 48, "series": [{"date": "2026-04-30", "value": 52}, ...]},
    {"metric": "rhr", "current": 56, "series": [...]},
    {"metric": "sleep_min", "current": 396, "series": [...]},
    {"metric": "strain", "current": 11.2, "series": [...]},
    {"metric": "weight_lbs", "current": 218.4, "series": [...]},
    {"metric": "recovery", "current": 65, "series": [...]}
  ]
}
```

### `GET /api/metric/{name}?user_id=hugo&days=14`

Returns full data for the drill-down page:
```json
{
  "metric": "hrv",
  "n_days": 14,
  "series": [{"date": "...", "value": ..., "z": ...}, ...],
  "stats": {"mean": 48.2, "std": 5.1, "slope_per_day": -0.2, "z_today": -1.2},
  "baseline": {"mean": 48.2, "lower_1sd": 43.1, "upper_1sd": 53.3}
}
```

### `POST /api/manual-log`

Body:
```json
{"user_id": "hugo", "date": "2026-05-14",
 "subjective_energy": 6, "subjective_mood": 7, "subjective_hunger": 5,
 "weight_lbs": 218.4, "kcal_consumed": 2290, "protein_g": 195}
```

UPSERT into `manual_log`. Returns `{"fields_updated": [...], "completeness": {...}}` (mirrors the MCP tool's response shape so the implementations can share code later).

### `GET /api/workouts?user_id=hugo&days=30&type=cycling`

Returns paginated workouts list. Filterable by type.

### `GET /api/narration/today?user_id=hugo` (internal helper)

The narration endpoint, separated so it can be cached / preheated independently. Called by `/api/dashboard/today`.

---

## Claude narration mechanism

**Prompt template** (server-side, never sent to client):

```
You are Hugo's health-and-fitness analytical assistant. Given today's
auto-regulation output and the underlying signals, write ONE concise
sentence (max 25 words) explaining the recommendation in physiological
terms Hugo will recognize. Be specific about the numbers driving the
call. Don't hedge.

Today's recommendation: {recommendation}
Triggering signals: {triggering_signals as JSON}
3-day trend: HRV {hrv_trend}, RHR {rhr_trend}, sleep {sleep_trend}
Conservative bias is built into the rules — your job is to explain,
not second-guess.
```

**Model:** `claude-3-5-haiku-latest` (cheap, fast, sufficient for 1-sentence narration).

**Caching:** the input to the LLM is the auto-regulation `triggering_signals` payload, hashed. If the hash matches the last cached narration for that user+date, return cached. Invalidates when manual log changes (subjective markers update the signals).

**Cost:** ~250 input tokens + 30 output tokens × 1-2 invocations per day = ~$0.001/day. Negligible.

**Failure mode:** if Anthropic API fails, dashboard renders without narration (Today strip still shows the rule-based recommendation). No hard failure.

---

## Palette — Console

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0a0e14` | Page background |
| `--surface` | `#0e1422` | Tiles, Today strip, log panel |
| `--border` | `#1f3a5f` | Card borders, dividers |
| `--text` | `#d4d4d4` | Body text |
| `--text-muted` | `#5a8db5` | Labels, captions |
| `--accent-primary` | `#7eb5e0` | HRV, primary data lines, links |
| `--accent-warm` | `#e2a04a` | RHR, warnings |
| `--accent-good` | `#5ad4a8` | Sleep, recovery, positive deltas |
| `--accent-bad` | `#e25a4a` | Negative z, danger states |
| `--accent-strain` | `#d44a8a` | Strain bars |
| Mono font | SF Mono / JetBrains Mono | Labels, numbers |
| Body font | Inter | Narration, body text |

Same hex values as Jarvis L1-L5 console for visual consistency across Hugo's personal tools.

---

## Implementation order (Phase 2)

1. **Backend endpoints** in health-metrics-service. Add `/api/dashboard/today`, `/grid`, `/metric/[name]`, `/workouts`, `/manual-log`. Cover with pytest + the existing `db_session` rollback fixture. 20 → ~30 tests. *(~2 hours)*
2. **Narration endpoint** — Anthropic SDK + caching layer. Mock for tests; live-call for smoke. *(~1 hour)*
3. **Scaffold dashboard repo** at `~/code/health-metrics-dashboard` — Next.js 14 + TS + Tailwind + shadcn/ui. `growthink1/health-metrics-dashboard` GitHub repo, public + privacy URL reused. *(~1 hour)*
4. **Pages 0** — `<NavHeader>` + `/` skeleton: hardcoded mocked data, shape only. *(~1 hour)*
5. **Pages 1** — wire `/` to live `/api/dashboard/today` + `/api/dashboard/grid`. SparklineTile component built first. *(~3 hours)*
6. **Pages 2** — `/metric/[name]` drill-down page with Recharts + day-by-day table. *(~2 hours)*
7. **LogPanel** wired to `POST /api/manual-log`. Inline-conditional render based on Today strip's `log_status`. *(~2 hours)*
8. **Workouts page**. *(~2 hours)*
9. **Polish** — keyboard nav, loading states, error states, no-data states. *(~2 hours)*
10. **Railway deploy** — backend redeploy with new endpoints; dashboard new Railway service. *(~1 hour)*

**Total estimate:** ~17 hours of focused work for v1. Spread across 2-3 days, parallelizable with the Track B MCP tools work.

## Phase 1 dependencies (already in flight)

- Live token re-bootstrap (Whoop scope fix)
- 30-day backfill once tokens are stable
- APScheduler for unattended daily ingest
- MCP `tools/health/` module (Track B; gives Hugo Claude access in this CLI + Claude Desktop)

The dashboard build (this spec) starts after at least the live re-bootstrap + a few days of real data. Don't need the full 30-day backfill to start building — mock data + 3 days of real data is sufficient for component development.

---

## What's deferred to v2

- Embedded Claude chat surface (drawer or sidebar)
- Auth + multi-user
- Mobile-first responsive design (v1 is desktop-only; mobile usable but not optimized)
- Export (CSV / PDF)
- Compare-period view (this week vs last week)
- Workout-marker overlays on HRV/sleep charts
- Notifications when recommendation changes
- "Why this recommendation?" expandable detail view (would surface the triggering_signals payload as a debug pane)
