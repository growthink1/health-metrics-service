"""GET /api/v1/workouts — recent workout history."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import Workout
from ..regulation.schemas import WorkoutSummary
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")

_USER_AGES: dict[str, int] = {"hugo": 44, "andrea": 40}


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


class WorkoutsResponse(BaseModel):
    user_id: str
    n_days: int
    workouts: list[WorkoutSummary]


@router.get("/workouts", response_model=WorkoutsResponse)
async def get_workouts_history(
    user_id: str = Query(default="hugo"),
    n_days: int = Query(default=14, ge=1, le=90),
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> WorkoutsResponse:
    log.info(
        "workouts_history_request",
        user_id=user_id,
        n_days=n_days,
        principal=principal,
    )
    today = date_type.today()
    cutoff = today - timedelta(days=n_days)
    age_max = 220 - _USER_AGES.get(user_id, 44)
    async with _session_factory() as session:
        r = await session.execute(
            select(Workout)
            .where(
                Workout.user_id == user_id,
                Workout.workout_date >= cutoff,
                Workout.workout_date <= today,
            )
            .order_by(Workout.workout_date.desc(), Workout.started_at.desc())
        )
        rows = list(r.scalars().all())
    workouts = [
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
        for w in rows
    ]
    return WorkoutsResponse(user_id=user_id, n_days=n_days, workouts=workouts)
