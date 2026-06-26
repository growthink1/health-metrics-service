# Training-Water Retention Model — Design Spec

**Date:** 2026-06-26
**Repo:** `growthink1/health-metrics-service`
**Supersedes:** Phase 2 glycogen-water regressor (PR #9, closed unmerged)
**Builds on:** Phase 1 Kalman TDEE filter (merged `8fdef41`, deployed prod 2026-06-26)
**Workflow:** `superpowers:subagent-driven-development`. CI gates: ruff + format + pytest, 100% line/branch on the new module. Conventional commits, NO co-author trailer.

---

## 1. Why this exists — the Phase 2 finding

Phase 2 modeled bodyweight water as glycogen bound to logged carbohydrate. Validation against Hugo's real Jun 20–26 data showed the carb model explains only ~15–20% of the observed swing and **cannot physically explain the episode**: a 199g carb day (67g above the fitted ~132g maintenance) binds ≤0.3 lb of water through a 3–4.5 g-water-per-g-glycogen ratio, but the observed swing was +1.8 lb (≈6× larger). PR #9's optimizer scored a clean 0.46 overfit ratio while producing a model that de-watered nothing — the fit objective (minimize de-watered curvature) was a proxy disconnected from the goal.

Re-examining the episode against **workouts instead of carbs** is decisive:

```
Jun 20  222.4   functional-fitness (strain 12.8) + walks
Jun 21  222.7   REST, 199g carbs                 ← carb spike: weight barely moved
Jun 22  224.0   functional-fitness (strain 14.8) ← +1.3 lb, morning AFTER hard training
Jun 23  223.5   rest
Jun 24  224.0   functional-fitness + walk
Jun 25  224.2   cycling (strain 12.2)            ← plateau spans the training run
Jun 26  222.4   REST                              ← whoosh on the first full rest day
```

The plateau spans the consecutive training days; the whoosh lands on the first rest day. Weight tracks **training-induced water retention** (inflammation, repair, glycogen repletion, creatine) — a 24–72h acute retention — not dietary carbs. Phase 2's model had the training sign *backwards*: it subtracted water for training (chronic glycogen burn) on exactly the days that retained it.

## 2. The model

Each workout deposits a water bolus that decays exponentially. Pure training-driven; no carb term.

```
training_water_lbs(t) = Σ_{sessions s, day_s ≤ t}   k · load_s · e^(−λ · (t − day_s))
```

- **`k`** — gain, lb per load-unit. **`λ`** — decay rate, per day. These two are the only fitted params.
- **`load_s`** — the session's `strain` (already logged). Missing strain → per-type fallback constant (functional-fitness 12, cycling 11, walking 5, activity 8, `yard-work` 6, default 8).
- We subtract the **deviation**, not the absolute level:
  ```
  baseline   = mean(training_water_lbs over the window)
  offset(t)  = training_water_lbs(t) − baseline
  ```
- **Priors (Hugo, starting constants in `water_retention_config.py`):** `k ≈ 0.05` (a strain-14 functional-fitness session deposits ~0.7 lb), `λ = ln2 / 2 ≈ 0.3466` (~2-day half-life). Per-user. Andrea = same defaults, flagged `# TODO: validate against Andrea's data`.

## 3. Architecture & files

| Path | Action | Responsibility |
|---|---|---|
| `src/health_metrics/regulation/water_retention.py` | Create | Pure kernel. `training_water_series(workouts_by_day, dates) -> list[DayWater]` + `clears_by(today, sessions, params, threshold=0.2) -> date \| None`. No I/O. |
| `src/health_metrics/regulation/water_retention_config.py` | Create | Per-user `WaterRetentionParams(k, λ)` + per-type strain fallback dict. `get_water_params(user_id)`. |
| `src/health_metrics/regulation/brief.py` | Modify | `compute_weight_trend`: compute offset, run the runtime gate, conditionally de-water the Kalman input, always set annotation + forecast. Add `_fetch_workouts_by_day` helper. |
| `src/health_metrics/regulation/schemas.py` | Modify | `WeightTrend` gains 3 fields (see §5). |
| `scripts/validate_water_retention.py` | Create | Offline priors-validation against the held-out Jun 20–26 episode. Bounded-grid fallback. Prints accept/reject + the raw-vs-dewatered table. Not in the request path. |
| `tests/regulation/test_water_retention.py` | Create | 5 tests (§6). 100% line+branch on the new module. |
| `tests/fixtures/days/glycogen-real-hugo-2026-06-26.json` | Reuse | Real joined series (38 days, 22 weight-days, 28 carb-days). Already committed on this branch. |

Phase 2 artifacts (`glycogen.py`, `glycogen_config.py`, `scripts/fit_glycogen_params.py`, `tests/regulation/test_glycogen.py`) are NOT brought onto this branch. **PR #9 is closed unmerged** with a comment linking this spec.

## 4. Pipeline integration — gated de-watering (the Phase 2 lesson encoded)

Phase 2 de-watered the Kalman input unconditionally and shipped a regression. Here the filter only changes when it provably helps, checked live per request — not at fit time.

In `compute_weight_trend`:

1. **Always** compute `training_water_series` over the window → today's `offset` + `clears_by`. Set `training_water_offset_lbs` and `training_water_clears_by` unconditionally (annotation always ships when ≥1 workout in window).
2. Build a de-watered observation series `[(date, weight − offset_dev)]`, run it through `kalman_weight`, and compare its **velocity-variance** against the raw-weight Kalman over the same window.
3. **Gate:** if `dewatered_velocity_var ≤ raw_velocity_var × 0.95` (≥5% improvement) → use the de-watered filter for `revealed_tdee` + set `weight_dewatered_lbs`. Else → keep raw filtering, `weight_dewatered_lbs = None`. **No confidence penalty** — annotation-only is a valid state, not a degraded one (`revealed_tdee_confidence` unchanged from Phase 1's logic).
4. **No-workout window:** offset/forecast `None`, raw Kalman, no crash.

`current_lbs` stays the raw latest reading, untouched.

## 5. Output contract

`WeightTrend` adds:

```python
training_water_offset_lbs: float | None = None   # today's retention deviation; +ve = above baseline
weight_dewatered_lbs: float | None = None         # raw − offset_dev; only when the gate passes
training_water_clears_by: date | None = None      # date the kernel decays below 0.2 lb
```

These let the coaching layer say: *"≈1.3 lb of today's reading is post-training water — back to baseline by Thursday, don't react to it."* The forecast turns a scale-panic moment into a non-event and costs nothing (the decay curve already exists).

## 6. Fitting / validation — priors-first, no unconstrained optimizer

No optimizer in the request path or the build. Priors are validated **once, offline** (`scripts/validate_water_retention.py`):

- Fit window: 2026-05-20 → 06-19. Held-out: 2026-06-20 → 06-26 (the episode).
- **Acceptance gate:** with prior `(k, λ)`, the de-watered Jun 20–26 series must show a lower max day-over-day increase than raw (raw = 1.3 lb) AND the whoosh (last-day drop) must shrink in magnitude. If priors pass → ship them.
- **If priors fail:** coarse bounded grid over `k ∈ [0.02, 0.08]` × `λ ∈ [0.2, 0.6]` (step 0.01 / 0.05), pick the gentlest point clearing the gate. **No unconstrained optimizer, ever** — that is the specific Phase 2 failure mode.
- **If even the grid fails:** report it honestly and ship annotation-only (the §4 runtime gate auto-disables de-watering anyway). Do not relax the test to manufacture a pass.

## 7. Tests (5 required)

1. **Kernel shape** — single strain-14 session → offset peaks same-day, decays below 0.2 lb by the day implied by `λ`. Deterministic.
2. **Episode reconstruction (headline, out-of-sample)** — apply fit-window priors to the held-out Jun 20–26 window from the fixture; assert de-watered max day-over-day increase < raw 1.3 lb AND whoosh shrinks. **Honest gate:** if priors+grid can't clear it, the test asserts the annotation-only fallback and the finding is reported — never relaxed to green.
3. **Runtime gate** — synthetic window where de-watering helps → `weight_dewatered_lbs` set + de-watered TDEE used; window where it doesn't → `None`, raw TDEE unchanged. Exercises both gate branches.
4. **`clears_by` forecast** — known session + `λ` → correct decay-to-0.2 date.
5. **No-workout window** — offset/forecast `None`, clean fallback to raw Kalman, no crash.

100% line+branch on `water_retention.py`. `engine.py` stays 100%. Full suite 216 → ~221.

## 8. Engine invariants (unchanged)

- `compute_regulation()` remains the sole producer of `RegulationCall`. Water retention touches only `compute_weight_trend`.
- Pure compute path: `water_retention.py` is I/O-free; validation is offline.
- Atomic cache invalidation unchanged.
- Per-user-fittable: no shared hardcoded constants in the engine — params live in `water_retention_config.py[user_id]`.

## 9. Non-goals / deferred

- **Carb-glycogen term** — dropped. Re-add only if a future high-carb refeed (400g+) shows a glycogen signal the training kernel can't explain, with evidence.
- **Per-session attribution** in the output — deferred; the single offset + forecast is enough to act on.
- **Sodium term** (former Phase 3) — deferred; training water is the dominant, most-predictable term on this data.
- **Andrea's params** — defaults shipped, flagged; needs her own validation once she has ≥3 weeks of weight+workout logs.

## 10. PR hygiene

- Branch `feature/training-water-retention` off `main` (`8fdef41`).
- PR description: raw-vs-dewatered Jun 20–26 table (the proof), the validated `(k, λ)`, and whether priors passed or fell to the grid.
- Close PR #9 with a comment pointing to this spec.

---

*Supersedes the glycogen-water hypothesis with a training-retention model after PR #9 validation showed logged carbs cannot explain the episode amplitude. The data points at training, not diet, as the water driver.*
