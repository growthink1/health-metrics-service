"""POST /api/v1/body-composition — log a DEXA / body-comp reading. Insert-only."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import BodyComposition
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


def _dec(v: float | None) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


class BodyCompPayload(BaseModel):
    user_id: str = "hugo"
    measured_date: date_type = Field(default_factory=date_type.today)
    source: str = "dexa"
    weight_lbs: float | None = None
    body_fat_pct: float | None = None
    lean_mass_lbs: float | None = None
    fat_mass_lbs: float | None = None
    notes: str | None = None


class BodyCompResponse(BaseModel):
    id: int
    user_id: str
    measured_date: date_type
    source: str
    weight_lbs: float | None
    body_fat_pct: float | None
    lean_mass_lbs: float | None
    fat_mass_lbs: float | None
    notes: str | None


@router.post("/body-composition", response_model=BodyCompResponse, status_code=201)
async def post_body_composition(
    payload: BodyCompPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> BodyCompResponse:
    log.info(
        "body_comp_write",
        user_id=payload.user_id,
        measured_date=payload.measured_date.isoformat(),
        principal=principal,
    )
    async with _session_factory() as session:
        row = BodyComposition(
            user_id=payload.user_id,
            measured_date=payload.measured_date,
            source=payload.source,
            weight_lbs=_dec(payload.weight_lbs),
            body_fat_pct=_dec(payload.body_fat_pct),
            lean_mass_lbs=_dec(payload.lean_mass_lbs),
            fat_mass_lbs=_dec(payload.fat_mass_lbs),
            notes=payload.notes,
        )
        session.add(row)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(row)
        return BodyCompResponse(
            id=row.id,
            user_id=row.user_id,
            measured_date=row.measured_date,
            source=row.source,
            weight_lbs=float(row.weight_lbs) if row.weight_lbs is not None else None,
            body_fat_pct=float(row.body_fat_pct) if row.body_fat_pct is not None else None,
            lean_mass_lbs=float(row.lean_mass_lbs) if row.lean_mass_lbs is not None else None,
            fat_mass_lbs=float(row.fat_mass_lbs) if row.fat_mass_lbs is not None else None,
            notes=row.notes,
        )
