# Spec: Daily Session Brief + Auto-Regulation Engine

**Repo:** `growthink1/health-metrics-service`
**Spec path:** `docs/superpowers/specs/2026-05-26-session-brief-design.md`
**Author:** Hugo Delgado · drafted with claude.ai
**Sprint window:** 2026-05-27 → 2026-06-04 (5 PRs, ~1 Claude-week)
**Workflow:** `superpowers:subagent-driven-development` (implementer → spec review → Code Reviewer → fix → re-review per task)
**CI gates:** ruff · mypy-strict · pytest with 100% coverage on `app/regulation/`

---

## 1. Purpose

Move daily auto-regulation logic — the decision tree that maps Hugo's (and Andrea's) biometric state to a calorie / training prescription — out of the chat agent's head and into a deterministic, fixture-tested Python function exposed via HTTP. Two consumers depend on it:

- `mcp-unified-server` (claude.ai daily check-ins via `get_session_brief()` tool)
- `health.ironforgeai.com` dashboard (single-card "today's call" panel)

The current state — manual export + paste into chat + Claude re-derives the call each time — burns time and produces inconsistent outputs day to day. After this build, the agent reads one tool, the dashboard renders the same call, and the logic is testable against fixture days.

## 2. Invariants

Five invariants the system must hold across all PRs:

1. **Single source of truth for the regulation call.** `compute_regulation()` is the only function that produces a `RegulationCall`. Dashboard, MCP, and any future client all hit the same endpoint.
2. **Pure function for the engine.** `compute_regulation()` takes typed inputs, returns a typed output, no DB or network I/O. The brief endpoint composes it with data-fetch layers.
3. **Cache invalidation on every write that affects today.** Any POST to `manual_log`, `meals`, or `health_events` for today's date invalidates the cache. No stale brief reads.
4. **Confidence degrades with missing inputs, never silently.** Missing subjective markers, missing weight, missing Oura — each flips `confidence` from `high` to `medium` or `low` and appears in `missing_inputs`. The agent never receives "high confidence" against incomplete data.
5. **Health events gate everything.** Active `acute_infection` overrides recovery numbers. Pending `dental_procedure` within 14 days locks no-deficit mode. The engine consults `health_events` before consulting biometrics.

## 3. Schema

### 3.1 New tables

```sql
-- health_events: scheduled or active medical context that gates regulation
CREATE TABLE health_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'dental_procedure', 'acute_infection', 'antibiotic_course',
                        'fever', 'injury', 'scheduled_lab_draw',
                        'scheduled_dexa', 'scheduled_sleep_study'
                    )),
    status          TEXT NOT NULL CHECK (status IN ('active','pending','resolving','resolved')),
    started_at      DATE,
    expected_resolution DATE,
    affects         TEXT[] NOT NULL DEFAULT '{}',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX health_events_user_status_idx ON health_events (user_id, status);
CREATE INDEX health_events_expected_idx ON health_events (expected_resolution)
    WHERE status IN ('pending','active','resolving');

-- regulation_cache: post-ingest computed brief
CREATE TABLE regulation_cache (
    user_id         TEXT NOT NULL,
    as_of_date      DATE NOT NULL,
    brief_json      JSONB NOT NULL,
    cached_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latest_ingestion_at TIMESTAMPTZ NOT NULL,
    latest_write_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, as_of_date)
);
CREATE INDEX regulation_cache_cached_at_idx ON regulation_cache (cached_at);
```

### 3.2 Schema extensions

```sql
-- subjective markers on the manual_log table
ALTER TABLE manual_log
    ADD COLUMN IF NOT EXISTS soreness_1_10        SMALLINT,
    ADD COLUMN IF NOT EXISTS sleep_subjective_1_10 SMALLINT;

-- per-source status on daily_metrics
ALTER TABLE daily_metrics
    ADD COLUMN IF NOT EXISTS oura_status  TEXT NOT NULL DEFAULT 'ok',
    ADD COLUMN IF NOT EXISTS whoop_status TEXT NOT NULL DEFAULT 'ok';
```

### 3.3 Pydantic schemas

Full schemas live in `app/regulation/schemas.py`. Public shapes (the `SessionBrief` returned by the endpoint) match the structure used in the claude.ai design conversation — see §3.3 of that conversation for the field-by-field breakdown.

Key types: `RegulationState`, `TrainingModifier`, `RegulationCall`, `DailySnapshot`, `WorkoutSummary`, `TrendSummary`, `HealthEvent`, `Flag`, `WeightTrend`, `MissingInput`, `SessionBrief`.

## 4. Regulation engine

### 4.1 Decision tree (priority order, first match wins)

