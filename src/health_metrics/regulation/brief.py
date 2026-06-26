"""Build SessionBrief from raw queries.

Composes: data fetchers -> DailySnapshot -> compute_regulation -> SessionBrief.
"""

import math
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DailyMetrics, HealthEvent, ManualLog, Meal, Workout
from .engine import compute_regulation
from .glycogen import DayPoint, estimate_glycogen_water
from .glycogen_config import get_glycogen_params
from .kalman import kalman_weight
from .schemas import (
    DailySnapshot,
    Flag,
    HealthEventSnapshot,
    MissingInput,
    SessionBrief,
    WeightTrend,
    WorkoutSummary,
)

log = structlog.get_logger()

# User ages -- defer formal users table (plan §1 decision #4)
_USER_AGES: dict[str, int] = {"hugo": 44, "andrea": 40}


def _age_predicted_max_hr(user_id: str) -> int:
    age = _USER_AGES.get(user_id, 44)
    return 220 - age


async def _consecutive_days_below_baseline(session: AsyncSession, user_id: str, as_of: date_type) -> int:
    r = await session.execute(
        select(DailyMetrics.metric_date, DailyMetrics.unified_hrv_z)
        .where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date <= as_of,
        )
        .order_by(DailyMetrics.metric_date.desc())
        .limit(30)
    )
    n = 0
    for row in r:
        if row.unified_hrv_z is not None and float(row.unified_hrv_z) < 0:
            n += 1
        else:
            break
    return n


async def _sleep_3d_avg(session: AsyncSession, user_id: str, as_of: date_type) -> float | None:
    r = await session.execute(
        select(func.avg(DailyMetrics.oura_sleep_duration_min)).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date >= as_of - timedelta(days=2),
            DailyMetrics.metric_date <= as_of,
            DailyMetrics.oura_sleep_duration_min.is_not(None),
        )
    )
    v = r.scalar_one_or_none()
    return float(v) if v is not None else None


async def _strain_7d_mean(session: AsyncSession, user_id: str, as_of: date_type) -> float:
    r = await session.execute(
        select(func.avg(DailyMetrics.whoop_day_strain)).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date >= as_of - timedelta(days=6),
            DailyMetrics.metric_date <= as_of,
            DailyMetrics.whoop_day_strain.is_not(None),
        )
    )
    v = r.scalar_one_or_none()
    return float(v) if v is not None else 0.0


async def _last_workout_max_hr_pct(
    session: AsyncSession, user_id: str, as_of: date_type
) -> tuple[float | None, list[WorkoutSummary]]:
    r = await session.execute(
        select(Workout)
        .where(
            Workout.user_id == user_id,
            Workout.workout_date >= as_of - timedelta(days=7),
            Workout.workout_date <= as_of,
        )
        .order_by(Workout.workout_date.desc())
        .limit(5)
    )
    workouts = list(r.scalars().all())
    age_max = _age_predicted_max_hr(user_id)
    summaries = [
        WorkoutSummary(
            workout_date=w.workout_date,
            workout_type=w.workout_type,
            duration_min=w.duration_min,
            avg_hr=w.avg_hr,
            max_hr=w.max_hr,
            strain=float(w.strain) if w.strain is not None else None,
            kcal=w.kcal,
            max_hr_pct_age_predicted=(float(w.max_hr) / age_max) if w.max_hr else None,
        )
        for w in workouts
    ]
    yesterday = as_of - timedelta(days=1)
    yest = [w for w in workouts if w.workout_date == yesterday and w.max_hr is not None]
    if yest:
        return max(float(w.max_hr) for w in yest) / age_max, summaries
    return None, summaries


async def _active_events(session: AsyncSession, user_id: str) -> list[HealthEventSnapshot]:
    r = await session.execute(
        select(HealthEvent).where(
            HealthEvent.user_id == user_id,
            HealthEvent.status.in_(["active", "pending", "resolving"]),
        )
    )
    return [
        HealthEventSnapshot(
            event_type=ev.event_type,  # type: ignore[arg-type]
            status=ev.status,  # type: ignore[arg-type]
            expected_resolution=ev.expected_resolution,
            started_at=ev.started_at,
        )
        for ev in r.scalars().all()
    ]


async def _history_days_count(session: AsyncSession, user_id: str) -> int:
    r = await session.execute(
        select(func.count(func.distinct(DailyMetrics.metric_date))).where(
            DailyMetrics.user_id == user_id,
        )
    )
    return int(r.scalar_one() or 0)


async def _subjective_logged_within_48h(session: AsyncSession, user_id: str, as_of: date_type) -> bool:
    r = await session.execute(
        select(func.count())
        .select_from(ManualLog)
        .where(
            ManualLog.user_id == user_id,
            ManualLog.log_date >= as_of - timedelta(days=1),
            ManualLog.subjective_energy.is_not(None),
        )
    )
    return (r.scalar_one() or 0) > 0


