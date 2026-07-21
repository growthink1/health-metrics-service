# Progress Dashboard v2 — Live-Wire Design

**Date:** 2026-07-21
**Repos:** `health-metrics-service` (FastAPI + Postgres, Railway `backend`) + `health-metrics-dashboard` (Next.js 16 App Router, Railway `dashboard`, health.ironforgeai.com) + a small `mcp-unified-server` re-vendor.
**Owner:** Hugo · Priority: medium-high · Supersedes the v1 dashboard-live-data spec.

---

## 1. Context & reconciliation (what's real vs. the incoming spec)

The incoming task described a "static React file with 4 tabs and hard-coded arrays" (`WEIGHT_DAILY`, `BIG4`, …) to swap onto live data. Reconnaissance found that premise **does not match the actual dashboard repo**, plus several backend assumptions that differ from reality. This design records the real ground truth and the decisions made with Hugo:

- **The 4-tab static dashboard does not exist in `health-metrics-dashboard`** (grep of the full tree + all 50 commits of history + sibling repos: zero hits for any named constant, tab layout, or dot-provenance convention). That repo is a Next.js 16 App Router app that has been **API-driven since its first data commit** — 3 pages (Grid/Workouts/Goals) + a `/metric/[name]` drilldown. Body-fat/lean-mass aren't in its type system. **Decision: build the 4-tab `/progress` view NEW in that app** (not "swap constants").
- **`workout_sets` exists but is empty; `exercise_alias` and `workout_detail` do not exist.** So strength is **`LIFTS`-notes-parser-only** today; exercise grouping/aliasing lives in `progress_config.py`, not a table. `workout_sets` is preferred automatically once populated (future `workout_detail` task).
- **Table is `manual_log`** (not `manual_entries`); free-text lift logs are in `manual_log.notes`.
- **`health_events.id` is a real UUID** already; the brief drops it when building `HealthEventSnapshot`. §6 fold-in = surface it.
- **`event_type` CHECK enum is 8 medical types — no `'system'`.** **Decision: connector-health is log-WARNING + endpoint + pill only — no `health_event` write, no enum change.**
- **`compute_weight_trend`** already yields dewatered weight + filtered velocity — reuse it, never reimplement.
- **No new DB tables.** The only schema change is **one nullable column** — `body_composition.vat_cm2` (VAT cm²) — added via migration and backfilled with the July-8 DEXA value (**145**, from the scan). Everything else reads existing tables, and the event `id` is a schema field on an existing UUID column.

## 2. Locked decisions

| # | Decision |
|---|----------|
| 1 | One spec, **phased build**: Phase 1 backend API → Phase 2 connector-health → Phase 3 frontend. Backend is independently API-verifiable first. |
| 2 | Connector-health: **log WARNING + `/health/connectors` + header pill only** (no `health_event`, no enum change). |
| 3 | Frontend: **build the 4-tab `/progress` view new** in `health-metrics-dashboard`, consuming the endpoints via its existing `lib/api.ts` + same-origin proxy. |
| 4 | Strength parser is **conservative** — skips segments it can't confidently parse rather than emit a wrong load. |
| 5 | `PLAN_START_WEIGHT` = earliest `manual_log.weight_lbs` on/after `PLAN_START`, not a hardcoded constant. |
| 6 | Provenance semantics are sacred: `proj`/`estimated` are **never** labeled measured, at the endpoint or the chart. |

---

## PHASE 1 — Backend progress API (`health-metrics-service`)

### 3. `progress_config.py` (new, `src/health_metrics/progress/`)

All model-driven constants + the alias map. These are hypotheses revised at each DEXA — the endpoints present them as projections, never measurements.

