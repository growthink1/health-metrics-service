"""GET /health — Railway/CF probe. 200 = service + DB up, 503 = DB unreachable."""

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db import AsyncSessionLocal as _session_factory

log = structlog.get_logger()
router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    try:
        async with _session_factory() as session:
            await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "db": "ok"}, status_code=200)
    except Exception as e:
        log.warning("health_db_check_failed", error=str(e))
        return JSONResponse({"status": "error", "db": "down"}, status_code=503)
