"""Glycogen-water regressor (Phase 2).

Removes the predictable, carb-driven component of bodyweight water swing BEFORE
the Phase 1 Kalman filter sees it. Glycogen binds ~3g water per gram; carb
surplus/deficit moves glycogen, training depletes it. We log both, so this
component is predictable, not noise.

Pure functions — no DB, no network. The param fit is offline
(scripts/fit_glycogen_params.py); fitted params live in glycogen_config.py.

# Phase 3 (future): add a sodium term for high-sodium-meal transients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type


@dataclass(frozen=True)
class GlycogenParams:
    alpha: float  # fraction of surplus/deficit carb that moves glycogen
    carb_maintenance: float  # carbs (g) at which glycogen holds steady
    beta: float  # g water per g glycogen
    g_min: float  # physiological glycogen floor (g)
    g_max: float  # physiological glycogen ceiling (g)
    tier_scale: float  # global multiplier on the depletion tier dict
    g_init: float = 450.0  # starting glycogen estimate (g)


@dataclass(frozen=True)
class DayPoint:
    """One day of input for the regressor."""

    date: date_type
    weight_lbs: float | None
    carbs_g: float | None
    workouts: list[tuple[str, float]] = field(default_factory=list)  # (type, load_proxy)


@dataclass(frozen=True)
class DayOffset:
    date: date_type
    glycogen_g: float
    water_offset_lbs: float  # absolute β·G in lb
    water_deviation_lbs: float  # deviation from window-baseline (what we subtract)
    weight_dewatered_lbs: float | None  # observed − deviation; None if no weight that day


# Workout type → grams-of-glycogen-per-load-unit. tier_scale multiplies all of these.
DEPLETION_TIERS: dict[str, float] = {
    "functional-fitness": 8.0,
    "cycling": 6.0,
    "activity": 4.0,
    "walking": 2.0,
    "yard-work": 3.0,
    "weightlifting": 6.0,
    "weightlifting_msk": 6.0,
}
_DEFAULT_TIER = 4.0

_LBS_PER_GRAM = 1.0 / 453.6


def _daily_depletion(workouts: list[tuple[str, float]], tier_scale: float) -> float:
    total = 0.0
    for wtype, load in workouts:
        tier = DEPLETION_TIERS.get(wtype, _DEFAULT_TIER)
        total += tier * load
    return total * tier_scale


def estimate_glycogen_water(series: list[DayPoint], params: GlycogenParams) -> list[DayOffset]:
    """Walk the series day-by-day, accumulating glycogen + computing the water
    offset. Subtract the window-mean offset so we remove the DEVIATION (swing),
    not the absolute level. Missing carbs → treat as neutral (carb_maintenance)
    so glycogen holds — the caller flags low confidence separately."""
    if not series:
        return []

    g = params.g_init
    raw_offsets: list[tuple[date_type, float, float | None]] = []  # (date, water_lbs, weight)
    for day in series:
        carbs = day.carbs_g if day.carbs_g is not None else params.carb_maintenance
        depletion = _daily_depletion(day.workouts, params.tier_scale)
        g = g + params.alpha * (carbs - params.carb_maintenance) - depletion
        g = max(params.g_min, min(params.g_max, g))
        water_lbs = params.beta * g * _LBS_PER_GRAM
        raw_offsets.append((day.date, water_lbs, day.weight_lbs))

    baseline = sum(w for _, w, _ in raw_offsets) / len(raw_offsets)

    out: list[DayOffset] = []
    for d, water_lbs, weight in raw_offsets:
        deviation = water_lbs - baseline
        dewatered = (weight - deviation) if weight is not None else None
        out.append(
            DayOffset(
                date=d,
                glycogen_g=water_lbs / (params.beta * _LBS_PER_GRAM),  # back out G for transparency
                water_offset_lbs=water_lbs,
                water_deviation_lbs=deviation,
                weight_dewatered_lbs=dewatered,
            )
        )
    return out


# Param bounds in optimizer order [alpha, carb_maint, beta, g_min, g_max, tier_scale].
# Hard physiological limits — out-of-bounds is penalized (Nelder-Mead is unconstrained).
_FIT_BOUNDS: list[tuple[float, float]] = [
    (0.1, 0.8),
    (80.0, 200.0),
    (2.0, 4.5),
    (200.0, 400.0),
    (450.0, 700.0),
    (0.5, 2.0),
]


def _curvature_residual(pts: list[DayPoint], p: GlycogenParams) -> float:
    """2nd-difference (curvature) energy of the de-watered weight series. Low =
    clean linear trend. Returns a large sentinel if fewer than 3 de-watered points."""
    import numpy as np

    offs = estimate_glycogen_water(pts, p)
    vals = [o.weight_dewatered_lbs for o in offs if o.weight_dewatered_lbs is not None]
    if len(vals) < 3:
        return 1e6
    arr = np.array(vals)
    second_diff = np.diff(arr, n=2)
    return float(np.sum(second_diff**2))


def _fit_objective(x, fit_series: list[DayPoint]) -> float:
    """Penalized curvature objective for Nelder-Mead over the 6 fit params.

    The bounds penalty also enforces g_min < g_max: g_min's range [200,400] and
    g_max's range [450,700] are disjoint, so any in-bounds point already has
    g_min < g_max — no separate ordering guard is needed."""
    for xi, (lo, hi) in zip(x, _FIT_BOUNDS, strict=True):
        if xi < lo or xi > hi:
            return 1e9 + sum(abs(v) for v in x)
    p = GlycogenParams(alpha=x[0], carb_maintenance=x[1], beta=x[2], g_min=x[3], g_max=x[4], tier_scale=x[5])
    return _curvature_residual(fit_series, p)


def fit_params(
    series: list[DayPoint],
    holdout_dates: set[date_type] | None = None,
) -> tuple[GlycogenParams, float, float]:
    """Fit params by minimizing the 2nd-difference (curvature) of the de-watered
    weight series on the FIT window (series minus holdout). Returns
    (params, fit_residual, holdout_residual). scipy Nelder-Mead with bounds as
    penalty. Offline use only — never in the request path."""
    import numpy as np
    from scipy.optimize import minimize

    holdout = holdout_dates or set()
    fit_series = [d for d in series if d.date not in holdout]
    hold_series = [d for d in series if d.date in holdout]

    x0 = np.array([0.45, 135.0, 3.0, 300.0, 600.0, 1.0])

    res = minimize(
        _fit_objective,
        x0,
        args=(fit_series,),
        method="Nelder-Mead",
        options={"maxiter": 2000, "xatol": 1e-3, "fatol": 1e-3},
    )
    p = GlycogenParams(
        alpha=res.x[0], carb_maintenance=res.x[1], beta=res.x[2], g_min=res.x[3], g_max=res.x[4], tier_scale=res.x[5]
    )
    fit_resid = _curvature_residual(fit_series, p)
    hold_resid = _curvature_residual(hold_series, p) if hold_series else fit_resid
    return p, fit_resid, hold_resid
