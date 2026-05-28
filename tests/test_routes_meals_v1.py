"""Tests for POST /api/v1/meals."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import Meal, RegulationCache


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_meal_v1_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/meals",
            json={"user_id": test_user_id, "meal_name": "breakfast", "kcal": 500},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_meal_v1_inserts_row(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "meal_date": "2026-05-28",
                "meal_time": "08:30:00",
                "meal_name": "oatmeal",
                "kcal": 450,
                "protein_g": 20,
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] > 0
    assert body["kcal"] == 450
    assert body["meal_name"] == "oatmeal"
    assert body["meal_time"] == "08:30:00"
    assert body["source"] == "api"


@pytest.mark.asyncio
async def test_meal_v1_multiple_meals_same_day(db_session, monkeypatch, test_user_id):
    """No unique constraint on (user_id, meal_date) — multiple meals per day succeed."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "meal_date": "2026-05-28",
                "meal_name": "breakfast",
                "kcal": 400,
            },
        )
        r2 = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "meal_date": "2026-05-28",
                "meal_name": "lunch",
                "kcal": 700,
            },
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]

    r = await db_session.execute(
        select(Meal).where(
            Meal.user_id == test_user_id,
            Meal.meal_date == date(2026, 5, 28),
        )
    )
    rows = r.scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_meal_v1_invalidates_cache(db_session, monkeypatch, test_user_id):
    """Pre-seed a cache row; verify it's gone after the POST."""
    db_session.add(
        RegulationCache(
            user_id=test_user_id,
            as_of_date=date.today(),
            brief_json={"placeholder": True},
            latest_ingestion_at=datetime(2026, 5, 28, tzinfo=UTC),
            latest_write_at=datetime(2026, 5, 28, tzinfo=UTC),
        )
    )
    await db_session.flush()

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "meal_name": "snack", "kcal": 200},
        )
    assert resp.status_code == 201

    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date.today(),
        )
    )
    assert r.scalar_one_or_none() is None
