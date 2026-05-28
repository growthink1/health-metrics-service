"""POST /api/v1/manual-entry — log weight + nutrition + subjective markers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import ManualLog
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


class ManualEntryPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = "hugo"
    log_date: date_type = Field(default_factory=date_type.today, alias="entry_date")
    weight_lbs: float | None = None
    kcal_consumed: int | None = None
    protein_g: int | None = None
    fat_g: int | None = None
    carbs_g: int | None = None
    subjective_energy: int | None = Field(default=None, ge=1, le=10, alias="energy_1_10")
    subjective_mood: int | None = Field(default=None, ge=1, le=10, alias="mood_1_10")
    subjective_hunger: int | None = Field(default=None, ge=1, le=10, alias="hunger_1_10")
    soreness_1_10: int | None = Field(default=None, ge=1, le=10)
    sleep_subjective_1_10: int | None = Field(default=None, ge=1, le=10)
    notes: str | None = None


class ManualEntryResponse(BaseModel):
    id: int
    user_id: str
    log_date: date_type
    weight_lbs: float | None
    kcal_consumed: int | None
    protein_g: int | None
    fat_g: int | None
    carbs_g: int | None
    subjective_energy: int | None
    subjective_mood: int | None
    subjective_hunger: int | None
    soreness_1_10: int | None
    sleep_subjective_1_10: int | None
    notes: str | None


@router.post("/manual-entry", response_model=ManualEntryResponse, status_code=201)
async def post_manual_entry(
    payload: ManualEntryPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> ManualEntryResponse:
    log.info(
        "manual_entry_write",
        user_id=payload.user_id,
        log_date=payload.log_date.isoformat(),
        principal=principal,
    )
    async with _session_factory() as session:
        # Upsert on uq_manual_log_user_date
        stmt = pg_insert(ManualLog).values(
            user_id=payload.user_id,
            log_date=payload.log_date,
            weight_lbs=Decimal(str(payload.weight_lbs)) if payload.weight_lbs is not None else None,
            kcal_consumed=payload.kcal_consumed,
            protein_g=payload.protein_g,
            fat_g=payload.fat_g,
            carbs_g=payload.carbs_g,
            subjective_energy=payload.subjective_energy,
            subjective_mood=payload.subjective_mood,
            subjective_hunger=payload.subjective_hunger,
            soreness_1_10=payload.soreness_1_10,
            sleep_subjective_1_10=payload.sleep_subjective_1_10,
            notes=payload.notes,
        )
        # Build the set_ dict from non-None payload fields so partial updates
        # don't null-out existing data
        updates: dict = {}
        for field in (
            "weight_lbs",
            "kcal_consumed",
            "protein_g",
            "fat_g",
            "carbs_g",
            "subjective_energy",
            "subjective_mood",
            "subjective_hunger",
            "soreness_1_10",
            "sleep_subjective_1_10",
            "notes",
        ):
            v = getattr(payload, field)
            if v is not None:
                if field == "weight_lbs":
                    updates[field] = Decimal(str(v))
                else:
                    updates[field] = v
        updates["updated_at"] = datetime.now(UTC)

        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "log_date"],
            set_=updates,
        ).returning(ManualLog)
        result = await session.execute(stmt)
        row = result.scalar_one()

        # Cache invalidation — affects today's brief regardless of the entry's log_date
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()

        return ManualEntryResponse(
            id=row.id,
            user_id=row.user_id,
            log_date=row.log_date,
            weight_lbs=float(row.weight_lbs) if row.weight_lbs is not None else None,
            kcal_consumed=row.kcal_consumed,
            protein_g=row.protein_g,
            fat_g=row.fat_g,
            carbs_g=row.carbs_g,
            subjective_energy=row.subjective_energy,
            subjective_mood=row.subjective_mood,
            subjective_hunger=row.subjective_hunger,
            soreness_1_10=row.soreness_1_10,
            sleep_subjective_1_10=row.sleep_subjective_1_10,
            notes=row.notes,
        )
