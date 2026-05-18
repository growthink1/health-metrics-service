"""Dashboard REST endpoints (consumed by the Next.js dashboard frontend)."""

import statistics
from contextlib import asynccontextmanager
from datetime import date as date_type, datetime, timedelta
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..models import DailyMetrics, ManualLog, Workout
from ..narration import generate_narration
from ..regulation import compute_regulation_signals, regulate

log = structlog.get_logger()
router = APIRouter(prefix="/api")

METRIC_COLUMNS = {
    "hrv": "oura_hrv_avg",
    "rhr": "oura_rhr",
    "sleep_min": "oura_sleep_duration_min",
    "strain": "whoop_day_strain",
    "recovery": "whoop_recovery_score",
}

# When the primary Oura column is null (e.g. Oura doesn't return sleep sessions
# for this user), fall back to the Whoop equivalent so the tile + drilldown
# render real data instead of an empty series.
ALL_METRIC_DEFS: dict[str, dict[str, str]] = {
    "hrv": {"column": "oura_hrv_avg", "source_table": "daily_metrics", "z_column": "unified_hrv_z"},
    "rhr": {"column": "oura_rhr", "source_table": "daily_metrics", "z_column": "unified_rhr_z"},
    "sleep_min": {"column": "oura_sleep_duration_min", "source_table": "daily_metrics", "z_column": "unified_sleep_z"},
    "strain": {"column": "whoop_day_strain", "source_table": "daily_metrics", "z_column": None},
    "recovery": {"column": "whoop_recovery_score", "source_table": "daily_metrics", "z_column": None},
    "weight_lbs": {"column": "weight_lbs", "source_table": "manual_log", "z_column": None},
}


def _whoop_sleep_actual_min(whoop_raw: dict[str, Any] | None) -> int | None:
    """Derive 'minutes slept' from the raw Whoop v2 stage_summary if present.

    Whoop's stage_summary has total_in_bed_time_milli + total_awake_time_milli;
    actual sleep = in_bed - awake. Returns None on any missing field.
    """
    try:
        stage = whoop_raw["sleep"]["records"][0]["score"]["stage_summary"]
        in_bed = stage.get("total_in_bed_time_milli")
        awake = stage.get("total_awake_time_milli") or 0
        if in_bed is None:
            return None
        return int((in_bed - awake) / 60000)
    except (TypeError, KeyError, IndexError):
        return None


def _read_metric(r: DailyMetrics, metric: str) -> float | None:
    """Read a metric value off a DailyMetrics row, falling back from Oura → Whoop
    when the primary Oura field is null. Returns float or None."""
    if metric == "hrv":
        v = r.oura_hrv_avg if r.oura_hrv_avg is not None else r.whoop_hrv_ms
    elif metric == "rhr":
        v = r.oura_rhr if r.oura_rhr is not None else r.whoop_rhr
    elif metric == "sleep_min":
        v = r.oura_sleep_duration_min if r.oura_sleep_duration_min is not None \
            else _whoop_sleep_actual_min(r.whoop_raw)
    elif metric == "strain":
        v = r.whoop_day_strain
    elif metric == "recovery":
        v = r.whoop_recovery_score
    else:
        v = None
    if v is None:
        return None
    return float(v)


# Indirection so tests can substitute the session factory
def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


