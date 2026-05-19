"""Meal logging — CRUD + photo upload/stream + day-aggregate recompute."""

from contextlib import asynccontextmanager
from datetime import date as date_type, time as time_type
from typing import Any, AsyncIterator

import structlog
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field as PydField
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..jobs.recompute import recompute_day_aggregate
from ..models import ManualLog, Meal
from ..storage import get_storage

log = structlog.get_logger()
router = APIRouter(prefix="/api")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


class MealCreate(BaseModel):
    user_id: str | None = None
    date: str  # ISO yyyy-mm-dd
    time: str | None = None  # ISO HH:MM
    meal_name: str | None = None
    kcal: int = PydField(..., ge=0, le=10000)
    protein_g: int | None = PydField(default=None, ge=0, le=1000)
    fat_g: int | None = PydField(default=None, ge=0, le=1000)
    carbs_g: int | None = PydField(default=None, ge=0, le=2000)
    notes: str | None = None
    photo_path: str | None = None
    source: str = "chat"


def _meal_to_json(m: Meal) -> dict[str, Any]:
    return {
        "id": m.id,
        "date": m.meal_date.isoformat(),
        "time": m.meal_time.isoformat() if m.meal_time else None,
        "meal_name": m.meal_name,
        "kcal": m.kcal,
        "protein_g": m.protein_g,
        "fat_g": m.fat_g,
        "carbs_g": m.carbs_g,
        "notes": m.notes,
        "photo_path": m.photo_path,
        "source": m.source,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("/meals")
async def create_meal(payload: MealCreate) -> dict[str, Any]:
    settings = get_settings()
    uid = payload.user_id or settings.user_id
    try:
        meal_date = date_type.fromisoformat(payload.date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")
    meal_time: time_type | None = None
    if payload.time:
        try:
            meal_time = time_type.fromisoformat(payload.time)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid time: {e}")

    async with _session_factory() as session:
        meal = Meal(
            user_id=uid,
            meal_date=meal_date,
            meal_time=meal_time,
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
        await session.refresh(meal)
        await recompute_day_aggregate(session, uid, meal_date)

        res = await session.execute(
            select(ManualLog).where(ManualLog.user_id == uid, ManualLog.log_date == meal_date)
        )
        ml = res.scalar_one()

    return {
        "meal": _meal_to_json(meal),
        "aggregate": {
            "kcal_consumed": ml.kcal_consumed,
            "protein_g": ml.protein_g,
            "fat_g": ml.fat_g,
            "carbs_g": ml.carbs_g,
        },
    }


@router.get("/meals")
async def list_meals(
    user_id: str | None = Query(default=None),
    date: str = Query(...),
) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    try:
        d = date_type.fromisoformat(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")

    async with _session_factory() as session:
        res = await session.execute(
            select(Meal)
            .where(Meal.user_id == uid, Meal.meal_date == d)
            .order_by(Meal.meal_time.asc().nullslast(), Meal.created_at.asc())
        )
        meals = list(res.scalars().all())

    return {"date": d.isoformat(), "meals": [_meal_to_json(m) for m in meals]}


@router.delete("/meals/{meal_id}")
async def delete_meal(meal_id: int, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id

    async with _session_factory() as session:
        res = await session.execute(
            select(Meal).where(Meal.id == meal_id, Meal.user_id == uid)
        )
        meal = res.scalar_one_or_none()
        if meal is None:
            raise HTTPException(status_code=404, detail="meal not found")
        meal_date = meal.meal_date
        photo_path = meal.photo_path
        await session.delete(meal)
        await session.flush()
        await recompute_day_aggregate(session, uid, meal_date)

    # Best-effort photo cleanup; never fail the request for storage errors
    if photo_path:
        store = get_storage()
        if store is not None:
            try:
                store.delete(photo_path)
            except Exception:
                log.warning("photo_delete_failed", path=photo_path)

    return {"deleted_meal_id": meal_id, "recomputed_date": meal_date.isoformat()}


@router.post("/meals/upload")
async def upload_photo(photo: UploadFile = File(...)) -> dict[str, Any]:
    store = get_storage()
    if store is None:
        raise HTTPException(status_code=503, detail="photo storage not configured")
    raw = await photo.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="photo too large; max 5MB")
    ext = "jpg"
    if photo.content_type:
        if "png" in photo.content_type:
            ext = "png"
        elif "webp" in photo.content_type:
            ext = "webp"
    key = store.upload_with_sha(raw, prefix="meals", ext=ext)
    return {"photo_path": key, "bytes": len(raw)}


@router.get("/meals/{meal_id}/photo")
async def get_meal_photo(meal_id: int, user_id: str | None = Query(default=None)):
    settings = get_settings()
    uid = user_id or settings.user_id
    async with _session_factory() as session:
        res = await session.execute(
            select(Meal).where(Meal.id == meal_id, Meal.user_id == uid)
        )
        meal = res.scalar_one_or_none()
    if meal is None or not meal.photo_path:
        raise HTTPException(status_code=404, detail="meal photo not found")
    store = get_storage()
    if store is None:
        raise HTTPException(status_code=503, detail="photo storage not configured")
    media = "image/jpeg"
    if meal.photo_path.endswith(".png"):
        media = "image/png"
    elif meal.photo_path.endswith(".webp"):
        media = "image/webp"
    return StreamingResponse(store.stream(meal.photo_path), media_type=media)