def _downgrade_confidence(conf: str | None) -> str | None:
    """One-notch downgrade used when the regressor is skipped (sparse carb logs)."""
    return {"high": "medium", "medium": "low", "low": "low"}.get(conf or "", conf)


async def _fetch_carbs_and_workouts(
    session: AsyncSession, user_id: str, start: date_type, end: date_type
) -> tuple[dict[date_type, float], dict[date_type, list[tuple[str, float]]]]:
    """Daily carb totals (summed across meals) + per-day workout (type, strain)
    over [start, end]. Pure read; feeds the glycogen DayPoint series."""
    carb_rows = await session.execute(
        select(Meal.meal_date, func.sum(Meal.carbs_g))
        .where(
            Meal.user_id == user_id,
            Meal.carbs_g.is_not(None),
            Meal.meal_date >= start,
            Meal.meal_date <= end,
        )
        .group_by(Meal.meal_date)
    )
    carbs_by_date: dict[date_type, float] = {d: float(c) for d, c in carb_rows.all() if c is not None}

    wk_rows = await session.execute(
        select(Workout.workout_date, Workout.workout_type, Workout.strain).where(
            Workout.user_id == user_id,
            Workout.workout_date >= start,
            Workout.workout_date <= end,
        )
    )
    workouts_by_date: dict[date_type, list[tuple[str, float]]] = {}
    for wdate, wtype, strain in wk_rows.all():
        load = float(strain) if strain is not None else 1.0
        workouts_by_date.setdefault(wdate, []).append((wtype or "", load))

    return carbs_by_date, workouts_by_date


async def compute_weight_trend(session: AsyncSession, user_id: str, as_of: date_type, n_days: int = 14) -> WeightTrend:
    r = await session.execute(
        select(ManualLog.log_date, ManualLog.weight_lbs)
        .where(
            ManualLog.user_id == user_id,
            ManualLog.weight_lbs.is_not(None),
            ManualLog.log_date >= as_of - timedelta(days=n_days),
            ManualLog.log_date <= as_of,
        )
        .order_by(ManualLog.log_date.asc())
    )
    rows = [(d, float(w)) for d, w in r.all()]
    if not rows:
        return WeightTrend(n_days=n_days)
    current = rows[-1][1]
    delta = current - rows[0][1] if len(rows) > 1 else 0.0
    r2 = await session.execute(
        select(func.avg(ManualLog.kcal_consumed)).where(
            ManualLog.user_id == user_id,
            ManualLog.kcal_consumed.is_not(None),
            ManualLog.log_date >= as_of - timedelta(days=n_days),
            ManualLog.log_date <= as_of,
        )
    )
    avg_kcal = r2.scalar_one_or_none()

    # Phase 2: de-water with the glycogen regressor BEFORE the Kalman filter.
    # Build a DayPoint series over the full window from carb + workout logs, then
    # gate on carb-log coverage: if <50% of weight-days have a carb log, the
    # regressor is uninformative -> skip it and use the Phase 1 raw-weight Kalman
    # exactly (with a one-notch confidence downgrade).
    start = as_of - timedelta(days=n_days)
    carbs_by_date, workouts_by_date = await _fetch_carbs_and_workouts(session, user_id, start, as_of)

    weight_by_date = dict(rows)
    all_dates = sorted(set(weight_by_date) | set(carbs_by_date) | set(workouts_by_date))
    day_points = [
        DayPoint(
            date=d,
            weight_lbs=weight_by_date.get(d),
            carbs_g=carbs_by_date.get(d),
            workouts=workouts_by_date.get(d, []),
        )
        for d in all_dates
    ]

    total_weight_days = len(weight_by_date)
    carb_logged_weight_days = sum(1 for d in weight_by_date if d in carbs_by_date)
    use_regressor = total_weight_days > 0 and (carb_logged_weight_days / total_weight_days) >= 0.5

    glycogen_offset_lbs: float | None = None
    weight_dewatered_lbs: float | None = None

    if use_regressor:
        offsets = estimate_glycogen_water(day_points, get_glycogen_params(user_id))
        # Feed the de-watered observation series to the Kalman filter.
        dewatered_obs = [(o.date, o.weight_dewatered_lbs) for o in offsets]
        points = kalman_weight(dewatered_obs)
        # Surface the latest day that actually had a weight reading.
        final_offset = next((o for o in reversed(offsets) if o.weight_dewatered_lbs is not None), None)
        if final_offset is not None:
            glycogen_offset_lbs = final_offset.water_deviation_lbs
            weight_dewatered_lbs = final_offset.weight_dewatered_lbs
    else:
        # Phase 1 fallback: raw-weight Kalman, exactly as before.
        points = kalman_weight([(d, w) for d, w in rows])

    final = points[-1] if points else None
    filtered_weight = final.level if final else None
    filtered_velocity = final.velocity if final else None
    velocity_sigma = math.sqrt(final.velocity_var) if final else None

    # Confidence rule (from plan):
    #   low:    n < 14 OR velocity_sigma > 0.15 lb/day
    #   medium: 14-28 obs OR velocity_sigma 0.05-0.15
    #   high:   >=28 obs AND velocity_sigma < 0.05
    n_obs = len(rows)
    tdee_conf: str | None
    if n_obs < 14 or (velocity_sigma is not None and velocity_sigma > 0.15):
        tdee_conf = "low"
    elif n_obs < 28 or (velocity_sigma is not None and velocity_sigma > 0.05):
        tdee_conf = "medium"
    else:
        tdee_conf = "high"

    # Sparse-carb fallback downgrades confidence one notch (regressor skipped).
    if not use_regressor:
        tdee_conf = _downgrade_confidence(tdee_conf)

    # Revealed TDEE from filtered velocity (NOT endpoint delta)
    revealed_tdee: int | None = None
    if avg_kcal is not None and filtered_velocity is not None:
        revealed_tdee = int(float(avg_kcal) - (filtered_velocity * 3500.0))

    return WeightTrend(
        n_days=n_days,
        current_lbs=current,  # raw, untouched
        delta_lbs=delta,  # raw, preserved for reference
        revealed_tdee_kcal=revealed_tdee,
        filtered_weight_lbs=filtered_weight,
        filtered_velocity_lbs_per_day=filtered_velocity,
        revealed_tdee_confidence=tdee_conf,  # type: ignore[arg-type]
        glycogen_water_offset_lbs=glycogen_offset_lbs,
        weight_dewatered_lbs=weight_dewatered_lbs,
    )