def _resolve_as_of(as_of: str | None) -> date_type:
    if as_of is None:
        tz = ZoneInfo(get_settings().timezone)
        return datetime.now(tz).date() - timedelta(days=1)
    try:
        return date_type.fromisoformat(as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid as_of: {e}")


def _log_status_for(ml: ManualLog | None) -> str:
    if ml is None:
        return "all_missing"
    parts = []
    if ml.subjective_energy is None or ml.subjective_mood is None or ml.subjective_hunger is None:
        parts.append("subjective_missing")
    if ml.weight_lbs is None:
        parts.append("weight_missing")
    if ml.kcal_consumed is None:
        parts.append("nutrition_missing")
    return ",".join(parts) if parts else "complete"


@router.get("/dashboard/today")
async def dashboard_today(
    user_id: str = Query(default=None),
    as_of: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    anchor = _resolve_as_of(as_of)

    async with _session_factory() as session:
        # Fetch most recent daily_metrics <= anchor
        res = await session.execute(
            select(DailyMetrics)
            .where(DailyMetrics.user_id == uid)
            .where(DailyMetrics.metric_date <= anchor)
            .order_by(DailyMetrics.metric_date.desc())
            .limit(1)
        )
        dm = res.scalar_one_or_none()

        # Manual log for anchor
        res = await session.execute(
            select(ManualLog).where(
                ManualLog.user_id == uid,
                ManualLog.log_date == anchor,
            )
        )
        ml = res.scalar_one_or_none()

        # Compute regulation
        signals = await compute_regulation_signals(session, uid, anchor)
        recommendation, rationale, payload = regulate(signals)

        # Narration (cached + content-addressed)
        narration = await generate_narration(
            session, uid, anchor, recommendation, signals
        )

        today_hrv = dm.oura_hrv_avg if (dm and dm.oura_hrv_avg is not None) else (
            int(dm.whoop_hrv_ms) if dm and dm.whoop_hrv_ms is not None else None
        )

        return {
            "as_of": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            "metric_date": dm.metric_date.isoformat() if dm else anchor.isoformat(),
            "today_strip": {
                "recommendation": recommendation,
                "suggested_kcal": payload.get("kcal"),
                "suggested_training_mod": payload.get("training"),
                "today_hrv_ms": today_hrv,
                "hrv_z_3d_avg": round(signals.hrv_z_3d, 2) if signals.hrv_z_3d else 0.0,
                "log_status": _log_status_for(ml),
            },
            "narration": narration,
            "narration_generated_at": datetime.now(ZoneInfo(settings.timezone)).isoformat()
            if narration else None,
            "rationale": rationale,
        }


@router.get("/dashboard/grid")
async def dashboard_grid(
    user_id: str | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=365),
    as_of: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    anchor = _resolve_as_of(as_of)
    start = anchor - timedelta(days=days - 1)

    async with _session_factory() as session:
        res = await session.execute(
            select(DailyMetrics)
            .where(DailyMetrics.user_id == uid)
            .where(DailyMetrics.metric_date >= start)
            .where(DailyMetrics.metric_date <= anchor)
            .order_by(DailyMetrics.metric_date.asc())
        )
        dm_rows = list(res.scalars().all())

        res = await session.execute(
            select(ManualLog)
            .where(ManualLog.user_id == uid)
            .where(ManualLog.log_date >= start)
            .where(ManualLog.log_date <= anchor)
            .order_by(ManualLog.log_date.asc())
        )
        ml_rows = list(res.scalars().all())

    tiles = []
    for metric in METRIC_COLUMNS:
        series = []
        for r in dm_rows:
            v = _read_metric(r, metric)
            if v is not None:
                series.append({"date": r.metric_date.isoformat(), "value": v})
        current = series[-1]["value"] if series else None
        tiles.append({"metric": metric, "current": current, "series": series})

    weight_series = [
        {"date": r.log_date.isoformat(),
         "value": float(r.weight_lbs) if r.weight_lbs is not None else None}
        for r in ml_rows
        if r.weight_lbs is not None
    ]
    tiles.append({
        "metric": "weight_lbs",
        "current": weight_series[-1]["value"] if weight_series else None,
        "series": weight_series,
    })

    return {"n_days": days, "tiles": tiles}


@router.get("/metric/{name}")
async def metric_drilldown(
    name: str,
    user_id: str | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=365),
    as_of: str | None = Query(default=None),
) -> dict[str, Any]:
    if name not in ALL_METRIC_DEFS:
        raise HTTPException(status_code=400, detail=f"unknown metric: {name}")

    settings = get_settings()
    uid = user_id or settings.user_id
    anchor = _resolve_as_of(as_of)
    start = anchor - timedelta(days=days - 1)
    meta = ALL_METRIC_DEFS[name]

    async with _session_factory() as session:
        if meta["source_table"] == "daily_metrics":
            res = await session.execute(
                select(DailyMetrics)
                .where(DailyMetrics.user_id == uid)
                .where(DailyMetrics.metric_date >= start)
                .where(DailyMetrics.metric_date <= anchor)
                .order_by(DailyMetrics.metric_date.asc())
            )
            rows = list(res.scalars().all())
            series = []
            for r in rows:
                v = _read_metric(r, name)
                if v is None:
                    continue
                pt = {"date": r.metric_date.isoformat(), "value": v}
                if meta["z_column"]:
                    z = getattr(r, meta["z_column"])
                    pt["z"] = float(z) if z is not None else None
                series.append(pt)
        else:  # manual_log
            res = await session.execute(
                select(ManualLog)
                .where(ManualLog.user_id == uid)
                .where(ManualLog.log_date >= start)
                .where(ManualLog.log_date <= anchor)
                .order_by(ManualLog.log_date.asc())
            )
            rows = list(res.scalars().all())
            series = [
                {"date": r.log_date.isoformat(), "value": float(getattr(r, meta["column"]))}
                for r in rows
                if getattr(r, meta["column"]) is not None
            ]

    values = [pt["value"] for pt in series]
    if len(values) >= 2:
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        # Simple linear slope
        n = len(values)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = mean
        num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((x - x_mean) ** 2 for x in xs)
        slope = num / den if den != 0 else 0.0
    else:
        mean = values[0] if values else 0.0
        std = 0.0
        slope = 0.0

    z_today = None
    if series and meta.get("z_column"):
        last = series[-1]
        z_today = last.get("z")

    return {
        "metric": name,
        "n_days": days,
        "series": series,
        "stats": {
            "mean": round(mean, 2),
            "std": round(std, 2),
            "slope_per_day": round(slope, 3),
            "z_today": z_today,
        },
        "baseline": {
            "mean": round(mean, 2),
            "lower_1sd": round(mean - std, 2),
            "upper_1sd": round(mean + std, 2),
        },
    }


class ManualLogPayload(BaseModel):
    user_id: str | None = None
    date: str  # ISO date
    subjective_energy: int | None = PydField(default=None, ge=1, le=10)
    subjective_mood: int | None = PydField(default=None, ge=1, le=10)
    subjective_hunger: int | None = PydField(default=None, ge=1, le=10)
    weight_lbs: float | None = PydField(default=None, ge=50, le=500)
    kcal_consumed: int | None = PydField(default=None, ge=0, le=10000)
    protein_g: int | None = PydField(default=None, ge=0, le=1000)
    fat_g: int | None = PydField(default=None, ge=0, le=1000)
    carbs_g: int | None = PydField(default=None, ge=0, le=2000)
    notes: str | None = None


@router.post("/manual-log")
async def manual_log(payload: ManualLogPayload = Body(...)) -> dict[str, Any]:
    settings = get_settings()
    uid = payload.user_id or settings.user_id
    try:
        log_date = date_type.fromisoformat(payload.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")

    fields = payload.model_dump(exclude_none=True, exclude={"user_id", "date"})
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")

    fields_updated = list(fields.keys())

    async with _session_factory() as session:
        stmt = pg_insert(ManualLog).values(user_id=uid, log_date=log_date, **fields)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "log_date"], set_=fields
        )
        await session.execute(stmt)
        await session.commit()

        res = await session.execute(
            select(ManualLog).where(
                ManualLog.user_id == uid, ManualLog.log_date == log_date
            )
        )
        row = res.scalar_one()

    completeness = {
        "subjective": all([
            row.subjective_energy is not None,
            row.subjective_mood is not None,
            row.subjective_hunger is not None,
        ]),
        "weight": row.weight_lbs is not None,
        "nutrition": row.kcal_consumed is not None,
    }
    next_required = []
    if not completeness["subjective"]:
        next_required.extend(["subjective_energy", "subjective_mood", "subjective_hunger"])

    return {
        "logged_date": log_date.isoformat(),
        "fields_updated": fields_updated,
        "completeness": completeness,
        "next_required_inputs": next_required,
    }


