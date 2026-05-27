"""Brief builder tests -- compose data fetchers + compute_regulation."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from health_metrics.models import DailyMetrics, HealthEvent, Workout
from health_metrics.regulation.brief import compute_session_brief
from health_metrics.regulation.schemas import RegulationState


@pytest.mark.asyncio
async def test_compute_session_brief_with_no_data_returns_low_confidence(
    db_session, test_user_id
):
    """Empty DB -> confidence low, missing_inputs non-empty."""
    brief = await compute_session_brief(db_session, test_user_id, date(2026, 5, 26))
    assert brief.confidence == "low"
    assert len(brief.missing_inputs) > 0
    fields = {m.field for m in brief.missing_inputs}
    assert "oura_today" in fields
    assert "whoop_today" in fields


@pytest.mark.asyncio
async def test_compute_session_brief_uses_daily_metrics(db_session, test_user_id):
    """Today's row + 14 days of history -> state is DEFICIT or DEFICIT_CONSERVATIVE."""
    as_of = date(2026, 5, 26)
    # Seed today's row
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=as_of,
            whoop_recovery_score=85,
            oura_sleep_duration_min=420,
            oura_hrv_avg=35,
            ingestion_complete=True,
        )
    )
    # Seed 14 days of prior history (so we exit cold start)
    for i in range(1, 15):
        db_session.add(
            DailyMetrics(
                user_id=test_user_id,
                metric_date=as_of - timedelta(days=i),
                oura_sleep_duration_min=420,
            )
        )
    await db_session.flush()

    brief = await compute_session_brief(db_session, test_user_id, as_of)
    assert brief.regulation_call.state in (
        RegulationState.DEFICIT,
        RegulationState.DEFICIT_CONSERVATIVE,
    )
    assert brief.regulation_call.state not in (
        RegulationState.MAINTENANCE_SLEEP_DEFICIT,
        RegulationState.MAINTENANCE_ILLNESS,
        RegulationState.MAINTENANCE_PRE_PROCEDURE,
        RegulationState.MAINTENANCE_HRV_DEPRESSION,
    )


@pytest.mark.asyncio
async def test_compute_session_brief_with_pending_dental_routes_pre_procedure(
    db_session, test_user_id
):
    """Pending dental_procedure event within 14d -> MAINTENANCE_PRE_PROCEDURE."""
    as_of = date(2026, 5, 26)
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=as_of,
            whoop_recovery_score=70,
            oura_sleep_duration_min=420,
        )
    )
    db_session.add(
        HealthEvent(
            user_id=test_user_id,
            event_type="dental_procedure",
            status="pending",
            expected_resolution=as_of + timedelta(days=7),
        )
    )
    await db_session.flush()

    brief = await compute_session_brief(db_session, test_user_id, as_of)
    assert brief.regulation_call.state == RegulationState.MAINTENANCE_PRE_PROCEDURE
    assert "no_deficit_pre_procedure" in brief.regulation_call.overrides_today


@pytest.mark.asyncio
async def test_compute_session_brief_recent_workouts_returned(
    db_session, test_user_id
):
    """Two workouts in last 7 days -> brief.recent_workouts has 2 entries desc."""
    as_of = date(2026, 5, 26)
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=as_of - timedelta(days=3),
            source="test",
            source_id="w1",
            workout_type="strength",
            started_at=datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc),
            duration_min=45,
            max_hr=140,
            strain=Decimal("8.5"),
        )
    )
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=as_of - timedelta(days=1),
            source="test",
            source_id="w2",
            workout_type="cardio",
            started_at=datetime(2026, 5, 25, 8, 0, tzinfo=timezone.utc),
            duration_min=30,
            max_hr=160,
            strain=Decimal("11.0"),
        )
    )
    await db_session.flush()

    brief = await compute_session_brief(db_session, test_user_id, as_of)
    assert len(brief.recent_workouts) == 2
    # Order desc by workout_date -- most recent first
    assert brief.recent_workouts[0].workout_date == as_of - timedelta(days=1)
    assert brief.recent_workouts[1].workout_date == as_of - timedelta(days=3)
