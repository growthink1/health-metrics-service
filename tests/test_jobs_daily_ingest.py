"""Integration tests for the daily ingest job."""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from health_metrics.jobs.daily_ingest import run_daily_ingest
from health_metrics.models import DailyMetrics, Workout
from health_metrics.sources.base import OuraDayPayload, WhoopDayPayload, WhoopWorkout


def _oura_payload_from_fixture() -> OuraDayPayload:
    return OuraDayPayload(
        metric_date=date(2026, 5, 12),
        sleep_score=78,
        sleep_duration_min=412,
        sleep_efficiency=89.2,
        sleep_latency_min=9,
        rem_min=84,
        deep_min=70,
        light_min=258,
        awake_min=18,
        hrv_avg=45,
        rhr=58,
        temp_deviation=0.3,
        readiness_score=72,
        raw={},
    )


def _whoop_payload_and_workouts():
    payload = WhoopDayPayload(
        metric_date=date(2026, 5, 12),
        recovery_score=65,
        hrv_ms=42.5,
        rhr=60,
        sleep_performance=82,
        sleep_need_min=480,
        sleep_debt_min=90,
        day_strain=14.2,
        avg_hr=92,
        max_hr=165,
        kcal_burned=2342,
        raw={},
    )
    workouts = [
        WhoopWorkout(
            source_id="wkt-abc123",
            workout_date=date(2026, 5, 12),
            workout_type="cycling",
            started_at="2026-05-12T17:00:00.000Z",
            duration_min=45,
            avg_hr=135,
            max_hr=168,
            strain=14.2,
            kcal=387,
            zone_minutes={0: 1, 1: 4, 2: 12, 3: 15, 4: 11, 5: 2},
            raw={},
        )
    ]
    return payload, workouts


@pytest.mark.asyncio
async def test_single_date_ingest_writes_full_row(db_session, test_user_id):
    target_day = date(2026, 5, 12)

    oura_mock = AsyncMock()
    oura_mock.fetch_day.return_value = _oura_payload_from_fixture()
    oura_mock.close = AsyncMock()

    whoop_payload, whoop_workouts = _whoop_payload_and_workouts()
    whoop_mock = AsyncMock()
    whoop_mock.fetch_day.return_value = (whoop_payload, whoop_workouts)
    whoop_mock.close = AsyncMock()

    with patch("health_metrics.jobs.daily_ingest._build_whoop_client",
               new=AsyncMock(return_value=whoop_mock)), \
         patch("health_metrics.jobs.daily_ingest._build_oura_client",
               return_value=oura_mock):
        await run_daily_ingest(day=target_day, user_id=test_user_id, session=db_session, commit=False)

    res = await db_session.execute(
        select(DailyMetrics).where(
            DailyMetrics.user_id == test_user_id,
            DailyMetrics.metric_date == target_day,
        )
    )
    row = res.scalar_one()
    assert row.oura_sleep_score == 78
    assert row.oura_hrv_avg == 45
    assert row.whoop_recovery_score == 65
    assert float(row.whoop_hrv_ms) == 42.5
    assert float(row.whoop_day_strain) == 14.2
    assert row.whoop_kcal_burned == 2342
    assert row.ingestion_complete is True
    assert row.oura_status == "ok"
    assert row.whoop_status == "ok"
    assert row.unified_hrv_z is None
    assert row.unified_rhr_z is None
    assert row.unified_sleep_z is None

    res = await db_session.execute(
        select(Workout).where(
            Workout.user_id == test_user_id,
            Workout.workout_date == target_day,
        )
    )
    workouts = res.scalars().all()
    assert len(workouts) == 1
    w = workouts[0]
    assert w.source == "whoop"
    assert w.source_id == "wkt-abc123"
    assert w.workout_type == "cycling"
    assert w.duration_min == 45
    assert float(w.strain) == 14.2


@pytest.mark.asyncio
async def test_ingest_is_idempotent_for_same_date(db_session, test_user_id):
    target_day = date(2026, 5, 12)

    oura_mock = AsyncMock()
    oura_mock.fetch_day.return_value = _oura_payload_from_fixture()
    oura_mock.close = AsyncMock()

    whoop_payload, workouts_data = _whoop_payload_and_workouts()
    whoop_mock = AsyncMock()
    whoop_mock.fetch_day.return_value = (whoop_payload, workouts_data)
    whoop_mock.close = AsyncMock()

    with patch("health_metrics.jobs.daily_ingest._build_whoop_client",
               new=AsyncMock(return_value=whoop_mock)), \
         patch("health_metrics.jobs.daily_ingest._build_oura_client",
               return_value=oura_mock):
        await run_daily_ingest(day=target_day, user_id=test_user_id, session=db_session, commit=False)
        await run_daily_ingest(day=target_day, user_id=test_user_id, session=db_session, commit=False)

    res = await db_session.execute(
        select(DailyMetrics).where(
            DailyMetrics.user_id == test_user_id,
            DailyMetrics.metric_date == target_day,
        )
    )
    rows = res.scalars().all()
    assert len(rows) == 1

    res = await db_session.execute(
        select(Workout).where(Workout.user_id == test_user_id, Workout.workout_date == target_day)
    )
    workouts_db = res.scalars().all()
    assert len(workouts_db) == 1
