"""Tests for POST + PATCH /api/v1/health-events."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import HealthEvent, RegulationCache


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_health_event_post_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/health-events",
            json={
                "user_id": test_user_id,
                "event_type": "dental_procedure",
                "status": "pending",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_event_patch_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/health-events/{uuid.uuid4()}",
            json={"status": "resolved"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_event_post_happy_path(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/health-events",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "event_type": "dental_procedure",
                "status": "pending",
                "started_at": "2026-05-28",
                "expected_resolution": "2026-06-01",
                "affects": ["training", "nutrition"],
                "notes": "wisdom tooth extraction",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["event_type"] == "dental_procedure"
    assert body["status"] == "pending"
    assert body["affects"] == ["training", "nutrition"]
    # ID should parse as a UUID
    uuid.UUID(body["id"])


@pytest.mark.asyncio
async def test_health_event_post_rejects_unknown_event_type(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/health-events",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "event_type": "alien_abduction",
                "status": "active",
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_health_event_patch_happy_path(db_session, monkeypatch, test_user_id):
    """Create an event via DB, PATCH to resolve it."""
    ev = HealthEvent(
        user_id=test_user_id,
        event_type="acute_infection",
        status="active",
        affects=["training"],
    )
    db_session.add(ev)
    await db_session.flush()
    event_id = ev.id

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/health-events/{event_id}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"status": "resolved", "notes": "all better"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["notes"] == "all better"
    # affects untouched (not in payload)
    assert body["affects"] == ["training"]


@pytest.mark.asyncio
async def test_health_event_patch_404_unknown_uuid(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/health-events/{uuid.uuid4()}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"status": "resolved"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_health_event_post_invalidates_cache(db_session, monkeypatch, test_user_id):
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
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/health-events",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "event_type": "fever",
                "status": "active",
            },
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
async def test_health_event_patch_invalidates_cache(db_session, monkeypatch, test_user_id):
    ev = HealthEvent(
        user_id=test_user_id,
        event_type="injury",
        status="active",
    )
    db_session.add(ev)
    await db_session.flush()
    event_id = ev.id

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
    from health_metrics.routes import health_events as he_route

    monkeypatch.setattr(he_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/health-events/{event_id}",
            headers={"Authorization": "Bearer dash-tok"},
            json={"status": "resolving"},
        )
    assert resp.status_code == 200
    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date.today(),
        )
    )
    assert r.scalar_one_or_none() is None
