"""Manual ingest trigger — POST /ingest/daily?date=YYYY-MM-DD"""

from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..jobs.daily_ingest import run_daily_ingest

log = structlog.get_logger()
router = APIRouter()


@router.post("/ingest/daily")
async def trigger_daily_ingest(
    date: str | None = Query(default=None, description="YYYY-MM-DD; default = yesterday in service TZ"),
    user_id: str | None = Query(default=None),
):
    settings = get_settings()
    target_user = user_id or settings.user_id

    if date is None:
        tz = ZoneInfo(settings.timezone)
        target_day = datetime.now(tz).date() - timedelta(days=1)
    else:
        try:
            target_day = date_type.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid date: {e}")

    async with AsyncSessionLocal() as session:
        result = await run_daily_ingest(day=target_day, user_id=target_user, session=session)
    return result
