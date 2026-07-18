from datetime import UTC, date, datetime

import pytest

from health_metrics.models import ActivityLog, BodyComposition, DailyMetrics, Workout
from health_metrics.regulation import brief as brief_module
from health_metrics.regulation.brief import compute_energy_today, compute_session_brief
from health_metrics.regulation.schemas import EnergyToday, SessionBrief


@pytest.mark.asyncio
async def test_energy_today_uses_dexa_rmr_and_activity(db_session, test_user_id):
    db_session.add(
        BodyComposition(user_id=test_user_id, measured_date=date(2026, 7, 1), source="dexa", lean_mass_lbs=170.0)
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
    db_session.add(DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=2650))
    await db_session.flush()

    e = await compute_energy_today(
        db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 14)
    )
    assert e is not None
    assert e.rmr_source == "dexa"
    assert e.rmr_kcal == 2036
    # 2.7mi walk * 220lb * calibrated neat_coef (0.20) = 118.8 kcal net-of-resting.
    assert 110 <= e.neat_kcal <= 125
    assert e.tdee_measured_kcal == 2650  # complete day (as_of < today)


@pytest.mark.asyncio
async def test_energy_today_partial_today_excludes_measured(db_session, test_user_id):
    db_session.add(DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=807))
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


@pytest.mark.asyncio
async def test_compute_session_brief_populates_energy_today(db_session, test_user_id):
    """M2 — compute_session_brief wires compute_energy_today into SessionBrief.energy_today."""
    as_of = date(2026, 7, 13)
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=as_of,
            whoop_recovery_score=70,
            oura_sleep_duration_min=420,
        )
    )
    await db_session.flush()

    brief = await compute_session_brief(db_session, test_user_id, as_of)
    assert brief.energy_today is not None
    assert isinstance(brief.energy_today, EnergyToday)


@pytest.mark.asyncio
async def test_compute_session_brief_energy_today_fails_soft(db_session, test_user_id, monkeypatch):
    """If compute_energy_today raises, the brief is still returned with energy_today=None
    rather than the whole session brief blowing up (energy is additive, not critical-path)."""
    as_of = date(2026, 7, 13)
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=as_of,
            whoop_recovery_score=70,
            oura_sleep_duration_min=420,
        )
    )
    await db_session.flush()

    async def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(brief_module, "compute_energy_today", _raise)

    brief = await compute_session_brief(db_session, test_user_id, as_of)
    assert isinstance(brief, SessionBrief)
    assert brief.energy_today is None