@router.get("/workouts")
async def workouts(
    user_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    workout_type: str | None = Query(default=None),
    as_of: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    anchor = _resolve_as_of(as_of)
    start = anchor - timedelta(days=days - 1)

    async with _session_factory() as session:
        stmt = (
            select(Workout)
            .where(Workout.user_id == uid)
            .where(Workout.workout_date >= start)
            .where(Workout.workout_date <= anchor)
            .order_by(Workout.workout_date.desc(), Workout.started_at.desc())
        )
        if workout_type:
            stmt = stmt.where(Workout.workout_type == workout_type)
        res = await session.execute(stmt)
        rows = list(res.scalars().all())

    return {
        "n_days": days,
        "workouts": [
            {
                "date": r.workout_date.isoformat(),
                "source": r.source,
                "source_id": r.source_id,
                "type": r.workout_type,
                "started_at": r.started_at.isoformat(),
                "duration_min": r.duration_min,
                "strain": float(r.strain) if r.strain is not None else None,
                "kcal": r.kcal,
                "avg_hr": r.avg_hr,
                "max_hr": r.max_hr,
                "zones": {
                    "0": r.zone_0_min, "1": r.zone_1_min, "2": r.zone_2_min,
                    "3": r.zone_3_min, "4": r.zone_4_min, "5": r.zone_5_min,
                },
            }
            for r in rows
        ],
    }
