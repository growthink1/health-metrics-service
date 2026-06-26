# Glycogen-Water Regressor Plan (Phase 2)

> **Workflow:** `superpowers:subagent-driven-development`. CI gates: ruff + format + pytest, 100% line/branch coverage on the new `glycogen.py` module. Conventional commits, NO co-author trailer.
> **Prerequisite MET:** Phase 1 (Kalman filter) merged at `8fdef41` + deployed to prod 2026-06-26. Hugo waived the 2-week bake to proceed immediately.

**Spec:** Hugo's verbatim Phase 2 spec (2026-06-26 chat) + GitHub issue #7. Preserved in PR description.
**Goal:** Estimate a per-day glycogen-water offset from carb + training logs, subtract its *deviation* from observed weight to produce a "de-watered" weight, feed THAT into the Phase 1 Kalman filter. De-water at the source instead of smoothing.
**Repo/branch:** `~/code/health-metrics-service`, `feature/glycogen-water-regressor` off `main` at `8fdef41`. Single PR.

## Real-data reality (verified against prod 2026-06-26)

Committed fixture `tests/fixtures/days/glycogen-real-hugo-2026-06-26.json`: 38 calendar days (2026-05-20..06-26), **22 weight-days, 28 carb-days**. Workout types present: `activity, cycling, functional-fitness, walking, yard-work`.

**The Jun 21–26 episode (the proof case) is fully populated:**
```
Jun 20: 222.4 lb  carbs  92g  walk×2 + functional-fitness(strain 12.8)
Jun 21: 222.7 lb  carbs 199g  ← carb spike
Jun 22: 224.0 lb  carbs  96g  functional-fitness(strain 14.8)   +1.6 lb water
Jun 23: 223.5 lb  carbs 140g
Jun 24: 224.0 lb  carbs 173g  functional-fitness + walk
Jun 25: 224.2 lb  carbs 195g  cycling(strain 12.2)
Jun 26: 222.4 lb  carbs  —    ← 1.8 lb whoosh (224.2 → 222.4)
```

**Data is thin for a 5–6 param fit.** Adaptation: fit on **May 20–Jun 19**, hold out **Jun 20–26** as out-of-sample validation. The headline reconstruction test then validates the model on a window it never saw during fitting — a far stronger claim than fitting on the episode. The overfit guard (Test 4) is the load-bearing gate given the small sample.

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `src/health_metrics/regulation/glycogen.py` | Create | Pure-numpy/scipy glycogen accumulator + water-offset + param-fit. No I/O. `estimate_glycogen_water(series, params) -> list[DayOffset]` + `fit_params(series, holdout_days) -> GlycogenParams`. |
| `src/health_metrics/regulation/glycogen_config.py` | Create | Per-user fitted `GlycogenParams` + the workout→depletion tier dict. Keyed by user_id; `hugo` fitted, `andrea` = defaults (needs own fit — documented). |
| `src/health_metrics/regulation/brief.py` | Modify | `compute_weight_trend` subtracts glycogen-water deviation before calling `kalman_weight`. Graceful fallback to Phase 1 raw-weight Kalman when carb/workout logs are sparse in the window. |
| `src/health_metrics/regulation/schemas.py` | Modify | `WeightTrend` gains `glycogen_water_offset_lbs: float\|None` + `weight_dewatered_lbs: float\|None`. |
| `tests/regulation/test_glycogen.py` | Create | 5 required tests (below). 100% coverage on `glycogen.py`. |
| `tests/fixtures/days/glycogen-real-hugo-2026-06-26.json` | Create (DONE) | Already exported. Real joined series. Hugo's private repo + his explicit request to use real logs for Test 1 → privacy OK. |

## Model (per spec)

### Glycogen accumulator (per day)
```
G_t = clamp( G_{t-1} + α·(carbs_t − carb_maintenance) − depletion_t , G_min, G_max )
```
- `carbs_t` = logged daily carb sum (None → treat as carb_maintenance, i.e. neutral, AND flag the day as low-confidence)
- `carb_maintenance` = carb intake at which glycogen holds steady (fit; prior ~120–150g)
- `α` = fraction of surplus/deficit carb that moves glycogen (fit; prior ~0.3–0.6)
- `depletion_t` = Σ over that day's workouts of `tier(workout_type) · load_proxy`. `load_proxy` = `strain` if present else 1.0.
- `G_min, G_max` = physiological bounds (fit within hard limits; e.g. 250–650g)

### Workout → depletion tier (config dict, grams-per-strain-unit, tunable)
Starting tiers (the implementer fits/tunes; keep as config in `glycogen_config.py`):
```
functional-fitness : 8.0   # glycolytic, depletes hard
cycling            : 6.0
activity           : 4.0
walking            : 2.0
yard-work          : 3.0
weightlifting      : 6.0   # legacy type, keep for older data
weightlifting_msk  : 6.0
DEFAULT            : 4.0
```
Depletion grams = tier × strain (strain ≈ 5 walk, ≈ 12–15 functional-fitness → walk depletes ~10g, functional-fitness ~100–120g; tune so magnitudes land in the physiological 50–150g/session range).

