# Engine Item #6 — Structured Activity Logging + Daily Energy (NEAT/TDEE) Model

**Date:** 2026-07-13
**Repos:** `health-metrics-service` (backend) + `mcp-unified-server` (tool surface)
**Priority:** Below Items #1 (weight_dewatered) and #2 (false-green) — both shipped. Additive capability, not a bug fix. This is the single change that makes the Jul 22 "+2,500 steps/day" NEAT-escalation lever *measurable same-day* instead of only visible weeks later via the weight slope.

---

## 1. Problem & the reframe

The original ask: "walks and rides have nowhere to live except free-text `notes`; the TDEE anchor (~2,950 = Katch-McArdle RMR × static 1.55) ignores step count and logged activity, so same-day NEAT is invisible."

Exploration of the actual prod data (Jul 6–13) **partially falsified the premise** and reshaped the design:

- **Walks already reach the `workouts` table via Whoop** as `workout_type='walking'` (Jul 6/7/9/10) with duration + kcal. They are *structured today* — just not fed into any calculation.
- **`daily_metrics.whoop_kcal_burned` is a measured daily expenditure** — 2604/2650/2979/2698/2855/2459/2653 for Jul 6–12 → real measured TDEE averaging **~2,700**, i.e. **~250 below the ~2,950 mental anchor**.
- **No body-composition data exists in the DB** (no body-fat %, lean mass, height, age, sex). Katch-McArdle RMR therefore *cannot be computed in-code today*.
- **Strava is not an ingestion source** — every `workouts` row is `source='whoop'`. Strava-only walk days (Whoop missed Jul 8/11/12/13) genuinely live only in `notes`.
- **The "1.55 × RMR = 2,950" model does not exist in code.** The engine uses hard-coded state→kcal maps (2800/2400). The only "TDEE" in code is `revealed_tdee_kcal` (Kalman weight-slope, retrospective).

The real gaps, then:
1. **No structured home for manual/Strava activity** the automated ingestion misses (Part A).
2. **The TDEE readout ignores same-day activity entirely** (Part B).
3. **RMR is an un-homed magic number** — solved by giving DEXA a structured home (Part A2).

## 2. Locked decisions (from brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| 1 | TDEE anchor | **Blend both, calibrated to revealed-TDEE** — surface measured (Whoop) + modeled (RMR+NEAT); calibrate the model against `whoop_kcal_burned` AND the Kalman `revealed_tdee`. |
| 2 | NEAT source | **Union of `workouts` + `activity_log`, deduped by `(date, type)`** — manual entry wins when both exist. Escalation visible even with zero manual logging; Strava-only days filled manually. |
| 3 | Calibration | **Offline one-time tuning → config constants + validator script** (mirrors `get_water_params` + the water-retention validator). Re-run manually when body comp shifts. |
| 4 | RMR source | **Add a `body_composition` (DEXA) table now.** RMR computed via Katch-McArdle from the latest row; graceful fallback until a reading exists. |
| 5 | Headline | **`tdee_estimate_today` = modeled number** (calibrated), with `tdee_measured` (Whoop) shown alongside + a **divergence flag** at >10%. No literal averaging — it would re-contaminate the calibrated signal. |

## 3. Architecture (follows existing patterns)

New units, each single-purpose and independently testable, mirroring `water_retention.py` / `water_retention_config.py` / the offline validator:

```
regulation/
  energy.py            # pure: compute_energy_today(...) -> EnergyToday; NEAT dedup+aggregation; blend
  energy_config.py     # per-user constants: baseline_activity_factor, neat_coef, fallback_rmr_kcal
  body_composition.py  # katch_mcardle_rmr(lean_mass_lbs) + latest-reading resolver
scripts/
  calibrate_energy.py  # offline: tune constants against whoop_kcal_burned + revealed_tdee history
routes/
  activities.py            # POST /api/v1/activities  (insert-only, cache invalidate)
  body_composition.py      # POST /api/v1/body-composition (insert-only, cache invalidate)
```

