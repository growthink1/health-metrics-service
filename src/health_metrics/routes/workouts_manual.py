"""Manual off-strap workout logging — writes to the existing workouts table
with source='manual'. UI surfaces these alongside Whoop/Oura-sourced workouts."""

from contextlib import asynccontextmanager
from datetime import date as date_type, datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..models import Workout

log = structlog.get_logger()
router = APIRouter(prefix="/api")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


class ManualWorkoutCreate(BaseModel):
    user_id: str | None = None
    date: str
    sport_name: str
    duration_min: int = PydField(..., ge=1, le=600)
    strain: float | None = PydField(default=None, ge=0, le=21)
    kcal: int | None = PydField(default=None, ge=0)
    notes: str | None = None


def _workout_to_json(w: Workout) -> dict[str, Any]:
    return {
        "id": w.id,
        "date": w.workout_date.isoformat(),
        "source": w.source,
        "source_id": w.source_id,
        "type": w.workout_type,
        "started_at": w.started_at.isoformat(),
        "duration_min": w.duration_min,
        "strain": float(w.strain) if w.strain is not None else None,
        "kcal": w.kcal,
    }


@router.post("/workouts/manual")
async def create_manual_workout(payload: ManualWorkoutCreate) -> dict[str, Any]:
    settings = get_settings()
    uid = payload.user_id or settings.user_id
    try:
        d = date_type.fromisoformat(payload.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")

    started_at = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    async with _session_factory() as session:
        w = Workout(
            user_id=uid,
            workout_date=d,
            source="manual",
            source_id=uuid4().hex,
            workout_type=payload.sport_name,
            started_at=started_at,
            duration_min=payload.duration_min,
            strain=payload.strain,
            kcal=payload.kcal,
            raw={"notes": payload.notes} if payload.notes else None,
        )
        session.add(w)
        await session.flush()
        await session.refresh(w)
        await session.commit()

    return {"workout": _workout_to_json(w)}