```python
from datetime import date

PLAN_START = date(2026, 6, 1)
GOAL = {"weight": 180.0, "bf": 13.0, "lean": 155.0}
BF_DROP_PCT_PER_MO = 1.8      # body-fat % glidepath
LEAN_GAIN_LB_PER_MO = 0.8     # lean-mass glidepath
PROJ_TAPER = 0.90             # monthly velocity decay for the weight-horizon projection
CONNECTOR_STALE_HOURS = 36    # (Phase 2) data-staleness threshold
TOKEN_EXPIRY_WARN_HOURS = 48  # (Phase 2) token-expiry warning window

# exercise pattern (lowercased substring) -> (canonical label, group)
EXERCISE_ALIASES: list[tuple[str, str, str]] = [
    ("lat pulldown", "Lat Pulldown", "Pull"),
    ("cable row", "Cable Row", "Pull"),
    ("rear-delt fly", "Rear Delt Fly", "Pull"),
    ("rear delt fly", "Rear Delt Fly", "Pull"),
    ("shrug", "Shrug", "Pull"),
    ("hammer curl", "Hammer Curl", "Pull"),
    ("cable curl", "Cable Curl", "Pull"),
    ("back squat", "Back Squat", "Legs"),
    ("barbell rdl", "Barbell RDL", "Legs"),
    ("bulgarian split squat", "Bulgarian Split Squat", "Legs"),
    ("goblet squat", "Goblet Squat", "Legs"),
    ("bench", "Bench Press", "Push"),
    ("ohp", "Overhead Press", "Push"),
    ("overhead press", "Overhead Press", "Push"),
    ("incline db press", "Incline DB Press", "Push"),
    ("lateral raise", "Lateral Raise", "Push"),
    # order matters: longer/more-specific patterns first ("back squat" before "squat")
    ("squat", "Back Squat", "Legs"),
]
BIG4 = ["Bench Press", "Back Squat", "Lat Pulldown", "Cable Row"]
```

Pure projection helpers live in `progress/math.py` (deterministic, unit-tested): monthly schedule interpolation, tapered velocity extrapolation, bodycomp glidepath. No I/O.

### 4. Endpoints (`routes/progress.py`, prefix `/api/v1/progress`)

All take `?user_id=hugo`; **empty data → empty arrays, never 500**; no auth (single-user internal). Shapes are 1:1 with the frontend's planned arrays (defined here, since no component exists yet).

**4.1 `GET /progress/weight`**
```json
{ "daily":   [{ "day": 0, "date": "Jun 1", "weight": 229.0, "dewatered": null }],
  "horizon": [{ "m": "Jun 26", "actual": 229.0, "schedule": 229.0, "proj": 229.0, "dexa": false }],
  "kpis": { "current": 217.4, "dewatered": 216.9, "lost_since_start": 11.6,
            "to_goal": 37.4, "filtered_lb_per_wk": -1.3 } }
```
- `daily`: one point per `manual_log` row with non-null `weight_lbs`, ordered by `log_date`. `day` = `(log_date - PLAN_START).days`. `date` = `"%b %-d"`. `dewatered` = the de-watered filtered series from `compute_weight_trend` (aligned by date; `null` where unavailable, e.g. pre-workout-data days).
- `horizon` (monthly buckets from PLAN_START through the plan window): `actual` = latest reading in the month (else `null`); `schedule` = linear `PLAN_START_WEIGHT → GOAL.weight` across the plan window; `proj` = current filtered velocity extrapolated with `PROJ_TAPER` (gentle slope decay as TDEE falls with weight); `dexa: true` on months containing a `scheduled_dexa` event.
- `kpis`: `current` = latest raw reading; `dewatered` = `compute_weight_trend().weight_dewatered_lbs`; `lost_since_start` = `PLAN_START_WEIGHT - current`; `to_goal` = `current - GOAL.weight`; `filtered_lb_per_wk` = `filtered_velocity_lbs_per_day * 7`.

**4.2 `GET /progress/bodycomp`**
```json
{ "bodyfat": [{ "m": "Jul 26", "measured": 33.1, "estimated": null, "proj": 33.1, "dexa": true }],
  "lean":    [{ "m": "Jul 26", "measured": 147.2, "estimated": null, "proj": 147.2, "dexa": true }],
  "vat":     [{ "date": "2026-07-08", "vat_cm2": 145 }],
  "kpis": { "bf_est": 32.8, "lean_est": 148.0, "vat_baseline": 145 } }
```
- `measured`: one point per `body_composition` row (currently the July 8 DEXA: bf 33.1, lean 147.2). `estimated`: current-month derived only (`lean` held at last-measured; `fat = weight − lean`; `bf% = fat/weight`). `proj`: glidepath to `GOAL` via `BF_DROP_PCT_PER_MO` / `LEAN_GAIN_LB_PER_MO`.
- `vat`: its own series from `body_composition.vat_cm2` (VAT is the primary metric; currently one point = 145, second lands in October). The nullable `vat_cm2` column is added + backfilled (145 for the July-8 row) — the feature's only schema change (§1).
- KPIs never label `estimated`/`proj` as measured.

