"""POST + PATCH /api/v1/health-events — create + update health events."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import HealthEvent
from ..regulation.cache import invalidate_cache
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")

EventType = Literal[
    "dental_procedure",
    "acute_infection",
    "antibiotic_course",
    "fever",
    "injury",
    "scheduled_lab_draw",
    "scheduled_dexa",
    "scheduled_sleep_study",
]
EventStatus = Literal["active", "pending", "resolving", "resolved"]


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


class HealthEventCreate(BaseModel):
    user_id: str = "hugo"
    event_type: EventType
    status: EventStatus
    started_at: date_type | None = None
    expected_resolution: date_type | None = None
    affects: list[str] = []
    notes: str | None = None


class HealthEventUpdate(BaseModel):
    status: EventStatus | None = None
    expected_resolution: date_type | None = None
    affects: list[str] | None = None
    notes: str | None = None


class HealthEventResponse(BaseModel):
    id: UUID
    user_id: str
    event_type: str
    status: str
    started_at: date_type | None
    expected_resolution: date_type | None
    affects: list[str]
    notes: str | None


@router.post("/health-events", response_model=HealthEventResponse, status_code=201)
async def create_health_event(
    payload: HealthEventCreate,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> HealthEventResponse:
    log.info(
        "health_event_create",
        user_id=payload.user_id,
        event_type=payload.event_type,
        status=payload.status,
        principal=principal,
    )
    async with _session_factory() as session:
        ev = HealthEvent(
            user_id=payload.user_id,
            event_type=payload.event_type,
            status=payload.status,
            started_at=payload.started_at,
            expected_resolution=payload.expected_resolution,
            affects=payload.affects,
            notes=payload.notes,
        )
        session.add(ev)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(ev)
        return HealthEventResponse(
            id=ev.id,
            user_id=ev.user_id,
            event_type=ev.event_type,
            status=ev.status,
            started_at=ev.started_at,
            expected_resolution=ev.expected_resolution,
            affects=list(ev.affects or []),
            notes=ev.notes,
        )


@router.patch("/health-events/{event_id}", response_model=HealthEventResponse)
async def update_health_event(
    event_id: UUID,
    payload: HealthEventUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> HealthEventResponse:
    log.info("health_event_update", event_id=str(event_id), principal=principal)
    async with _session_factory() as session:
        r = await session.execute(select(HealthEvent).where(HealthEvent.id == event_id))
        ev = r.scalar_one_or_none()
        if ev is None:
            raise HTTPException(status_code=404, detail="health_event not found")
        if payload.status is not None:
            ev.status = payload.status
        if payload.expected_resolution is not None:
            ev.expected_resolution = payload.expected_resolution
        if payload.affects is not None:
            ev.affects = payload.affects
        if payload.notes is not None:
            ev.notes = payload.notes
        ev.updated_at = datetime.now(UTC)
        await session.flush()
        await invalidate_cache(session, ev.user_id, date_type.today())
        await session.commit()
        await session.refresh(ev)
        return HealthEventResponse(
            id=ev.id,
            user_id=ev.user_id,
            event_type=ev.event_type,
            status=ev.status,
            started_at=ev.started_at,
            expected_resolution=ev.expected_resolution,
            affects=list(ev.affects or []),
            notes=ev.notes,
        )
