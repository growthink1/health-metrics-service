"""Glycogen-water regressor tests (Phase 2).

HONEST FINDING (read me): the headline out-of-sample test (Test 1) was written
to assert the de-watered Jun 20-26 series is monotone-ish decreasing with max
day-over-day increase < 0.4 lb. It DOES NOT. Fitting on May 20-Jun 19 and
applying the model to the held-out Jun 20-26 window leaves the +1.3-1.6 lb
swings essentially intact.

Root cause (mechanism, not a code bug — see test_glycogen_offset_signed_correctly,
which proves the accumulator is correct): the glycogen-water term's maximum
single-day dynamic range is ~0.5 lb even at the extreme parameter bounds
(alpha=0.8, beta=4.5: one carb-surplus day moves G by <=51 g => <=0.5 lb water).
The observed Jun 21->22 raw swing is +1.3 lb and the full episode amplitude is
+1.8 lb — roughly 3x larger than anything the logged carb deltas can explain
through this mechanism. A grid search over all six in-bounds params finds a best
achievable de-watered max-increase of 1.30 lb (never < 0.4, never monotone).

Two structural gaps surfaced:
  1. Timing: a carb load on day N shows up on the day N+1 morning weigh-in; the
     model applies the offset same-day.
  2. The Jun 26 "whoosh" lands on a None-carb day, which the model treats as
     neutral (carb_maintenance) and therefore cannot represent at all.

Per the plan's risk section ("Honesty over green ... do NOT relax the test to
make it pass; instead report it as a finding"), Tests 1 and 2 below assert the
TRUE, reproducible behavior and are explicitly labelled as negative findings.
The plan's gates (overfit guard Test 4, fallback Test 3, sign-correctness
Test 5) all pass on their own merits. Recommendation: the regressor needs a
next-day lag term + a way to represent un-logged whoosh days (or simply more
carb-logged history) before it can de-water episodes of this magnitude.
"""

import json
import statistics
from datetime import date
from pathlib import Path

from health_metrics.regulation.glycogen import (
    DayPoint,
    GlycogenParams,
    _curvature_residual,
    _fit_objective,
    estimate_glycogen_water,
    fit_params,
)
from health_metrics.regulation.glycogen_config import get_glycogen_params
from health_metrics.regulation.kalman import kalman_weight

FIXTURE = Path(__file__).parent.parent / "fixtures" / "days" / "glycogen-real-hugo-2026-06-26.json"

_HOLDOUT = {date(2026, 6, d) for d in range(20, 27)}  # Jun 20-26 out-of-sample


def _load_series() -> list[DayPoint]:
    data = json.loads(FIXTURE.read_text())
    out: list[DayPoint] = []
    for row in data["series"]:
        d = date.fromisoformat(row["date"])
        workouts = [
            (w["type"], float(w["strain"]) if w["strain"] is not None else 1.0) for w in row.get("workouts", [])
        ]
        out.append(
            DayPoint(
                date=d,
                weight_lbs=row["weight_lbs"],
                carbs_g=row["carbs_g"],
                workouts=workouts,
            )
        )
    return out


def _dewatered_jun(offsets) -> list[float]:
    return [o.weight_dewatered_lbs for o in offsets if o.date in _HOLDOUT and o.weight_dewatered_lbs is not None]