**4.3 `GET /progress/strength`**
```json
{ "groups": { "Push": {"Bench Press": [["Jul 17",225]]}, "Legs": {"Back Squat": [["Jul 21",235]]}, "Pull": {"Lat Pulldown": [["Jul 15",250]]} },
  "big4": { "Bench Press": [["Jul 17",225]], "Back Squat": [["Jul 21",235]], "Lat Pulldown": [["Jul 15",250]], "Cable Row": [["Jul 15",260]] },
  "kpis": { "bench_top": 225, "squat_top": 235, "pulldown": 250, "cable_row": 260 } }
```
- Primary source `workout_sets` (top working-set load per session: `MAX(weight_lbs) WHERE rpe/notes not a warmup`, grouped via `EXERCISE_ALIASES`) — **empty today**, so the **`LIFTS`-notes parser is the live source** (`progress/strength_notes.py`, pure + tested). Prefer the table per (date, exercise) when both exist.
- KPIs = latest top load for each big-4 lift.

**4.4 `GET /progress/cardio`**
```json
{ "walking": [{ "date": "Jul 20", "miles": 2.51, "min": 56, "pace": 22.3 }],
  "cycling": [{ "date": "Jul 20", "min": 40, "avg_hr": 117, "kcal": 285 }],
  "hiit":    [{ "label": "#3", "z4min": 12.5, "opener": 160, "status": "completed" }],
  "kpis": { "longest_walk_mi": 3.23, "walk_pace": 21.7, "z2_week_min": 138 } }
```
- `walking`/`cycling` from `workouts` (`workout_type IN ('walking','cycling')`) unioned with `activity_log` (manual walks/rides); `miles`/`min`/`pace` (min per mile) where derivable. `hiit`: completed 4×4s with time-in-Z4 if present, else programmed targets with `status:'planned'` — preserve the target/actual distinction. `z2_week_min` = walk + Z2-ride minutes this week (target band 150–180).

### 5. Strength `LIFTS` parser (`progress/strength_notes.py`) — the critical, fiddly unit

Real grammar (from prod notes), pipe-delimited:
```
LIFTS (Lower #2) | Session RPE 6.5 (…) | <free commentary> | <exercise>: <load spec> @rpe (notes) -> <future cue> | …
```
Rules (pure function `parse_lifts(note: str, log_date: date) -> list[LiftTop]`):
1. Slice from the first `LIFTS`; split on ` | `.
2. First segment `LIFTS (<label>)` → session group hint (`Lower`→Legs, `Push`→Push, `Pull`→Pull; strip `#n`). Used only as a fallback group.
3. Skip `Session RPE …` and any segment that doesn't match an `EXERCISE_ALIASES` pattern (conservative — commentary like "back GOOD under load" is skipped).
4. For an exercise segment: **split on `->` and keep only the LEFT (achieved) side** — the right side is the future "NEXT/gate" cue and must be ignored. Extract candidate loads via `(\d+(?:\.\d+)?)\s*(?:x\d|working|@)` and take the **max** as the top working-set load. (Ramp/warmup loads are always lighter, so max-of-achieved ≈ top working set.)
5. Map the exercise name to `(canonical, group)` via `EXERCISE_ALIASES` (first match; specific-before-generic ordering). No match → skip.
6. Emit `LiftTop(canonical, group, log_date, top_load)`.

Validated expected outputs (must be covered by tests against the real strings in §1):
- `"Bench: 220x5, 220x5, 225x5 @7.5 (PR…) -> Push #7 … 225 across, gate 230"` → Bench Press / Push / **225** (not 230).
- `"Back squat: ramp 135/185/205; 225x5 x3, 235x5 @7.5 … -> NEXT: 235 across, gate 240"` → Back Squat / Legs / **235** (not 240).
- `"Barbell RDL: 185x8x3 @7.5"` → Barbell RDL / Legs / **185**.
- `"Lat pulldown 250 working, top @7.5 -> Pull #6 … 255"` → Lat Pulldown / Pull / **250**.
- `"Cable row 260 @7 -> hold … 265"` → Cable Row / Pull / **260**.

### 6. Event-id fold-in (§6 of the incoming spec)
- Add `id: str` to `HealthEventSnapshot` (schemas.py) and pass `str(ev.id)` in `_active_events`.
- New `GET /api/v1/health-events?status=pending` (and open-ended) returning events **including `id`** so `upsert_health_event(event_id=…, status='resolved')` can finally close the stale July-8 `scheduled_dexa`.
- **MCP re-vendor**: because `HealthEventSnapshot` gains a field, re-vendor `mcp-unified-server/tools/health_metrics_types.py` (cp + header SHA bump) + its shape-contract test. Bump `BRIEF_SCHEMA_VERSION` if the brief JSON shape changes for consumers (it adds `active_events[].id`).

---

## PHASE 2 — Connector health

