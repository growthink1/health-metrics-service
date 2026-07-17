"""Tests for POST /api/v1/activities."""

from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import ActivityLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_activity_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route

    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/activities", json={"activity_type": "walk", "activity_date": "2026-07-13", "source": "strava"}
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_activity_insert_roundtrip(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route

    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/activities",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "activity_date": "2026-07-13",
                "activity_type": "walk",
                "distance_mi": 2.7,
                "duration_min": 55,
                "source": "strava",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] > 0
    assert body["activity_type"] == "walk"
    r = await db_session.execute(
        select(ActivityLog).where(ActivityLog.user_id == test_user_id, ActivityLog.activity_date == date(2026, 7, 13))
    )
    assert r.scalar_one().activity_type == "walk"


@pytest.mark.asyncio
async def test_two_activities_same_day_both_persist(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route

    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        h = {"Authorization": "Bearer dash-tok"}
        await client.post(
            "/api/v1/activities",
            headers=h,
            json={"user_id": test_user_id, "activity_date": "2026-07-13", "activity_type": "walk", "source": "strava"},
        )
        await client.post(
            "/api/v1/activities",
            headers=h,
            json={"user_id": test_user_id, "activity_date": "2026-07-13", "activity_type": "ride", "source": "peloton"},
        )
    r = await db_session.execute(select(ActivityLog).where(ActivityLog.user_id == test_user_id))
    assert len(r.scalars().all()) == 2
