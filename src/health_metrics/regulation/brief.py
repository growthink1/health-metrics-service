"""Build SessionBrief from raw queries.

Composes: data fetchers -> DailySnapshot -> compute_regulation -> SessionBrief.
"""

import math
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ActivityLog, BodyComposition, DailyMetrics, HealthEvent, ManualLog, Workout
from .body_composition import katch_mcardle_rmr
from .energy import Activity, compute_energy
from .energy_config import get_energy_params, normalize_activity_type
from .engine import compute_regulation
from .kalman import kalman_weight
from .overrides import apply_overrides, fetch_active_overrides
from .schemas import (
    DailySnapshot,
    EnergyToday,
    Flag,
    HealthEventSnapshot,
    MissingInput,
    SessionBrief,
    WeightTrend,
    WorkoutSummary,
)
from .water_retention import clears_by, training_water_series
from .water_retention_config import get_water_params

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


async def _fetch_loads_by_day(
    session: AsyncSession, user_id: str, start: date_type, end: date_type
) -> dict[date_type, float]:
    """Summed training load per day over [start, end]. Load = strain when present,
    else a per-type fallback constant. Multiple workouts on a day are summed."""
    from .water_retention_config import fallback_load

    r = await session.execute(
        select(Workout.workout_date, Workout.workout_type, Workout.strain).where(
            Workout.user_id == user_id,
            Workout.workout_date >= start,
            Workout.workout_date <= end,
        )
    )
    loads: dict[date_type, float] = {}
    for wdate, wtype, strain in r.all():
        load = fallback_load(wtype, float(strain) if strain is not None else None)
        loads[wdate] = loads.get(wdate, 0.0) + load
    return loads


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

    # --- Phase 1 baseline: raw-weight Kalman ---
    raw_obs = [(d, w) for d, w in rows]
    raw_points = kalman_weight(raw_obs)
    raw_final = raw_points[-1] if raw_points else None
    raw_velocity = raw_final.velocity if raw_final else None
    raw_velocity_var = raw_final.velocity_var if raw_final else None
    raw_filtered_weight = raw_final.level if raw_final else None
    raw_sigma = math.sqrt(raw_velocity_var) if raw_velocity_var is not None else None

    # --- Training-water: absolute water (kernel anchors at fully-rested = 0). ---
    # No gate: the annotation is always populated when there are workouts in the
    # window. revealed_tdee stays on the RAW filter (moving TDEE onto the
    # de-watered series is D2, deferred).
    params = get_water_params(user_id)
    window_start = as_of - timedelta(days=n_days)
    loads_by_day = await _fetch_loads_by_day(session, user_id, window_start, as_of)
    water_offset_lbs: float | None = None
    water_clears: date_type | None = None
    weight_dewatered_lbs: float | None = None
    weight_dewatered_7d_avg: float | None = None
    if loads_by_day and raw_points:
        all_dates = [d for d, _ in rows]
        water_series = training_water_series(loads_by_day, all_dates, params)
        water_by_date = {p.date: p.water_lbs for p in water_series}  # ABSOLUTE, not deviation
        # Today's absolute training water (above fully-rested).
        water_offset_lbs = water_by_date.get(rows[-1][0])
        water_clears = clears_by(as_of, loads_by_day, params)
        # De-watered weight = filtered level − today's absolute water. Always.
        if raw_filtered_weight is not None and water_offset_lbs is not None:
            weight_dewatered_lbs = raw_filtered_weight - water_offset_lbs
        # 7-day mean of the de-watered series (filtered level − absolute water, per day).
        dewatered_series = [fp.level - water_by_date[fp.date] for fp in raw_points if fp.date in water_by_date]
        if dewatered_series:
            last7 = dewatered_series[-7:]
            weight_dewatered_7d_avg = sum(last7) / len(last7)

    # --- Confidence (unchanged from Phase 1; raw filter drives TDEE). ---
    sigma = raw_sigma
    n_obs = len(rows)
    tdee_conf: str | None
    if n_obs < 14 or (sigma is not None and sigma > 0.15):
        tdee_conf = "low"
    elif n_obs < 28 or (sigma is not None and sigma > 0.05):
        tdee_conf = "medium"
    else:
        tdee_conf = "high"

    revealed_tdee: int | None = None
    if avg_kcal is not None and raw_velocity is not None:
        revealed_tdee = int(float(avg_kcal) - (raw_velocity * 3500.0))

    return WeightTrend(
        n_days=n_days,
        current_lbs=current,  # raw, untouched
        delta_lbs=delta,
        revealed_tdee_kcal=revealed_tdee,
        filtered_weight_lbs=raw_filtered_weight,
        filtered_velocity_lbs_per_day=raw_velocity,
        revealed_tdee_confidence=tdee_conf,  # type: ignore[arg-type]
        training_water_offset_lbs=water_offset_lbs,
        weight_dewatered_lbs=weight_dewatered_lbs,
        weight_dewatered_7d_avg=weight_dewatered_7d_avg,
        training_water_clears_by=water_clears,
    )


async def _fetch_day_activities(session: AsyncSession, user_id: str, as_of: date_type) -> list[Activity]:
    """Union of auto workouts + manual activity_log for the day, normalized."""
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


async def compute_energy_today(
    session: AsyncSession,
    user_id: str,
    as_of: date_type,
    weight_lbs: float | None,
    today: date_type,
) -> EnergyToday | None:
    params = get_energy_params(user_id)

    # RMR from the latest body_composition row with lean mass; else fallback.
    br = await session.execute(
        select(BodyComposition.lean_mass_lbs)
        .where(
            BodyComposition.user_id == user_id,
            BodyComposition.lean_mass_lbs.is_not(None),
            BodyComposition.measured_date <= as_of,
        )
        .order_by(BodyComposition.measured_date.desc())
        .limit(1)
    )
    lean = br.scalar_one_or_none()
    if lean is not None:
        rmr_kcal, rmr_source = katch_mcardle_rmr(float(lean)), "dexa"
    else:
        rmr_kcal, rmr_source = params.fallback_rmr_kcal, "fallback"

    activities = await _fetch_day_activities(session, user_id, as_of)

    dr = await session.execute(
        select(DailyMetrics.whoop_kcal_burned).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date == as_of,
        )
    )
    whoop_kcal = dr.scalar_one_or_none()
    whoop_complete = as_of < today  # today's whoop total is partial

    return compute_energy(
        rmr_kcal=rmr_kcal,
        rmr_source=rmr_source,
        weight_lbs=weight_lbs,
        activities=activities,
        whoop_kcal_burned=whoop_kcal,
        whoop_complete=whoop_complete,
        params=params,
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

    _wt_weight = None
    if weight_trend is not None:
        _wt_weight = weight_trend.filtered_weight_lbs or weight_trend.current_lbs
    energy_today = await compute_energy_today(session, user_id, as_of, weight_lbs=_wt_weight, today=date_type.today())

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

    # Apply durable manual overrides on top of the engine's call (spec §13).
    # Kept out of the pure engine — compute_regulation stays I/O-free (Invariant #2).
    active_overrides = await fetch_active_overrides(session, user_id, as_of)
    if active_overrides:
        call = apply_overrides(call, active_overrides)

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
        energy_today=energy_today,
        active_events=active_events,
        flags=flags,
        missing_inputs=missing,
        confidence=call.confidence,
        generated_at=datetime.now(UTC),
    )
