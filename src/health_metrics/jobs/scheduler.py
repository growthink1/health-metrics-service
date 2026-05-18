"""APScheduler integration — runs the daily Whoop+Oura ingest at 06:00 ET.

Idempotent: running multiple times in a day just upserts the same daily_metrics row.
Runs in-process inside the FastAPI worker — only safe with --workers 1.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import AsyncSessionLocal
from .daily_ingest import run_daily_ingest

log = structlog.get_logger()

_SCHEDULER_TZ = "America/New_York"
_SCHEDULER_HOUR = 6
_SCHEDULER_MINUTE = 0


async def _run_ingest_for_yesterday(user_id: str) -> None:
    """Callback the scheduler fires every day. Ingests yesterday in ET."""
    tz = ZoneInfo(_SCHEDULER_TZ)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    log.info("scheduler_tick", target_day=yesterday.isoformat(), user_id=user_id)
    async with AsyncSessionLocal() as session:
        try:
            result = await run_daily_ingest(day=yesterday, user_id=user_id, session=session)
            log.info("scheduler_tick_complete", **result)
        except Exception as e:
            log.error("scheduler_tick_failed", error=str(e), day=yesterday.isoformat())


def build_scheduler(user_id: str) -> AsyncIOScheduler:
    """Build (do not start) an AsyncIOScheduler with the daily-ingest job."""
    sched = AsyncIOScheduler(timezone=_SCHEDULER_TZ)
    sched.add_job(
        _run_ingest_for_yesterday,
        trigger=CronTrigger(hour=_SCHEDULER_HOUR, minute=_SCHEDULER_MINUTE, timezone=_SCHEDULER_TZ),
        kwargs={"user_id": user_id},
        id="daily_ingest",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # 1h grace window for tick recovery after restart
    )
    return sched