### Water offset
```
glycogen_water_g_t   = β · G_t           # β ≈ 3.0 g water / g glycogen (fit, 2.5–4.0)
glycogen_water_lbs_t = glycogen_water_g_t / 453.6
```

### De-watered weight (Kalman input)
```
baseline = mean(glycogen_water_lbs over the window)     # subtract DEVIATION, not absolute
weight_dewatered_t = observed_weight_t − (glycogen_water_lbs_t − baseline)
```

## Fitting (`fit_params`)
`scipy.optimize.minimize(method="Nelder-Mead")` over `[α, carb_maintenance, β, G_min, G_max, tier_scale]` (a single scalar `tier_scale` multiplying the whole depletion dict keeps the fit dim low — 6 params, not 8). Objective: **residual variance of the de-watered weight series on the FIT window** (May 20–Jun 19) — choose params that make de-watered weight smoothest/most-monotonic given a steady deficit. Use a 2nd-difference penalty (curvature) so the optimizer prefers a clean linear trend, not just low variance.

Hard bounds (reject overfit): `α ∈ [0.1,0.8]`, `carb_maintenance ∈ [80,200]`, `β ∈ [2.0,4.5]`, `G_min ∈ [200,400]`, `G_max ∈ [450,700]`, `tier_scale ∈ [0.5,2.0]`. Implement as a penalty in the objective (return large value if out of bounds) since Nelder-Mead is unconstrained.

Fit happens OFFLINE (a script the implementer runs once) → the resulting params are written as constants into `glycogen_config.py` for `hugo`. Do NOT fit at request time (keeps the compute path pure + fast). The fit script lives at `scripts/fit_glycogen_params.py` (committed, runnable, but not in the request path).

## Graceful degradation (spec invariant — never hard-fail)
In `compute_weight_trend`: count carb-logged days in the window. If `< 50%` of weight-days have carb logs, SKIP the regressor entirely and use Phase 1 raw-weight Kalman (exactly today's behavior). Set `glycogen_water_offset_lbs = None`, `weight_dewatered_lbs = None`, and downgrade `revealed_tdee_confidence` one notch (high→medium, medium→low). Document the threshold.

## Engine invariants (unchanged)
- `compute_regulation()` sole producer of `RegulationCall`. Glycogen touches only `compute_weight_trend`.
- Pure compute path: `glycogen.py` is I/O-free. The fit script is offline.
- Atomic cache invalidation unchanged.
- Per-user-fittable: NO shared hardcoded constants in the engine — params live in `glycogen_config.py[user_id]`. Andrea = defaults + a `# TODO: fit Andrea's params` note.

## Tests (5 required, per spec)

1. **`test_reconstructs_jun21_26_episode`** (HEADLINE) — load the real fixture, fit on May 20–Jun 19, run the regressor on the held-out Jun 20–26 window. Assert the de-watered weight series is monotone-decreasing within tolerance (no 224 plateau, no whoosh): `weight_dewatered[Jun26] < weight_dewatered[Jun20]` AND max day-over-day *increase* in the de-watered series < 0.4 lb (vs the raw +1.6 lb Jun21→22 spike). Out-of-sample.
2. **`test_dewatered_tdee_lower_variance_than_phase1`** — revealed_tdee from de-watered series has lower variance than Phase 1 raw-weight Kalman over the same fixture. Assert ≥20% additional variance reduction (issue #7 target ≥30% is the prod goal; 20% is the test gate given thin data).
3. **`test_missing_carb_logs_falls_back_to_phase1`** — feed a window where <50% of weight-days have carbs → assert `glycogen_water_offset_lbs is None`, `weight_dewatered_lbs is None`, result equals Phase 1 Kalman output, confidence downgraded, NO crash.
4. **`test_fitted_params_within_physiological_bounds`** (OVERFIT GUARD) — assert fitted params are inside the hard bounds AND validation-window (Jun 20–26) residual ≤ 1.5× fit-window residual.
5. **`test_glycogen_offset_signed_correctly`** — synthetic: high-carb day → positive offset; post-HIIT/low-carb day → negative offset relative to baseline.

100% line+branch on `glycogen.py`. Engine.py stays 100%. Full suite 216 → ~221.

## Cross-repo
`mcp-unified-server` vendored `WeightTrend` silently ignores the 2 new fields (Pydantic `extra='ignore'`). Same follow-up as Phase 1 (re-vendor at SHA bump). Not blocking.

## PR hygiene
- Branch `feature/glycogen-water-regressor`, stacked on merged Phase 1.
- PR description: before/after table of Jun 20–26 — raw weight (222.4→224.2→222.4 plateau+whoosh) vs de-watered (smooth decline) — the proof chart, as an ascii table from the test output.
- Note params are fit to Hugo specifically; Andrea needs her own fit (per-user-fittable, no shared constants).
- Phase 3 (sodium term) noted as out-of-scope in PR + a one-line `# Phase 3` comment in glycogen.py.

## Acceptance
PR opens, 4 gates green, headline episode-reconstruction test passes out-of-sample, overfit guard green. Stop after PR opens. Hugo reviews the before/after table before merge.