### 7. `GET /api/v1/health/connectors` (`routes/health_connectors.py`)
```json
[ {"source":"whoop","last_ingest_at":"2026-07-21T13:00:00Z","token_expires_at":"2026-07-21T14:49:00Z","status":"ok"},
  {"source":"oura","last_ingest_at":"2026-07-21T13:00:00Z","token_expires_at":null,"status":"ok"},
  {"source":"strava","last_ingest_at":"2026-07-20T00:00:00Z","token_expires_at":null,"status":"stale"} ]
```
- **whoop**: `last_ingest_at` = latest `daily_metrics.ingested_at` where whoop present; `token_expires_at` = `oauth_state.access_expires_at`; `status='stale'` if the latest `daily_metrics.whoop_status ∈ {auth_error, failed}` (reuses the signal shipped this week) OR no whoop data in `CONNECTOR_STALE_HOURS`; `'expiring'` if a refresh hasn't happened and the token is within `TOKEN_EXPIRY_WARN_HOURS`; else `'ok'`.
- **oura**: personal token (no expiry) → status off data staleness only.
- **strava**: off `activity_log` (source `strava`) recency; `n/a`/`ok` when no cadence expectation.

### 8. Scheduler check
Add `_check_connectors(user_id)` to the backend scheduler that calls the same logic and `log.warning("connector_stale", source=…, status=…)` for any non-`ok`. No `health_event` write. Runs as its own early-morning tick (the backend scheduler is ET; align near the "04:30" intent — a dedicated `CronTrigger` at 04:30 ET). This is the proactive guard against another silent-Whoop-token week.

---

## PHASE 3 — Frontend `/progress` (`health-metrics-dashboard`, Next.js 16 App Router)

### 9. Structure
- New route **`app/progress/page.tsx`** with **4 tabs** (Strength / Weight / Body Comp / Cardio) — a client tab component (shadcn/`@base-ui` or lightweight state). Add a **Progress** link to `components/NavHeader.tsx`.
- **`lib/types.ts`**: add `ProgressWeight`, `ProgressBodycomp`, `ProgressStrength`, `ProgressCardio`, `ConnectorHealth[]` matching the endpoint shapes.
- **`lib/api.ts`**: add `fetchProgressWeight/Bodycomp/Strength/Cardio()` + `fetchConnectorHealth()` via the existing `_get<T>` helper (same-origin proxy `app/api/[...path]/route.ts` → `API_BASE_URL_INTERNAL`).
- **`useProgressData()`** hook: fires the four fetches **in parallel**, exposes `{data, loading, error}` **per tab**; the page renders **per-card skeletons** and fills each tab as its call resolves — never blocks on the slowest.

### 10. Provenance (build fresh, preserve the semantics)
- Charts (Recharts) render: **solid dot = measured**, **hollow dot = estimated**, **dashed line = projected**; plus a **Body Comp banner** stating "one measured DEXA (Jul 8); everything else is projected until October." Model on the existing `GoalTrajectoryChart` observed(solid)/projected(dashed) line pattern, extended with the dot convention. A projection must never render as data.

### 11. Demo + pill
- `?demo=1` → the hook returns seeded static arrays (built from this spec's example JSON) for offline dev; no network.
- **Connector-status pill** in `NavHeader` (green all-ok / amber any-stale) from `fetchConnectorHealth()` — a real check, explicitly not the hardcoded `●ONLINE` pattern. One glance tells Hugo the pipeline is fed.

---

## 12. Testing
- **Backend** (pytest): each endpoint's shape + empty-case (empty arrays, not 500) + real-data KPI assertions — weight `current≈217.4`/`dewatered≈216.9`; strength `bench225/squat235/pulldown250/row260`; bodycomp one measured point `bf33.1`/`lean147.2` + `vat 145`; cardio shape. **Parser unit tests** on the five real `LIFTS` strings in §5 with exact expected loads. Connector-health status logic. Event-`id` presence. MCP shape-contract test after re-vendor.
- **Frontend** (vitest + playwright): `useProgressData` parallel/partial resolution + error state; fetchers; provenance rendering (dot styles); `/progress` e2e load + `?demo=1` renders offline; pill green/amber.

## 13. Deploy
- Phase 1: prod `alembic upgrade head` (the `vat_cm2` column) + backfill 145; deploy `backend`; smoke the 4 endpoints + `/health-events`; MCP re-vendor + restart. Phase 2: deploy; hit `/health/connectors`; confirm the scheduler job logs. Phase 3: dashboard auto-deploys from `main`; verify `/progress` live + pill + demo.

## 14. Out of scope
`interval_detail` per-interval 4×4 data; auth on read endpoints (single-user internal — noted, not built); dashboard write-back (writes stay on `log_*` tools).