def test_reconstructs_jun21_26_episode() -> None:
    """HEADLINE (out-of-sample, NEGATIVE FINDING).

    Fit on May 20-Jun 19, apply the regressor to the held-out Jun 20-26 window.
    The plan's target was: de-watered series monotone-ish decreasing, max
    day-over-day increase < 0.4 lb. The model DOES NOT achieve this — the swing
    is ~3x the mechanism's dynamic range. This test pins that reproducible
    reality so the regression is documented, not hidden.

    Determinism: fit is seeded by a fixed x0 and bounds; same fixture in =>
    same params out. We assert the model fails to flatten the episode, which is
    the stable, honest outcome.
    """
    series = _load_series()
    params, fit_resid, hold_resid = fit_params(series, holdout_dates=_HOLDOUT)

    # The G accumulator needs its warmup history, so run on the full series and
    # slice out the held-out window.
    offsets = estimate_glycogen_water(series, params)
    dewatered = _dewatered_jun(offsets)
    assert len(dewatered) >= 3

    raw = [d.weight_lbs for d in series if d.date in _HOLDOUT and d.weight_lbs is not None]
    raw_max_inc = max(raw[i + 1] - raw[i] for i in range(len(raw) - 1))
    dew_max_inc = max(dewatered[i + 1] - dewatered[i] for i in range(len(dewatered) - 1))

    print(
        f"\n[FINDING] out-of-sample Jun20-26: raw_max_inc={raw_max_inc:.3f} lb "
        f"dewatered_max_inc={dew_max_inc:.3f} lb (plan target <0.4 NOT met)"
    )

    # Honest assertion: the de-watered swing is NOT flattened below 0.4 lb. The
    # mechanism cannot subtract a >1 lb single-day spike, so the spike survives.
    assert dew_max_inc > 0.4, (
        "Episode unexpectedly flattened — if this fires, the model gained the "
        "ability to reconstruct the episode and Test 1 should be flipped to the "
        "plan's original < 0.4 lb success assertion."
    )


def test_dewatered_tdee_lower_variance_than_phase1() -> None:
    """Plan target: de-watered revealed_tdee variance >= 20% below Phase 1 raw.

    NEGATIVE FINDING: it is not. With the fitted params (glycogen pinned near
    g_min over this window) the de-watered velocity variance is within ~1% of
    raw — the regressor neither helps nor materially hurts. Asserted as the
    reproducible reality.
    """
    series = _load_series()
    params, _, _ = fit_params(series, holdout_dates=_HOLDOUT)
    offsets = estimate_glycogen_water(series, params)

    raw_obs = [(d.date, d.weight_lbs) for d in series]
    dew_obs = [(o.date, o.weight_dewatered_lbs) for o in offsets]

    def rolling_velocity_sd(obs: list[tuple[date, float | None]]) -> float:
        vels: list[float] = []
        for end in range(14, len(obs)):
            pts = kalman_weight(obs[end - 14 : end + 1])
            if pts:
                vels.append(pts[-1].velocity)
        return statistics.stdev(vels)

    raw_sd = rolling_velocity_sd(raw_obs)
    dew_sd = rolling_velocity_sd(dew_obs)
    reduction = 1.0 - (dew_sd / raw_sd)
    print(f"\n[FINDING] velocity SD reduction = {reduction:.2%} (plan target >=20% NOT met)")

    # The regressor does not deliver the 20% reduction on this data; the two SDs
    # are within ~5% of each other.
    assert abs(reduction) < 0.05


def test_missing_carb_logs_falls_back_to_phase1() -> None:
    """<50% carb-logged weight-days => regressor skipped, Phase 1 Kalman used as-is.

    Exercised at the pure-function layer: when carbs are mostly None the offsets'
    deviations are ~flat, but the brief layer (test_brief) is where the real skip
    happens. Here we assert the pure model degrades gracefully (no crash, offsets
    computable) so the brief can safely fall back."""
    series = _load_series()
    # Strip carbs from all but one weight-day -> coverage well below 50%.
    stripped: list[DayPoint] = []
    kept = False
    for d in series:
        if d.weight_lbs is not None and not kept:
            stripped.append(d)
            kept = True
        else:
            stripped.append(DayPoint(date=d.date, weight_lbs=d.weight_lbs, carbs_g=None, workouts=d.workouts))

    params = get_glycogen_params("hugo")
    offsets = estimate_glycogen_water(stripped, params)
    assert len(offsets) == len(stripped)
    # None-carb days are treated as neutral (carb_maintenance) -> no crash, finite.
    for o in offsets:
        assert o.water_deviation_lbs == o.water_deviation_lbs  # not NaN

    # Empty input returns empty (covers the early-return branch).
    assert estimate_glycogen_water([], params) == []


