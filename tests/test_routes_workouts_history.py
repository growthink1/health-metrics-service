"""Tests for GET /api/v1/workouts."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import Workout


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_workouts_history_401_without_token(db_session, monkeypatch, test_user_id):
    """No Authorization header → 401."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import workouts_history as wh_route

    monkeypatch.setattr(wh_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/workouts?user_id={test_user_id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_workouts_history_returns_recent_workouts(db_session, monkeypatch, test_user_id):
    """Seeds 3 workouts in the last week + 1 too old → endpoint returns the 3."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    today = date_type.today()
    for i in range(3):
        d = today - timedelta(days=i)
        db_session.add(
            Workout(
                user_id=test_user_id,
                workout_date=d,
                source="whoop",
                source_id=f"w-{test_user_id}-{i}",
                workout_type="strength",
                started_at=datetime.combine(d, datetime.min.time(), tzinfo=UTC),
                duration_min=45,
                avg_hr=140,
                max_hr=170,
                strain=Decimal("12.5"),
                kcal=400,
            )
        )
    # Too old — beyond default 14-day window
    too_old = today - timedelta(days=30)
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=too_old,
            source="whoop",
            source_id=f"w-{test_user_id}-old",
            workout_type="cardio",
            started_at=datetime.combine(too_old, datetime.min.time(), tzinfo=UTC),
            duration_min=30,
            avg_hr=130,
            max_hr=160,
            strain=Decimal("8.0"),
            kcal=300,
        )
    )
    await db_session.flush()

    from health_metrics.routes import workouts_history as wh_route

    monkeypatch.setattr(wh_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/workouts?user_id={test_user_id}",
            headers={"Authorization": "Bearer dash-tok"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == test_user_id
    assert body["n_days"] == 14
    assert len(body["workouts"]) == 3  # not the too-old one
    # Ordered desc by workout_date
    dates = [w["workout_date"] for w in body["workouts"]]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_workouts_history_validates_n_days_upper_bound(db_session, monkeypatch, test_user_id):
    """n_days > 90 → 422."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import workouts_history as wh_route

    monkeypatch.setattr(wh_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/workouts?user_id={test_user_id}&n_days=120",
            headers={"Authorization": "Bearer dash-tok"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_workouts_history_age_predicted_max_hr_propagates(db_session, monkeypatch, test_user_id):
    """max_hr_pct_age_predicted = max_hr / (220 - age). Default age 44 → 176."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    today = date_type.today()
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=today,
            source="whoop",
            source_id=f"w-{test_user_id}-hr",
            workout_type="cardio",
            started_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
            duration_min=60,
            avg_hr=150,
            max_hr=176,  # exactly age-predicted max for age 44 → 1.0
            strain=Decimal("14.0"),
            kcal=600,
        )
    )
    await db_session.flush()

    from health_metrics.routes import workouts_history as wh_route

    monkeypatch.setattr(wh_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Default user_id "hugo" age 44 → 176 max
        resp = await client.get(
            f"/api/v1/workouts?user_id={test_user_id}",
            headers={"Authorization": "Bearer dash-tok"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["workouts"]) == 1
    # Unknown user_id falls back to age 44 → 176 max; 176/176 = 1.0
    assert body["workouts"][0]["max_hr_pct_age_predicted"] == pytest.approx(1.0, abs=0.001)
