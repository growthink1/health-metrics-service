"""POST /api/v1/activities — log a walk/ride/etc. Insert-only; multiple per day."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import ActivityLog
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


class ActivityPayload(BaseModel):
    user_id: str = "hugo"
    activity_date: date_type = Field(default_factory=date_type.today)
    activity_type: str
    distance_mi: float | None = None
    duration_min: int | None = None
    elevation_ft: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    strain: float | None = None
    source: str = "manual"
    notes: str | None = None


class ActivityResponse(BaseModel):
    id: int
    user_id: str
    activity_date: date_type
    activity_type: str
    distance_mi: float | None
    duration_min: int | None
    elevation_ft: int | None
    avg_hr: int | None
    max_hr: int | None
    strain: float | None
    source: str
    notes: str | None


@router.post("/activities", response_model=ActivityResponse, status_code=201)
async def post_activity(
    payload: ActivityPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> ActivityResponse:
    log.info(
        "activity_write", user_id=payload.user_id, activity_date=payload.activity_date.isoformat(), principal=principal
    )
    async with _session_factory() as session:
        row = ActivityLog(
            user_id=payload.user_id,
            activity_date=payload.activity_date,
            activity_type=payload.activity_type,
            distance_mi=Decimal(str(payload.distance_mi)) if payload.distance_mi is not None else None,
            duration_min=payload.duration_min,
            elevation_ft=payload.elevation_ft,
            avg_hr=payload.avg_hr,
            max_hr=payload.max_hr,
            strain=Decimal(str(payload.strain)) if payload.strain is not None else None,
            source=payload.source,
            notes=payload.notes,
        )
        session.add(row)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(row)
        return ActivityResponse(
            id=row.id,
            user_id=row.user_id,
            activity_date=row.activity_date,
            activity_type=row.activity_type,
            distance_mi=float(row.distance_mi) if row.distance_mi is not None else None,
            duration_min=row.duration_min,
            elevation_ft=row.elevation_ft,
            avg_hr=row.avg_hr,
            max_hr=row.max_hr,
            strain=float(row.strain) if row.strain is not None else None,
            source=row.source,
            notes=row.notes,
        )
