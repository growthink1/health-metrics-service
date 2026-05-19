"""APScheduler integration — runs the daily Whoop+Oura ingest twice per morning.

Two jobs:
  - 06:00 ET — `daily_ingest_yesterday` — catches late-arriving data for
    the day that just ended (workouts logged overnight, retroactive Whoop
    score corrections, Oura sleep score updates).
  - 09:00 ET — `daily_ingest_today` — catches THIS morning's recovery
    score, HRV, RHR, sleep duration. Whoop typically publishes the morning
    recovery within an hour of wake-up; by 09:00 ET it's reliable.

Both calls are idempotent — upsert on (user_id, metric_date). Running
either job twice in a day just overwrites the same row with fresh data.

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


async def _run_ingest_for_yesterday(user_id: str) -> None:
    """06:00 ET job — ingests the day that just ended."""
    tz = ZoneInfo(_SCHEDULER_TZ)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    log.info("scheduler_tick", job="yesterday", target_day=yesterday.isoformat(), user_id=user_id)
    async with AsyncSessionLocal() as session:
        try:
            result = await run_daily_ingest(day=yesterday, user_id=user_id, session=session)
            log.info("scheduler_tick_complete", job="yesterday", **result)
        except Exception as e:
            log.error("scheduler_tick_failed", job="yesterday", error=str(e), day=yesterday.isoformat())


async def _run_ingest_for_today(user_id: str) -> None:
    """09:00 ET job — ingests this morning's recovery + HRV + RHR + sleep."""
    tz = ZoneInfo(_SCHEDULER_TZ)
    today = datetime.now(tz).date()
    log.info("scheduler_tick", job="today", target_day=today.isoformat(), user_id=user_id)
    async with AsyncSessionLocal() as session:
        try:
            result = await run_daily_ingest(day=today, user_id=user_id, session=session)
            log.info("scheduler_tick_complete", job="today", **result)
        except Exception as e:
            log.error("scheduler_tick_failed", job="today", error=str(e), day=today.isoformat())


def build_scheduler(user_id: str) -> AsyncIOScheduler:
    """Build (do not start) an AsyncIOScheduler with both daily-ingest jobs."""
    sched = AsyncIOScheduler(timezone=_SCHEDULER_TZ)
    sched.add_job(
        _run_ingest_for_yesterday,
        trigger=CronTrigger(hour=6, minute=0, timezone=_SCHEDULER_TZ),
        kwargs={"user_id": user_id},
        id="daily_ingest_yesterday",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    sched.add_job(
        _run_ingest_for_today,
        trigger=CronTrigger(hour=9, minute=0, timezone=_SCHEDULER_TZ),
        kwargs={"user_id": user_id},
        id="daily_ingest_today",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return sched