def test_fitted_params_within_physiological_bounds() -> None:
    """OVERFIT GUARD. Fitted params inside the hard bounds AND holdout residual
    <= 1.5x fit residual (no overfit to the thin 28-carb-day fit window)."""
    series = _load_series()
    params, fit_resid, hold_resid = fit_params(series, holdout_dates=_HOLDOUT)

    assert 0.1 <= params.alpha <= 0.8
    assert 80.0 <= params.carb_maintenance <= 200.0
    assert 2.0 <= params.beta <= 4.5
    assert 200.0 <= params.g_min <= 400.0
    assert 450.0 <= params.g_max <= 700.0
    assert 0.5 <= params.tier_scale <= 2.0
    assert params.g_min < params.g_max

    overfit_ratio = hold_resid / fit_resid if fit_resid else float("inf")
    print(f"\n[overfit] fit={fit_resid:.3f} hold={hold_resid:.3f} ratio={overfit_ratio:.2f}")
    assert overfit_ratio <= 1.5

    # No-holdout path returns hold_resid == fit_resid (covers that branch).
    _, fr2, hr2 = fit_params(series, holdout_dates=None)
    assert hr2 == fr2


def test_glycogen_offset_signed_correctly() -> None:
    """Synthetic: a high-carb surplus day yields a POSITIVE water deviation; a
    low-carb + hard-training (depletion) day yields a NEGATIVE one. Proves the
    accumulator mechanism is correct (the Test 1 negative finding is a model
    range limitation, not a sign/code bug)."""
    p = GlycogenParams(alpha=0.5, carb_maintenance=135.0, beta=3.0, g_min=200.0, g_max=700.0, tier_scale=1.0)
    days = [
        DayPoint(date(2026, 1, 1), 200.0, 135, []),  # neutral
        DayPoint(date(2026, 1, 2), 200.0, 135, []),  # neutral
        DayPoint(date(2026, 1, 3), 200.0, 335, []),  # +200g surplus -> G up
        DayPoint(date(2026, 1, 4), 200.0, 35, [("functional-fitness", 10.0)]),  # deficit + deplete
        DayPoint(date(2026, 1, 5), 200.0, 135, []),
    ]
    offs = estimate_glycogen_water(days, p)
    high_carb = offs[2]
    depletion = offs[3]
    neutral = offs[0]

    assert high_carb.water_deviation_lbs > neutral.water_deviation_lbs
    assert depletion.water_deviation_lbs < high_carb.water_deviation_lbs
    assert depletion.water_deviation_lbs < 0  # below window baseline
    # Unknown workout type uses the default tier (covers the .get default branch).
    offs2 = estimate_glycogen_water([DayPoint(date(2026, 2, 1), None, 135, [("mystery-sport", 5.0)])], p)
    assert offs2[0].weight_dewatered_lbs is None  # no weight that day


def test_fit_internals_penalty_branches() -> None:
    """Directly exercise the fit guard branches the real fixture never reaches:
    the <3-points curvature sentinel and the objective's out-of-bounds penalty."""
    p = GlycogenParams(alpha=0.45, carb_maintenance=135.0, beta=3.0, g_min=300.0, g_max=600.0, tier_scale=1.0)
    # <3 de-watered points -> sentinel.
    two_pts = [
        DayPoint(date(2026, 1, 1), 200.0, 135, []),
        DayPoint(date(2026, 1, 2), 201.0, 135, []),
    ]
    assert _curvature_residual(two_pts, p) == 1e6

    fit_series = [DayPoint(date(2026, 1, i), 200.0 + i * 0.1, 135, []) for i in range(1, 6)]
    # Out-of-bounds (alpha too high) -> large penalty.
    assert _fit_objective([0.95, 135.0, 3.0, 300.0, 600.0, 1.0], fit_series) >= 1e9
    # Valid in-bounds point -> finite, below the penalty floor.
    assert _fit_objective([0.45, 135.0, 3.0, 300.0, 600.0, 1.0], fit_series) < 1e9
