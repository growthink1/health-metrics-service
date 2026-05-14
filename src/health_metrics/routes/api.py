"""Dashboard REST endpoints (consumed by the Next.js dashboard frontend)."""

from contextlib import asynccontextmanager
from datetime import date as date_type, datetime, timedelta
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
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
    for metric, col in METRIC_COLUMNS.items():
        series = [
            {"date": r.metric_date.isoformat(), "value": getattr(r, col)}
            for r in dm_rows
            if getattr(r, col) is not None
        ]
        # convert Decimal to float for JSON
        for pt in series:
            if pt["value"] is not None and not isinstance(pt["value"], (int, float)):
                pt["value"] = float(pt["value"])
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
