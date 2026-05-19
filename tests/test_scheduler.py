"""Scheduler registers both daily-ingest cron jobs (06:00 yesterday + 09:00 today)."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def test_build_scheduler_registers_both_daily_jobs():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="hugo")
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        assert set(jobs.keys()) == {"daily_ingest_yesterday", "daily_ingest_today"}, \
            f"expected both jobs, got {list(jobs.keys())}"

        # 06:00 ET job — ingests yesterday
        y = jobs["daily_ingest_yesterday"]
        assert isinstance(y.trigger, CronTrigger)
        hour_field = next(f for f in y.trigger.fields if f.name == "hour")
        assert str(hour_field) == "6", f"yesterday job hour: expected 6, got {hour_field}"
        assert str(y.trigger.timezone) == "America/New_York"

        # 09:00 ET job — ingests today
        t = jobs["daily_ingest_today"]
        assert isinstance(t.trigger, CronTrigger)
        hour_field = next(f for f in t.trigger.fields if f.name == "hour")
        assert str(hour_field) == "9", f"today job hour: expected 9, got {hour_field}"
        assert str(t.trigger.timezone) == "America/New_York"
    finally:
        if sched.running:
            sched.shutdown(wait=False)


def test_build_scheduler_uses_correct_user_id():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="test_user_xyz")
    try:
        for j in sched.get_jobs():
            assert j.kwargs.get("user_id") == "test_user_xyz", \
                f"job {j.id} kwargs missing test_user_id"
    finally:
        if sched.running:
            sched.shutdown(wait=False)
