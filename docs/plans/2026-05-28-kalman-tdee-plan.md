# Kalman-Filtered TDEE Plan (Phase 1)

> **Workflow:** `superpowers:subagent-driven-development` (implementer → spec review → Code Reviewer → fix → re-review).
> **CI gates:** ruff + ruff format + pytest with 100% line/branch coverage on the new filter module.
> **Conventional commits, NO co-author trailer.**

**Spec:** Provided in-line by Hugo 2026-05-28 (chat message — preserved verbatim in PR description).
**Goal:** Stop computing `revealed_tdee_kcal` from raw endpoint weight delta — that's water-contaminated. Replace with a 1-D Kalman filter (constant-velocity / local-linear-trend) that produces a smoothed level + velocity, and compute TDEE from the filtered velocity.

**Repo:** `~/code/health-metrics-service`. **Branch:** `feature/kalman-tdee` off `main` at `bf31812`. Single PR.

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `src/health_metrics/regulation/kalman.py` | Create | Pure-numpy 1-D Kalman filter (local linear trend). `kalman_weight(observations) -> list[FilteredPoint]` returning level + velocity + variance per day. No I/O. ~60 lines. |
| `src/health_metrics/regulation/schemas.py` | Modify | Extend `WeightTrend` with `filtered_weight_lbs`, `filtered_velocity_lbs_per_day`, `revealed_tdee_confidence` ("high"\|"medium"\|"low"). Keep `current_lbs` as raw. |
| `src/health_metrics/regulation/brief.py` | Modify | `compute_weight_trend` swaps endpoint-delta math for Kalman-filtered slope. Velocity × 3500 = daily energy deficit → TDEE = avg_intake − (filtered_velocity × 3500). Confidence rule from spec: variance high or window <14d → "low". |
| `tests/regulation/test_kalman.py` | Create | 4 unit tests covering the filter math (synthetic recovery, gap handling, cold-start, variance gating). 100% coverage on `kalman.py`. |
| `tests/regulation/test_brief_kalman_integration.py` | Create | Characterization test on a checked-in 69-day synthetic-realistic fixture asserting std-dev reduction >60% vs the raw-endpoint method. Reproduces the prod whipsaw (3923↔2602) and proves the fix kills it. |
| `tests/fixtures/days/kalman-real-shape-69d.json` | Create | 69-day fixture: linear decline + ±1.5 lb noise + 3 random gap days. Synthetic, no PHI. |

## Filter parameters (starting values, tune in tests)

```
State:        x = [weight_level (lb), weight_velocity (lb/day)]
Transition:   F = [[1, 1], [0, 1]]
Observation:  H = [1, 0]
Process Q:    diag(q_level=0.01, q_vel=0.0005)
Obs noise R:  r_obs = 1.0          # ±1 lb daily water swing as 1-sigma; key trust knob
Init:         x0 = [first_obs, 0], P0 = diag(2.0, 0.1)
```

Missing days: predict-only step (no update). Gaps don't break the filter.

## Confidence rule (added per spec §"Engine invariants")

- `revealed_tdee_confidence = "low"` if window has <14 days of observations OR filtered velocity 1-sigma > 0.15 lb/day (sparse data → high uncertainty in slope)
- `revealed_tdee_confidence = "medium"` if window has 14–28 days AND velocity sigma 0.05–0.15
- `revealed_tdee_confidence = "high"` if window has ≥28 days AND velocity sigma < 0.05

This is a NEW field on `WeightTrend` — does NOT degrade the overall `SessionBrief.confidence` from §4.3 (which is about input completeness, not estimator variance).

## Invariants honored

1. `compute_regulation()` remains the SOLE producer of `RegulationCall`. Kalman touches only `compute_weight_trend` (a brief-builder helper), not the engine.
2. Pure engine — no I/O in `kalman.py`. `compute_weight_trend` is the I/O orchestrator.
3. Cache invalidation atomic — unchanged, no write path touched.
4. Confidence on the regulation call unchanged. New `revealed_tdee_confidence` is a SEPARATE field about TDEE quality.

## Cross-repo impact: `growthink1/mcp-unified-server`

Vendored `WeightTrend` at `tools/health_metrics_types.py` will silently drop the 3 new fields (Pydantic v2 default `extra='ignore'`). Acceptable for Phase 1 — Claude can still call `get_session_brief` and see `revealed_tdee_kcal`. **Follow-up:** re-vendor schemas at SHA bump to surface new fields in claude.ai's view.

## Tests

1. **`test_kalman_recovers_velocity_under_noise`** — synthetic 30-day linear decline at -0.25 lb/day + IID gaussian noise σ=1.5 lb → assert recovered velocity within ±0.05 lb/day of truth; raw-endpoint method off by >800 kcal; filter within ±100.
2. **`test_kalman_handles_gaps`** — same series with 3 random days dropped → assert no NaN/raise, velocity recovered within ±0.07 lb/day.
3. **`test_kalman_cold_start_low_confidence`** — 7-day window → `revealed_tdee_confidence == "low"`.
4. **`test_kalman_variance_decreases_with_more_data`** — variance(filtered_velocity) on day 30 < variance on day 10.
5. **`test_brief_kalman_integration`** — 69-day fixture with bimodal noise (high-carb day water spikes) → filtered `revealed_tdee` std-dev / raw `revealed_tdee` std-dev < 0.4 (i.e. >60% reduction).

100% line + branch coverage on `kalman.py`. Existing engine.py 100% gate stays green.

## Phase 2 (NOT in this PR — open as GitHub issue)

Carb-driven glycogen-water regressor. Estimate glycogen-water from rolling carb intake (`meals.carbs_g`) minus training depletion (workout volume/strain). Subtract from observed weight BEFORE the Kalman update — removes the predictable component of water rather than smoothing it. Already-existing inputs.

Implementer opens a tracked issue via `gh issue create` BEFORE opening this PR, referencing the Phase 2 spec.

## PR description (template)

PR description must include:
- 69-day fixture before/after table or ascii plot showing raw vs filtered `revealed_tdee` series
- Computed std-dev reduction percentage
- Reference to the Phase 2 issue
- Confirmation: 211 → 211 + 5 = 216 tests; engine.py 100% maintained; new module 100%.

## Acceptance

PR opens, all 4 gates green (pytest, engine cov, ruff, ruff format), Phase 2 issue created and linked in PR description. Stop after PR opens. Do not auto-merge — Hugo reviews the variance-reduction table.