The engine (`engine.py`) stays **I/O-free and untouched** (Invariant #2). All new computation lives in the brief layer (`compute_session_brief`), exactly like `compute_weight_trend`.

## 4. Part A — structured activity

### 4.1 `activity_log` table (migration)
| col | type | notes |
|-----|------|-------|
| id | BigInteger PK | |
| user_id | Text NOT NULL | |
| activity_date | Date NOT NULL | |
| activity_type | Text NOT NULL | CHECK ∈ walk/run/ride/z2/hiit/strength/climb/other |
| distance_mi | Numeric(6,2) NULL | |
| duration_min | Integer NULL | |
| elevation_ft | Integer NULL | |
| avg_hr | Integer NULL | |
| max_hr | Integer NULL | |
| strain | Numeric(4,2) NULL | |
| source | Text NOT NULL | CHECK ∈ strava/whoop/peloton/manual/api |
| notes | Text NULL | |
| created_at | timestamptz default NOW() | |

Index `idx_activity_log_user_date (user_id, activity_date)`. **No unique constraint** — multiple activities per day allowed (a walk AND a Z2 ride both log). The auto-ingested `workouts` table is left untouched; `activity_log` is the manual/Strava layer.

### 4.2 `POST /api/v1/activities` + `log_activity` MCP tool
Insert-only (no upsert). Mirrors `log_meal` conventions: returns the created row incl. `id`; invalidates the day's session-brief cache on write. MCP signature: `log_activity(activity_date, activity_type, distance_mi?, duration_min?, elevation_ft?, avg_hr?, max_hr?, strain?, source?, notes?, user_id='hugo')`.

### 4.3 Backfill
Parse `Strava: X mi, MM:SS, Y ft` fragments from `manual_log.notes` for Jul 6–13, **only for days Whoop did not already capture a walk** (candidates: Jul 8/11/12/13) to avoid seeding double-counts. Skip anything not cleanly extractable. **The parsed rows are shown to the user for confirmation before insertion.**

## 5. Part A2 — body composition (DEXA)

### 5.1 `body_composition` table (migration)
| col | type | notes |
|-----|------|-------|
| id | BigInteger PK | |
| user_id | Text NOT NULL | |
| measured_date | Date NOT NULL | |
| source | Text NOT NULL | CHECK ∈ dexa/bioimpedance/hydrostatic/manual |
| weight_lbs | Numeric(5,2) NULL | total mass at scan |
| body_fat_pct | Numeric(4,1) NULL | |
| lean_mass_lbs | Numeric(5,2) NULL | fat-free / lean mass |
| fat_mass_lbs | Numeric(5,2) NULL | |
| notes | Text NULL | |
| created_at | timestamptz default NOW() | |

Index `idx_body_comp_user_date (user_id, measured_date)`. `POST /api/v1/body-composition` + `log_body_composition` MCP tool (insert-only, cache invalidate).

### 5.2 Katch-McArdle
`rmr_kcal = 370 + 21.6 × lean_mass_kg`, where `lean_mass_kg = lean_mass_lbs / 2.2046`. Resolver uses the **most recent** `body_composition` row with a non-null `lean_mass_lbs` as of the brief date. **Backfill** the one known DEXA reading referenced in the Jul 8 note (surfaced for user confirmation first).

## 6. Part B — energy model

### 6.1 `energy_config.py` (per-user constants)
- `baseline_activity_factor` — sedentary desk baseline, **~1.35** (not 1.55 — logged activity becomes explicit rather than baked-in).
- `neat_coef` — walk/run net-of-resting kcal per mile per lb, seed **~0.53**, **calibrated offline**.
- `fallback_rmr_kcal` — used only when no `body_composition` row exists yet.
- `divergence_pct` — measured-vs-modeled flag threshold, **0.10**.

### 6.2 NEAT term (`energy.py`)
NEAT = activity kcal above the sedentary baseline, from the **deduped union of `workouts` + `activity_log`** for the day, keyed on `(activity_date, normalized_type)` with **manual (`activity_log`) winning** when both exist. Per activity, best-available estimate, in priority order:
1. **Measured kcal** when present — Whoop `workouts.kcal` (the real, primary source for auto-captured activity).
2. **Distance formula** for distance-only walks/runs: `distance_mi × weight_lbs × neat_coef` (net-of-resting).
3. Duration-only / typed activities without distance or kcal: a per-type kcal/min fallback (calibrated; conservative).

(An `avg_hr`-driven refinement is possible later but is **not** built now — see §10.)

Type normalization maps `workouts.workout_type` values (`walking`, `cycling`, `functional-fitness`) onto the `activity_type` enum for dedup.

### 6.3 The three numbers
- `tdee_measured_kcal` = `whoop_kcal_burned` (real; undercounts walk NEAT; excluded when the day is partial/absent — e.g. today's 807).
- `tdee_modeled_kcal` = `rmr_kcal × baseline_activity_factor + neat_kcal`.
- `tdee_estimate_kcal` = **modeled** (headline). `divergence_flag = |measured − modeled| / modeled > divergence_pct` when measured is present.

### 6.4 Calibration (`scripts/calibrate_energy.py`)
Offline, like the water-retention validator: over the logged history, fit `neat_coef` + `baseline_activity_factor` so `tdee_modeled` tracks **both** `whoop_kcal_burned` and the Kalman `revealed_tdee` (revealed-TDEE is the long-run ground-truth arbiter). Emit the fitted constants + residuals; hard-code the constants into `energy_config.py`. No runtime auto-calibration (YAGNI, non-deterministic).

## 7. Surfacing + MCP

New `EnergyToday` sub-object on `SessionBrief`:
```
EnergyToday{
  neat_kcal: float | None
  baseline_kcal: int | None            # rmr × baseline_activity_factor
  rmr_kcal: int | None
  tdee_measured_kcal: int | None       # whoop
  tdee_modeled_kcal: int | None
  tdee_estimate_kcal: int | None       # headline = modeled
  divergence_flag: bool
  activities_counted: list[str]        # e.g. ["walk 2.7mi (activity_log)", "ride 41min (workouts)"]
  rmr_source: Literal["dexa","fallback"]
}
energy_today: EnergyToday | None  # on SessionBrief; None when no RMR + no activity resolvable
```
Exposes the spec's `neat_kcal_today` / `tdee_estimate_today` (as `neat_kcal` / `tdee_estimate_kcal`) plus the blend detail. Wired in `compute_session_brief`; cache-invalidated by both new write paths.

**MCP:** add `log_activity` + `log_body_composition` tools (mirror the write-wrapper pattern); re-vendor `health_metrics_types.py` to pick up `EnergyToday`; restart the local MCP server so claude.ai sees the new tools/fields.

## 8. Testing

- `log_activity` round-trips a walk; row queryable by date + type; two activities same day both persist.
- **Dedup:** a day with both a Whoop `walking` workout and a manual `walk` in `activity_log` counts NEAT **once** (manual wins).
- `log_body_composition` round-trips; Katch-McArdle returns a sane RMR (~1,900–2,100 for Hugo's lean mass); resolver picks the latest reading.
- A 2.7 mi walk → `neat_kcal ≈ 110–125`; `tdee_estimate_kcal` lands near the calibrated anchor on a normal day (not ~3,075 double-count).
- A sedentary / no-activity day shows a lower `tdee_estimate_kcal` than an active day.
- `divergence_flag` fires when measured and modeled disagree >10%; false when they agree.
- Offline calibration reproduces `whoop_kcal_burned` + `revealed_tdee` within tolerance on real history.
- Full suite green; new modules at the coverage bar the repo enforces (engine gate untouched).

## 9. Migration & deploy

Two additive migrations (`activity_log`, `body_composition`) — new tables + indexes + CHECKs only, no changes to existing tables. Deploy order: prod `alembic upgrade head` **before** the code deploy (the brief's `compute_energy_today` reads both tables — but tolerates their emptiness, so ordering is low-risk). Then `railway up`, smoke-test the new endpoints + `energy_today` in the live brief, invalidate hugo's brief cache, re-verify. (Note: deploys still don't auto-bust the brief cache — tracked separately.)

## 10. Out of scope / deferred

- **Runtime auto-calibration** (rejected — YAGNI, non-deterministic).
- **Moving `revealed_tdee` onto the de-watered series** (Item #4 D2 — separate validated PR).
- **HR-based NEAT for every activity** — only used when `avg_hr` is present; no HR-model build-out.
- **A general body-composition dashboard/trend** — table + logging only for now; trends later if wanted.
- **Auto-ingesting Strava** — remains a manual layer; a Strava source integration is a future item.
