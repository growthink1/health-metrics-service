"""Scheduler registers the daily-ingest cron job at the configured time."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def test_build_scheduler_registers_daily_ingest_job():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="hugo")
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1, f"expected 1 job, got {len(jobs)}"
        job = jobs[0]
        assert job.id == "daily_ingest"
        assert isinstance(job.trigger, CronTrigger)
        # CronTrigger.fields is a list — find the hour field
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        assert str(hour_field) == "6", f"expected hour=6, got {hour_field}"
        # Timezone is America/New_York
        assert str(job.trigger.timezone) == "America/New_York"
    finally:
        if sched.running:
            sched.shutdown(wait=False)


def test_build_scheduler_uses_correct_user_id():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="test_user_xyz")
    try:
        jobs = sched.get_jobs()
        # The job kwargs should bind user_id
        assert jobs[0].kwargs.get("user_id") == "test_user_xyz"
    finally:
        if sched.running:
            sched.shutdown(wait=False)
