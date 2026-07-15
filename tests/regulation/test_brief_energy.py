from datetime import UTC, date, datetime

import pytest

from health_metrics.models import ActivityLog, BodyComposition, DailyMetrics, Workout
from health_metrics.regulation.brief import compute_energy_today


@pytest.mark.asyncio
async def test_energy_today_uses_dexa_rmr_and_activity(db_session, test_user_id):
    db_session.add(
        BodyComposition(
            user_id=test_user_id, measured_date=date(2026, 7, 1), source="dexa", lean_mass_lbs=170.0
        )
    )
    db_session.add(
        ActivityLog(
            user_id=test_user_id,
            activity_date=date(2026, 7, 13),
            activity_type="walk",
            distance_mi=2.7,
            duration_min=55,
            source="strava",
        )
    )
    db_session.add(
        DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=2650)
    )
    await db_session.flush()

    e = await compute_energy_today(
        db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 14)
    )
    assert e is not None
    assert e.rmr_source == "dexa"
    assert e.rmr_kcal == 2036
    # 2.7mi walk * 220lb * seed neat_coef (0.53) = 314.8 kcal net-of-resting.
    # (Brief's original range of 110-125 was stale vs. the merged Task 6 formula/coef,
    # independently asserted at 2.7*220*0.53 in tests/regulation/test_energy.py.)
    assert 310 <= e.neat_kcal <= 320
    assert e.tdee_measured_kcal == 2650  # complete day (as_of < today)


@pytest.mark.asyncio
async def test_energy_today_partial_today_excludes_measured(db_session, test_user_id):
    db_session.add(
        DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=807)
    )
    await db_session.flush()
    e = await compute_energy_today(
        db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 13)
    )
    assert e.tdee_measured_kcal is None  # as_of == today → partial
    assert e.rmr_source == "fallback"  # no body_composition row


@pytest.mark.asyncio
async def test_energy_today_dedups_whoop_and_manual_walk(db_session, test_user_id):
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=date(2026, 7, 13),
            source="whoop",
            source_id="w1",
            workout_type="walking",
            started_at=datetime(2026, 7, 13, tzinfo=UTC),
            duration_min=60,
            kcal=90,
        )
    )
    db_session.add(
        ActivityLog(
            user_id=test_user_id,
            activity_date=date(2026, 7, 13),
            activity_type="walk",
            distance_mi=2.7,
            duration_min=55,
            source="strava",
        )
    )
    await db_session.flush()
    e = await compute_energy_today(
        db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 14)
    )
    # Only ONE walk counted (manual wins) — not 90 + 315
    assert len(e.activities_counted) == 1
    assert "activity_log" in e.activities_counted[0]
