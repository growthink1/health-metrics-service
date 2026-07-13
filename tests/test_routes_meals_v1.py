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


@pytest.mark.asyncio
async def test_meal_patch_updates_in_place(db_session, monkeypatch, test_user_id):
    """PATCH corrects a meal in place — no duplicate row is created."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        post = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "meal_date": "2026-07-09",
                "meal_name": "breakfast",
                "kcal": 720,
            },
        )
        assert post.status_code == 201
        meal_id = post.json()["id"]

        patch = await client.patch(
            f"/api/v1/meals/{meal_id}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"kcal": 656, "meal_name": "breakfast (corrected)"},
        )
    assert patch.status_code == 200
    body = patch.json()
    assert body["id"] == meal_id
    assert body["kcal"] == 656
    assert body["meal_name"] == "breakfast (corrected)"

    r = await db_session.execute(
        select(Meal).where(
            Meal.user_id == test_user_id,
            Meal.meal_date == date(2026, 7, 9),
        )
    )
    rows = r.scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_meal_patch_404_unknown_id(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/meals/999999",
            headers={"Authorization": "Bearer dash-tok"},
            json={"kcal": 100},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meal_patch_partial_preserves_fields(db_session, monkeypatch, test_user_id):
    """A partial PATCH must not null-out fields it doesn't set."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        post = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "meal_name": "lunch",
                "kcal": 720,
                "protein_g": 42,
            },
        )
        assert post.status_code == 201
        meal_id = post.json()["id"]

        patch = await client.patch(
            f"/api/v1/meals/{meal_id}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"kcal": 656},
        )
    assert patch.status_code == 200
    body = patch.json()
    assert body["kcal"] == 656
    assert body["protein_g"] == 42


@pytest.mark.asyncio
async def test_meal_patch_invalidates_cache(db_session, monkeypatch, test_user_id):
    """PATCH must bust today's regulation cache (meals has no updated_at)."""
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
        post = await client.post(
            "/api/v1/meals",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "meal_name": "snack", "kcal": 200},
        )
        assert post.status_code == 201
        meal_id = post.json()["id"]

        # POST already invalidated the cache; re-seed so the PATCH is what busts it.
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

        patch = await client.patch(
            f"/api/v1/meals/{meal_id}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"kcal": 250},
        )
    assert patch.status_code == 200

    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date.today(),
        )
    )
    assert r.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_meal_patch_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import meals_v1 as mv1_route

    monkeypatch.setattr(mv1_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/meals/1",
            json={"kcal": 100},
        )
    assert resp.status_code == 401
