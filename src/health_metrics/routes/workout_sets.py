"""Per-set workout logging — CRUD on workout_sets, FKed to workouts.id."""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..models import Workout, WorkoutSet

log = structlog.get_logger()
router = APIRouter(prefix="/api")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


class SetCreate(BaseModel):
    user_id: str | None = None
    exercise: str
    reps: int = PydField(..., ge=1, le=100)
    weight_lbs: float | None = PydField(default=None, ge=0, le=2000)
    rpe: float | None = PydField(default=None, ge=1, le=10)
    notes: str | None = None


def _set_to_json(s: WorkoutSet) -> dict[str, Any]:
    return {
        "id": s.id,
        "workout_id": s.workout_id,
        "set_number": s.set_number,
        "exercise": s.exercise,
        "reps": s.reps,
        "weight_lbs": float(s.weight_lbs) if s.weight_lbs is not None else None,
        "rpe": float(s.rpe) if s.rpe is not None else None,
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.post("/workouts/{workout_id}/sets")
async def create_set(workout_id: int, payload: SetCreate) -> dict[str, Any]:
    settings = get_settings()
    uid = payload.user_id or settings.user_id

    async with _session_factory() as session:
        w = (await session.execute(
            select(Workout).where(Workout.id == workout_id, Workout.user_id == uid)
        )).scalar_one_or_none()
        if w is None:
            raise HTTPException(status_code=404, detail="workout not found")

        max_n = (await session.execute(
            select(func.max(WorkoutSet.set_number))
            .where(WorkoutSet.workout_id == workout_id)
            .where(WorkoutSet.exercise == payload.exercise)
        )).scalar()
        next_n = (max_n or 0) + 1

        s = WorkoutSet(
            user_id=uid, workout_id=workout_id, set_number=next_n,
            exercise=payload.exercise, reps=payload.reps,
            weight_lbs=payload.weight_lbs, rpe=payload.rpe, notes=payload.notes,
        )
        session.add(s)
        await session.flush()
        await session.refresh(s)
        await session.commit()

    return {"set": _set_to_json(s)}


@router.get("/workouts/{workout_id}/sets")
async def list_sets(workout_id: int, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id

    async with _session_factory() as session:
        rows = (await session.execute(
            select(WorkoutSet)
            .where(WorkoutSet.workout_id == workout_id, WorkoutSet.user_id == uid)
            .order_by(WorkoutSet.exercise.asc(), WorkoutSet.set_number.asc())
        )).scalars().all()

    return {"workout_id": workout_id, "sets": [_set_to_json(s) for s in rows]}


@router.delete("/workouts/sets/{set_id}")
async def delete_set(set_id: int, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id

    async with _session_factory() as session:
        s = (await session.execute(
            select(WorkoutSet).where(WorkoutSet.id == set_id, WorkoutSet.user_id == uid)
        )).scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=404, detail="set not found")
        await session.delete(s)
        await session.commit()

    return {"deleted_set_id": set_id}
