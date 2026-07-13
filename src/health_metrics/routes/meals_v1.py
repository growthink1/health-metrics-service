"""POST + PATCH /api/v1/meals — log a meal + correct it in place."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import time as time_type

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import Meal
from ..regulation.cache import invalidate_cache
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


class MealPayload(BaseModel):
    user_id: str = "hugo"
    meal_date: date_type = Field(default_factory=date_type.today)
    meal_time: str | None = None
    meal_name: str | None = None
    kcal: int | None = None
    protein_g: int | None = None
    fat_g: int | None = None
    carbs_g: int | None = None
    notes: str | None = None
    photo_path: str | None = None
    source: str = "api"


class MealResponse(BaseModel):
    id: int
    user_id: str
    meal_date: date_type
    meal_time: str | None
    meal_name: str | None
    kcal: int | None
    protein_g: int | None
    fat_g: int | None
    carbs_g: int | None
    notes: str | None
    photo_path: str | None
    source: str


@router.post("/meals", response_model=MealResponse, status_code=201)
async def post_meal(
    payload: MealPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> MealResponse:
    log.info(
        "meal_write",
        user_id=payload.user_id,
        meal_date=payload.meal_date.isoformat(),
        principal=principal,
    )
    parsed_time: time_type | None = None
    if payload.meal_time is not None:
        parsed_time = time_type.fromisoformat(payload.meal_time)

    async with _session_factory() as session:
        meal = Meal(
            user_id=payload.user_id,
            meal_date=payload.meal_date,
            meal_time=parsed_time,
            meal_name=payload.meal_name,
            kcal=payload.kcal,
            protein_g=payload.protein_g,
            fat_g=payload.fat_g,
            carbs_g=payload.carbs_g,
            notes=payload.notes,
            photo_path=payload.photo_path,
            source=payload.source,
        )
        session.add(meal)
        await session.flush()

        # Cache invalidation — affects today's brief regardless of the meal_date
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(meal)

        return MealResponse(
            id=meal.id,
            user_id=meal.user_id,
            meal_date=meal.meal_date,
            meal_time=meal.meal_time.isoformat() if meal.meal_time is not None else None,
            meal_name=meal.meal_name,
            kcal=meal.kcal,
            protein_g=meal.protein_g,
            fat_g=meal.fat_g,
            carbs_g=meal.carbs_g,
            notes=meal.notes,
            photo_path=meal.photo_path,
            source=meal.source,
        )


class MealUpdate(BaseModel):
    meal_date: date_type | None = None
    meal_time: str | None = None
    meal_name: str | None = None
    kcal: int | None = None
    protein_g: int | None = None
    fat_g: int | None = None
    carbs_g: int | None = None
    notes: str | None = None
    photo_path: str | None = None


@router.patch("/meals/{meal_id}", response_model=MealResponse)
async def update_meal(
    meal_id: int,
    payload: MealUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> MealResponse:
    log.info("meal_update", meal_id=meal_id, principal=principal)
    async with _session_factory() as session:
        r = await session.execute(select(Meal).where(Meal.id == meal_id))
        meal = r.scalar_one_or_none()
        if meal is None:
            raise HTTPException(status_code=404, detail="meal not found")

        if payload.meal_date is not None:
            meal.meal_date = payload.meal_date
        if payload.meal_time is not None:
            meal.meal_time = time_type.fromisoformat(payload.meal_time)
        if payload.meal_name is not None:
            meal.meal_name = payload.meal_name
        if payload.kcal is not None:
            meal.kcal = payload.kcal
        if payload.protein_g is not None:
            meal.protein_g = payload.protein_g
        if payload.fat_g is not None:
            meal.fat_g = payload.fat_g
        if payload.carbs_g is not None:
            meal.carbs_g = payload.carbs_g
        if payload.notes is not None:
            meal.notes = payload.notes
        if payload.photo_path is not None:
            meal.photo_path = payload.photo_path

        await session.flush()
        # meals has no updated_at, and the cache freshness check keys off
        # Meal.created_at (unchanged by a PATCH), so this explicit invalidation
        # is the only thing that busts the cache after an in-place correction.
        await invalidate_cache(session, meal.user_id, date_type.today())
        await session.commit()
        await session.refresh(meal)

        return MealResponse(
            id=meal.id,
            user_id=meal.user_id,
            meal_date=meal.meal_date,
            meal_time=meal.meal_time.isoformat() if meal.meal_time is not None else None,
            meal_name=meal.meal_name,
            kcal=meal.kcal,
            protein_g=meal.protein_g,
            fat_g=meal.fat_g,
            carbs_g=meal.carbs_g,
            notes=meal.notes,
            photo_path=meal.photo_path,
            source=meal.source,
        )