| Priority | Condition | State | Training | Kcal |
|---:|---|---|---|---:|
| 1 | `last_night_sleep < 240 min` OR `sleep_3d_avg < 300 min` | `MAINTENANCE_SLEEP_DEFICIT` | `Z2_ONLY` | 2,800 |
| 2 | Active `acute_infection` event | `MAINTENANCE_ILLNESS` | `REST` | 2,800 |
| 3 | Pending `dental_procedure` within 14 days | `MAINTENANCE_PRE_PROCEDURE` | `VOLUME_MINUS_20` (if recovery ≥60) else `Z2_ONLY` | 2,800 |
| 4 | HRV z₃ < −1.0 AND `consecutive_days_below_baseline ≥ 3` | `MAINTENANCE_HRV_DEPRESSION` | `VOLUME_MINUS_30_NO_HIIT` | 2,800 |
| 5 | `strain_7d_mean > 12` AND `recovery_today > 70` | `DEFICIT_CONSERVATIVE` | `FULL_NO_PROGRESSION` | 2,500 |
| 6 | History `< 14 days` (cold start) | `DEFICIT_CONSERVATIVE` | `FULL_NO_PROGRESSION` | 2,500 |
| 7 | Default (all green) | `DEFICIT` | `FULL_PROGRESSION` | 2,300 |

(Andrea targets: 2,400 / 2,150 / 2,000 — pass user_id into the kcal map.)

### 4.2 Overrides (additive, not exclusive)

Independent of the state above, the engine appends `overrides_today` entries that the agent/dashboard surface in the brief:

| Trigger | Override added |
|---|---|
| Pending dental procedure within 14d | `no_deficit_pre_procedure`, `no_z4_plus`, `rpe_cap_7`, `watch_jaw_load` |
| Workout in last 24h with `max_hr_pct_age_predicted ≥ 0.95` | `no_z4_plus` (today) |
| Antibiotic course active | `monitor_gi`, `hydration_plus` |
| Pre-extraction soft-food bridge (post-procedure within 7d) | `soft_food_only`, `no_overhead_press` |
| HRV z below −0.5 across 2 days (not yet 3) | `watchpoint_hrv` |

### 4.3 Confidence

`high` if all of: today's Oura present, today's Whoop present, ≥14 days history, subjective markers logged within last 48 h.
`medium` if any one of those is missing.
`low` if 2+ missing or cold-start.

## 5. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/session-brief?user_id=hugo` | Cache-aware brief read |
| POST | `/api/v1/manual-entry` | Log weight + subjective markers (invalidates cache) |
| POST | `/api/v1/meals` | Log a meal (invalidates cache) |
| POST | `/api/v1/health-events` | Create a health event (invalidates cache) |
| PATCH | `/api/v1/health-events/{id}` | Update status (invalidates cache) |
| GET | `/api/v1/workouts?user_id=&n_days=14` | Workout history |
| GET | `/api/v1/weight-trend?user_id=&n_days=30` | Weight + revealed TDEE |

All endpoints behind the existing Bearer-token auth. Dashboard token and MCP server token are separate principals so the audit trail can distinguish callers.

## 6. Post-ingest hook

```python
# app/ingestion/scheduler.py — pseudocode
@scheduler.scheduled_job("cron", hour=4, minute=30, timezone="America/New_York")
async def daily_ingestion():
    await ingest_oura()
    await ingest_whoop()
    # NEW:
    for user_id in ACTIVE_USERS:
        brief = await compute_session_brief(user_id, date.today())
        await write_regulation_cache(user_id, date.today(), brief)
```

Cache write is idempotent (PK on `(user_id, as_of_date)` → upsert). Endpoint reads cache; if validity check fails (see cache strategy section in claude.ai conversation), recomputes inline and writes back.

## 7. Acceptance criteria — fixture-driven

Each fixture is a JSON file in `tests/fixtures/days/` containing the exact `daily_metrics` row, recent workouts, active events, and trends for a single day. The engine is run against each and the output is asserted against expected fields.

| Fixture | Expected state | Expected training | Expected overrides ⊇ |
|---|---|---|---|
| `2026-05-17-infection-peak.json` (Recovery 6, RHR z +4.33, `acute_infection` active) | `MAINTENANCE_ILLNESS` | `REST` | `{no_training}` |
| `2026-05-24-sleep-crash.json` (sleep 4.65 h, Recovery 38) | `MAINTENANCE_SLEEP_DEFICIT` | `Z2_ONLY` | `{no_lift_today}` |
| `2026-05-26-pre-extraction.json` (Recovery 62, HRV z₃ +0.91, pending dental June 4) | `MAINTENANCE_PRE_PROCEDURE` | `VOLUME_MINUS_20` | `{no_deficit_pre_procedure, no_z4_plus, rpe_cap_7, watch_jaw_load}` |
| `2026-05-25-post-z5-spike.json` (max HR 171 yesterday, Recovery 74 today, pending dental June 4) | `MAINTENANCE_PRE_PROCEDURE` | `VOLUME_MINUS_20` | `{..., no_z4_plus}` (added by override) |
| `green-baseline-hugo.json` (no events, all signals green, 30d history) | `DEFICIT` | `FULL_PROGRESSION` | `{}` |
| `cold-start-andrea.json` (<14d history) | `DEFICIT_CONSERVATIVE` | `FULL_NO_PROGRESSION` | `{}` |
| `hrv-depression-3d.json` (z₃ −1.4, 4 consecutive days below baseline) | `MAINTENANCE_HRV_DEPRESSION` | `VOLUME_MINUS_30_NO_HIIT` | `{}` |

