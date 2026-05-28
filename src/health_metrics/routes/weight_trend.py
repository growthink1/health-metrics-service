"""GET /api/v1/weight-trend — weight + revealed TDEE."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..regulation.brief import compute_weight_trend
from ..regulation.schemas import WeightTrend
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


@router.get("/weight-trend", response_model=WeightTrend)
async def get_weight_trend(
    user_id: str = Query(default="hugo"),
    n_days: int = Query(default=30, ge=7, le=180),
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> WeightTrend:
    log.info(
        "weight_trend_request",
        user_id=user_id,
        n_days=n_days,
        principal=principal,
    )
    async with _session_factory() as session:
        return await compute_weight_trend(session, user_id, date_type.today(), n_days=n_days)
