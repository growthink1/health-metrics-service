"""GET /api/v1/session-brief — cache-aware brief read."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..regulation.brief import compute_session_brief
from ..regulation.cache import read_cache, write_cache
from ..regulation.schemas import SessionBrief
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


@router.get("/session-brief", response_model=SessionBrief)
async def session_brief(
    user_id: str = Query(default="hugo"),
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> SessionBrief:
    as_of = date_type.today()
    log.info(
        "session_brief_request",
        user_id=user_id,
        principal=principal,
        as_of=as_of.isoformat(),
    )
    async with _session_factory() as session:
        cached = await read_cache(session, user_id, as_of)
        if cached is not None:
            log.info("session_brief_cache_hit", user_id=user_id)
            return cached
        log.info("session_brief_cache_miss_recomputing", user_id=user_id)
        brief = await compute_session_brief(session, user_id, as_of)
        await write_cache(session, user_id, as_of, brief)
        await session.commit()
        return brief