Add fixtures whenever a real day exposes a new edge case. Real metrics export rows go in `tests/fixtures/days/` verbatim — no synthesis of plausible-looking numbers.

## 8. Test plan

- `tests/regulation/test_engine.py` — pure function tests against all 7 fixtures above. 100% branch coverage on `engine.py`.
- `tests/regulation/test_cache.py` — cache validity logic: TTL, write-triggered invalidation, ingestion-triggered invalidation.
- `tests/regulation/test_brief_endpoint.py` — endpoint integration: auth, response shape, cache hit/miss paths.
- `tests/regulation/test_post_ingest_hook.py` — hook runs after ingestion completes, writes cache row, idempotent on re-run.
- `tests/api/test_manual_entry.py`, `test_health_events.py`, `test_meals.py` — write-path tests including cache-invalidation side effects.

CI gate: 100% coverage on `app/regulation/` (parallel to provenance-service standard from TQStarling Phase 1).

## 9. PR breakdown

5 PRs, each shippable independently. Each PR follows the subagent-driven-development workflow: implementer → spec review → Code Reviewer → fix → re-review.

| # | PR title | Scope | Blocks |
|---:|---|---|---|
| 1 | `feat(db): health_events + regulation_cache tables + manual_log subjective markers` | Alembic migration only. Manual backfill: insert Hugo's June 4 `dental_procedure` event in `pending` status. | All others |
| 2 | `feat(regulation): compute_regulation engine + fixtures + pytest` | Pure function + 7 fixtures + tests. No API yet. | PR 3 |
| 3 | `feat(api): /session-brief + cache + post-ingest hook` | Endpoint + cache strategy + scheduler wire. Unblocks MCP. | PR 4 (loosely) |
| 4 | `feat(api): write tools - manual_entry, meals, health_events` | POST/PATCH endpoints + cache invalidation. | — |
| 5 | `feat(api): trend endpoints - workouts, weight-trend, revealed_tdee` | Trend reads + revealed-TDEE calc. | Monthly review workflow |

## 10. Open decisions

Two open questions worth resolving before PR 1:

1. **z-score window.** Current export mixes 14-day baseline z (most rows) and 3-day rolling z (May 25, 26). Lock at **14-day rolling with 7-day warmup**. All ingestion + the engine read this single window. Migration step rewrites existing z values on the unified window.
2. **Couple-level brief.** Ship `get_session_brief(user)` per-user for v0.1. Add `get_couple_brief()` in a follow-up PR after both per-user briefs are stable. The couple brief adds cross-pollination flags (shared sleep deficit, shared training-day clash, etc.) but isn't on the critical path.

## 11. Non-goals (deferred)

- Apple Health / Fitbod ingestion → separate spec. `workout_sets` stays empty until that pipeline lands.
- VO2max calc → needs lab-grade test or a cleaner Peloton estimate ingestion path.
- DEXA integration → manual entry only for now; no DEXA file parser.
- Andrea-specific tweaks to the engine (perimenopause-aware thresholds) → ship Hugo-correct first, add Andrea overlay in a follow-up.
- Notifications / push alerts → out of scope; brief is read-on-demand.

## 12. Operational notes

- Manual backfill required after PR 1 migration: insert Hugo's pending `dental_procedure` event (started_at: 2026-05-23, expected_resolution: 2026-06-04, affects: `['no_deficit', 'no_z4_plus', 'rpe_cap_7', 'watch_jaw_load']`).
- Manual backfill optional: any prior `acute_infection` events (e.g., the May 14–18 tooth infection peak) for retroactive fixture matching.
- Railway env var to add: none new on health-metrics-service. mcp-unified-server gets `HEALTH_API_URL` + `HEALTH_API_TOKEN`.
- Dashboard env var: same `HEALTH_API_URL` + a separate `HEALTH_API_TOKEN_DASHBOARD` for audit-trail separation.

---

*Spec drafted via subagent-driven-development workflow. Acceptance gate = all 7 fixtures pass + 100% coverage on `app/regulation/`. Closes claude.ai daily-brief design loop.*
