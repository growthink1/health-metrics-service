"""Offline calibration for the energy model. Read-only.

Grid-searches (baseline_activity_factor, neat_coef) to minimize combined error
of modeled TDEE against whoop_kcal_burned AND the Kalman revealed_tdee over
recent history, then prints the recommended constants to paste into
energy_config.py. Run with DATABASE_URL set (public proxy for prod).

    python -m scripts.calibrate_energy --user hugo --days 30

No writes: every query below is a SELECT. `compute_energy_today` (brief.py)
doesn't accept a params override, so this script replicates its small
fetch+compute steps inline and calls the pure `energy.compute_energy` per
grid cell instead -- it's standalone tooling, not part of the request path.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from health_metrics.models import ActivityLog, BodyComposition, DailyMetrics, Workout
from health_metrics.regulation.body_composition import katch_mcardle_rmr
from health_metrics.regulation.brief import compute_weight_trend
from health_metrics.regulation.energy import Activity, compute_energy
from health_metrics.regulation.energy_config import EnergyParams, get_energy_params, normalize_activity_type

BASELINE_ACTIVITY_FACTORS = (1.30, 1.35, 1.40, 1.45)
NEAT_COEFS = (0.20, 0.30, 0.40, 0.53)


@dataclass(frozen=True)
class _DayContext:
    """Everything compute_energy needs for one historical day, fetched once
    and reused across every (factor, coef) grid cell."""

    day: date
    rmr_kcal: int
    rmr_source: str
    weight_lbs: float | None
    activities: list[Activity]
    whoop_kcal_burned: int | None
    whoop_complete: bool


async def _rmr_for_day(session: AsyncSession, user_id: str, as_of: date, fallback_rmr_kcal: int) -> tuple[int, str]:
    """Mirrors brief.compute_energy_today's RMR resolution: latest DEXA lean-mass
    row on/before as_of, else the per-user fallback constant."""
    r = await session.execute(
        select(BodyComposition.lean_mass_lbs)
        .where(
            BodyComposition.user_id == user_id,
            BodyComposition.lean_mass_lbs.is_not(None),
            BodyComposition.measured_date <= as_of,
        )
        .order_by(BodyComposition.measured_date.desc())
        .limit(1)
    )
    lean = r.scalar_one_or_none()
    if lean is not None:
        return katch_mcardle_rmr(float(lean)), "dexa"
    return fallback_rmr_kcal, "fallback"


async def _activities_for_day(session: AsyncSession, user_id: str, as_of: date) -> list[Activity]:
    """Mirrors brief._fetch_day_activities: union of auto workouts + manual
    activity_log for the day, normalized. Dedup happens inside compute_energy."""
    acts: list[Activity] = []

    wr = await session.execute(
        select(Workout.workout_type, Workout.duration_min, Workout.kcal).where(
            Workout.user_id == user_id,
            Workout.workout_date == as_of,
        )
    )
    for wtype, dur, kcal in wr.all():
        acts.append(
            Activity(
                activity_type=normalize_activity_type(wtype),
                source_layer="auto",
                distance_mi=None,
                duration_min=dur,
                kcal=float(kcal) if kcal is not None else None,
            )
        )

    ar = await session.execute(
        select(ActivityLog.activity_type, ActivityLog.distance_mi, ActivityLog.duration_min).where(
            ActivityLog.user_id == user_id,
            ActivityLog.activity_date == as_of,
        )
    )
    for atype, dist, dur in ar.all():
        acts.append(
            Activity(
                activity_type=normalize_activity_type(atype),
                source_layer="manual",
                distance_mi=float(dist) if dist is not None else None,
                duration_min=dur,
                kcal=None,
            )
        )
    return acts


async def _whoop_kcal_for_day(session: AsyncSession, user_id: str, as_of: date) -> int | None:
    r = await session.execute(
        select(DailyMetrics.whoop_kcal_burned).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date == as_of,
        )
    )
    return r.scalar_one_or_none()


async def _weight_for_day(session: AsyncSession, user_id: str, as_of: date) -> float | None:
    """Mirrors compute_session_brief's weight input to compute_energy_today:
    the Kalman-filtered weight as of that day, falling back to the raw value."""
    trend = await compute_weight_trend(session, user_id, as_of)
    return trend.filtered_weight_lbs or trend.current_lbs


async def _fetch_history(
    session: AsyncSession, user_id: str, days: int, fallback_rmr_kcal: int
) -> tuple[list[_DayContext], int | None]:
    """Returns (per-day contexts oldest->newest, the window's Kalman revealed_tdee)."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    day_range = [start + timedelta(days=i) for i in range(days)]

    window_trend = await compute_weight_trend(session, user_id, today, n_days=days)
    revealed_tdee = window_trend.revealed_tdee_kcal

    contexts: list[_DayContext] = []
    for d in day_range:
        rmr_kcal, rmr_source = await _rmr_for_day(session, user_id, d, fallback_rmr_kcal)
        activities = await _activities_for_day(session, user_id, d)
        weight_lbs = await _weight_for_day(session, user_id, d)
        whoop_kcal = await _whoop_kcal_for_day(session, user_id, d)
        whoop_complete = d < today  # today's whoop total is always partial
        contexts.append(
            _DayContext(
                day=d,
                rmr_kcal=rmr_kcal,
                rmr_source=rmr_source,
                weight_lbs=weight_lbs,
                activities=activities,
                whoop_kcal_burned=whoop_kcal,
                whoop_complete=whoop_complete,
            )
        )
    return contexts, revealed_tdee


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def _combined(rmse_whoop: float | None, rmse_revealed: float | None) -> float | None:
    """Equal-weight mean of the two error signals -- both whoop_kcal_burned and
    the Kalman revealed_tdee are ground-truth arbiters per the design spec."""
    vals = [v for v in (rmse_whoop, rmse_revealed) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _evaluate_cell(
    contexts: list[_DayContext],
    revealed_tdee: int | None,
    base_params: EnergyParams,
    factor: float,
    coef: float,
) -> tuple[float | None, float | None, float | None]:
    params = EnergyParams(
        baseline_activity_factor=factor,
        neat_coef=coef,
        fallback_rmr_kcal=base_params.fallback_rmr_kcal,
        divergence_pct=base_params.divergence_pct,
    )
    whoop_errors: list[float] = []
    revealed_errors: list[float] = []
    for ctx in contexts:
        energy = compute_energy(
            rmr_kcal=ctx.rmr_kcal,
            rmr_source=ctx.rmr_source,
            weight_lbs=ctx.weight_lbs,
            activities=ctx.activities,
            whoop_kcal_burned=ctx.whoop_kcal_burned,
            whoop_complete=ctx.whoop_complete,
            params=params,
        )
        modeled = energy.tdee_modeled_kcal
        if ctx.whoop_complete and ctx.whoop_kcal_burned is not None:
            whoop_errors.append(modeled - ctx.whoop_kcal_burned)
        if revealed_tdee is not None:
            revealed_errors.append(modeled - revealed_tdee)

    rmse_whoop = _rmse(whoop_errors)
    rmse_revealed = _rmse(revealed_errors)
    return rmse_whoop, rmse_revealed, _combined(rmse_whoop, rmse_revealed)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="hugo", help="User id (default: hugo).")
    parser.add_argument("--days", type=int, default=30, help="Trailing window size in days (default: 30).")
    parser.add_argument(
        "--min-whoop-kcal",
        type=int,
        default=0,
        help="Exclude complete days whose whoop_kcal_burned is below this floor "
        "(incomplete strap-wear guard; e.g. RMR*1.2). 0 = keep all.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", flush=True)
        return 1

    base_params = get_energy_params(args.user)
    print(f"[calib] user={args.user} days={args.days} (read-only, no writes)")

    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            contexts, revealed_tdee = await _fetch_history(session, args.user, args.days, base_params.fallback_rmr_kcal)
    finally:
        await engine.dispose()

    if args.min_whoop_kcal > 0:
        excluded = [
            c
            for c in contexts
            if c.whoop_complete and c.whoop_kcal_burned is not None and c.whoop_kcal_burned < args.min_whoop_kcal
        ]
        contexts = [c for c in contexts if c not in excluded]
        if excluded:
            days_s = ", ".join(f"{c.day.isoformat()}({c.whoop_kcal_burned})" for c in excluded)
            print(f"[calib] excluded {len(excluded)} day(s) below whoop floor {args.min_whoop_kcal}: {days_s}")

    revealed_s = str(revealed_tdee) if revealed_tdee is not None else "n/a"
    print(f"[calib] window Kalman revealed_tdee: {revealed_s} kcal")
    print(f"{'factor':>8}{'coef':>8}{'rmse_whoop':>14}{'rmse_revealed':>16}{'combined':>12}")

    results: list[tuple[float, float, float | None, float | None, float | None]] = []
    for factor in BASELINE_ACTIVITY_FACTORS:
        for coef in NEAT_COEFS:
            rmse_whoop, rmse_revealed, combined = _evaluate_cell(contexts, revealed_tdee, base_params, factor, coef)
            results.append((factor, coef, rmse_whoop, rmse_revealed, combined))
            rw_s = f"{rmse_whoop:.1f}" if rmse_whoop is not None else "n/a"
            rr_s = f"{rmse_revealed:.1f}" if rmse_revealed is not None else "n/a"
            comb_s = f"{combined:.1f}" if combined is not None else "n/a"
            print(f"{factor:>8.2f}{coef:>8.2f}{rw_s:>14}{rr_s:>16}{comb_s:>12}")

    ranked = [row for row in results if row[4] is not None]
    if not ranked:
        print("\nNo (factor, coef) cell had enough data to score -- insufficient history.")
        return 0

    best = min(ranked, key=lambda row: row[4])  # type: ignore[arg-type]
    print(
        f"\nRecommended: baseline_activity_factor={best[0]:.2f}, neat_coef={best[1]:.2f} (combined RMSE={best[4]:.1f})"
    )
    print("Paste into energy_config.py:")
    print(
        f"    EnergyParams(baseline_activity_factor={best[0]:.2f}, neat_coef={best[1]:.2f}, "
        f"fallback_rmr_kcal={base_params.fallback_rmr_kcal}, divergence_pct={base_params.divergence_pct})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
