"""POST + PATCH + GET /api/v1/regulation-overrides — durable manual overrides (spec §13).

Overrides are fetched + applied in the brief layer (compute_session_brief), never
in the pure engine. This route is the CRUD surface: create, revoke, list.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import RegulationOverride
from ..regulation.cache import invalidate_cache
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")

OverrideField = Literal[
    "kcal_target",
    "training_modifier",
    "state",
    "add_override",
    "remove_override",
]
CreatedBy = Literal["hugo", "andrea", "claude_chat", "claude_code"]


def _session_factory() -> AsyncIterator:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


class RegulationOverrideCreate(BaseModel):
    user_id: str = "hugo"
    field: OverrideField
    value: Any
    justification: str
    valid_from: date_type
    valid_until: date_type
    created_by: CreatedBy


class RegulationOverrideRevoke(BaseModel):
    reason: str


class RegulationOverrideResponse(BaseModel):
    id: str
    user_id: str
    field: str
    value: Any
    justification: str
    valid_from: date_type
    valid_until: date_type
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None


class RegulationOverrideList(BaseModel):
    overrides: list[RegulationOverrideResponse]


def _to_response(row: RegulationOverride) -> RegulationOverrideResponse:
    return RegulationOverrideResponse(
        id=str(row.id),
        user_id=row.user_id,
        field=row.field,
        value=row.value,
        justification=row.justification,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        created_by=row.created_by,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
    )


@router.post("/regulation-overrides", response_model=RegulationOverrideResponse, status_code=201)
async def create_regulation_override(
    payload: RegulationOverrideCreate,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> RegulationOverrideResponse:
    log.info(
        "regulation_override_create",
        user_id=payload.user_id,
        field=payload.field,
        created_by=payload.created_by,
        principal=principal,
    )
    async with _session_factory() as session:
        ov = RegulationOverride(
            user_id=payload.user_id,
            field=payload.field,
            value=payload.value,
            justification=payload.justification,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            created_by=payload.created_by,
        )
        session.add(ov)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(ov)
        return _to_response(ov)


@router.patch("/regulation-overrides/{override_id}/revoke", response_model=RegulationOverrideResponse)
async def revoke_regulation_override(
    override_id: UUID,
    payload: RegulationOverrideRevoke,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> RegulationOverrideResponse:
    log.info("regulation_override_revoke", override_id=str(override_id), principal=principal)
    async with _session_factory() as session:
        r = await session.execute(select(RegulationOverride).where(RegulationOverride.id == override_id))
        ov = r.scalar_one_or_none()
        if ov is None:
            raise HTTPException(status_code=404, detail="regulation_override not found")
        ov.revoked_at = datetime.now(UTC)
        ov.revoked_reason = payload.reason
        await session.flush()
        await invalidate_cache(session, ov.user_id, date_type.today())
        await session.commit()
        await session.refresh(ov)
        return _to_response(ov)


@router.get("/regulation-overrides", response_model=RegulationOverrideList)
async def list_regulation_overrides(
    user_id: str = Query(...),
    active_only: bool = Query(default=False),
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> RegulationOverrideList:
    log.info(
        "regulation_override_list",
        user_id=user_id,
        active_only=active_only,
        principal=principal,
    )
    async with _session_factory() as session:
        stmt = select(RegulationOverride).where(RegulationOverride.user_id == user_id)
        if active_only:
            today = date_type.today()
            stmt = stmt.where(
                RegulationOverride.revoked_at.is_(None),
                RegulationOverride.valid_from <= today,
                RegulationOverride.valid_until >= today,
            )
        stmt = stmt.order_by(RegulationOverride.created_at.asc())
        r = await session.execute(stmt)
        return RegulationOverrideList(overrides=[_to_response(row) for row in r.scalars().all()])
