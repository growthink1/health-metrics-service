"""Tests for GET /api/v1/weight-trend."""

from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import ManualLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_weight_trend_401_without_token(db_session, monkeypatch, test_user_id):
    """No Authorization header → 401."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import weight_trend as wt_route

    monkeypatch.setattr(wt_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/weight-trend?user_id={test_user_id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_weight_trend_returns_delta_and_revealed_tdee(db_session, monkeypatch, test_user_id):
    """Seeds 3 weight + kcal entries; verifies delta + revealed_tdee math."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    today = date_type.today()
    # Seed weight 200 → 201 → 202 over 21 days; kcal 2500/day each row.
    rows = [
        (today - timedelta(days=21), Decimal("200.0"), 2500),
        (today - timedelta(days=10), Decimal("201.0"), 2500),
        (today, Decimal("202.0"), 2500),
    ]
    for d, w, k in rows:
        db_session.add(
            ManualLog(
                user_id=test_user_id,
                log_date=d,
                weight_lbs=w,
                kcal_consumed=k,
            )
        )
    await db_session.flush()

    from health_metrics.routes import weight_trend as wt_route

    monkeypatch.setattr(wt_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/weight-trend?user_id={test_user_id}&n_days=30",
            headers={"Authorization": "Bearer dash-tok"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["n_days"] == 30
    assert body["current_lbs"] == pytest.approx(202.0, abs=0.01)
    # delta = current(202) - first(200) = +2.0 lb -- raw delta preserved for reference
    assert body["delta_lbs"] == pytest.approx(2.0, abs=0.01)
    # revealed_tdee now derived from Kalman-filtered velocity, NOT endpoint delta.
    # Avg kcal=2500, filtered velocity ≈ +0.09 lb/day across the 22-day span ->
    # revealed_tdee = 2500 - 0.09*3500 ≈ 2178. Confidence is "low" (only 3 obs < 14).
    assert body["revealed_tdee_kcal"] == pytest.approx(2178, abs=5)
    assert body["revealed_tdee_confidence"] == "low"
    assert body["filtered_weight_lbs"] == pytest.approx(201.95, abs=0.1)
    assert body["filtered_velocity_lbs_per_day"] == pytest.approx(0.092, abs=0.02)


@pytest.mark.asyncio
async def test_weight_trend_validates_n_days_bounds(db_session, monkeypatch, test_user_id):
    """n_days < 7 → 422, n_days > 180 → 422."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import weight_trend as wt_route

    monkeypatch.setattr(wt_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Below the lower bound (7)
        r1 = await client.get(
            f"/api/v1/weight-trend?user_id={test_user_id}&n_days=3",
            headers={"Authorization": "Bearer dash-tok"},
        )
        # Above the upper bound (180)
        r2 = await client.get(
            f"/api/v1/weight-trend?user_id={test_user_id}&n_days=200",
            headers={"Authorization": "Bearer dash-tok"},
        )
    assert r1.status_code == 422
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_weight_trend_empty_data_returns_nulls(db_session, monkeypatch, test_user_id):
    """No ManualLog rows for user → WeightTrend with nulls."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import weight_trend as wt_route

    monkeypatch.setattr(wt_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/weight-trend?user_id={test_user_id}&n_days=30",
            headers={"Authorization": "Bearer dash-tok"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["n_days"] == 30
    assert body["current_lbs"] is None
    assert body["delta_lbs"] is None
    assert body["revealed_tdee_kcal"] is None