async def compute_session_brief(session: AsyncSession, user_id: str, as_of: date_type) -> SessionBrief:
    r = await session.execute(
        select(DailyMetrics).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date == as_of,
        )
    )
    today_row = r.scalar_one_or_none()

    last_night_sleep = today_row.oura_sleep_duration_min if today_row else None
    recovery_today = today_row.whoop_recovery_score if today_row else None
    hrv_z_3d = float(today_row.unified_hrv_z) if (today_row and today_row.unified_hrv_z is not None) else None

    sleep_3d_avg = await _sleep_3d_avg(session, user_id, as_of)
    consecutive_below = await _consecutive_days_below_baseline(session, user_id, as_of)
    strain_7d_mean = await _strain_7d_mean(session, user_id, as_of)
    last_hr_pct, workouts = await _last_workout_max_hr_pct(session, user_id, as_of)
    active_events = await _active_events(session, user_id)
    history_days_count = await _history_days_count(session, user_id)
    subjective_48h = await _subjective_logged_within_48h(session, user_id, as_of)
    weight_trend = await compute_weight_trend(session, user_id, as_of)

    snap = DailySnapshot(
        user_id=user_id,
        as_of_date=as_of,
        last_night_sleep_min=last_night_sleep,
        sleep_3d_avg_min=sleep_3d_avg,
        recovery_today=recovery_today,
        hrv_z_3d=hrv_z_3d,
        consecutive_days_below_baseline=consecutive_below,
        strain_7d_mean=strain_7d_mean,
        last_workout_max_hr_pct_age_predicted=last_hr_pct,
        active_events=active_events,
        history_days_count=history_days_count,
        oura_present_today=(today_row is not None and today_row.oura_sleep_duration_min is not None),
        whoop_present_today=(today_row is not None and today_row.whoop_recovery_score is not None),
        subjective_logged_within_48h=subjective_48h,
    )
    call = compute_regulation(snap)

    missing: list[MissingInput] = []
    if not snap.oura_present_today:
        missing.append(
            MissingInput(
                field="oura_today",
                impact="confidence_degrades",
                message="Oura ring data missing for today",
            )
        )
    if not snap.whoop_present_today:
        missing.append(
            MissingInput(
                field="whoop_today",
                impact="confidence_degrades",
                message="Whoop strap data missing for today",
            )
        )
    if snap.history_days_count < 14:
        missing.append(
            MissingInput(
                field="history_days_count",
                impact="engine_skipped_rule",
                message=f"Only {snap.history_days_count} days of history",
            )
        )
    if not snap.subjective_logged_within_48h:
        missing.append(
            MissingInput(
                field="subjective_48h",
                impact="confidence_degrades",
                message="No subjective markers logged in last 48h",
            )
        )

    flags: list[Flag] = []
    if "watchpoint_hrv" in call.overrides_today:
        flags.append(
            Flag(
                code="watchpoint_hrv",
                severity="watch",
                message="HRV trending below baseline 2+ days",
            )
        )
    if "no_z4_plus" in call.overrides_today:
        flags.append(
            Flag(
                code="no_z4_plus",
                severity="watch",
                message="Avoid Z4+ effort today",
            )
        )

    return SessionBrief(
        as_of_date=as_of,
        user_id=user_id,
        regulation_call=call,
        daily_snapshot=snap,
        recent_workouts=workouts,
        weight_trend=weight_trend,
        active_events=active_events,
        flags=flags,
        missing_inputs=missing,
        confidence=call.confidence,
        generated_at=datetime.now(UTC),
    )
