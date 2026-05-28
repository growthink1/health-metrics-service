"""Tests for POST /api/v1/manual-entry."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import ManualLog, RegulationCache


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_manual_entry_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import manual_entry as me_route

    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/manual-entry",
            json={"user_id": test_user_id, "weight_lbs": 180.5},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_manual_entry_inserts_row(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import manual_entry as me_route

    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/manual-entry",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "log_date": "2026-05-28",
                "weight_lbs": 180.5,
                "subjective_energy": 7,
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["weight_lbs"] == 180.5
    assert body["subjective_energy"] == 7
    # Confirm row in DB
    r = await db_session.execute(
        select(ManualLog).where(
            ManualLog.user_id == test_user_id,
            ManualLog.log_date == date(2026, 5, 28),
        )
    )
    row = r.scalar_one()
    assert float(row.weight_lbs) == 180.5


@pytest.mark.asyncio
async def test_manual_entry_upsert_preserves_other_fields(db_session, monkeypatch, test_user_id):
    """POST with only weight should NOT null-out previously-set kcal."""
    from decimal import Decimal

    db_session.add(
        ManualLog(
            user_id=test_user_id,
            log_date=date(2026, 5, 28),
            weight_lbs=Decimal("180.0"),
            kcal_consumed=2500,
        )
    )
    await db_session.flush()

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import manual_entry as me_route

    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/manual-entry",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "log_date": "2026-05-28", "weight_lbs": 181.0},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["weight_lbs"] == 181.0
    assert body["kcal_consumed"] == 2500  # preserved


@pytest.mark.asyncio
async def test_manual_entry_rejects_out_of_range_subjective(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import manual_entry as me_route

    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/manual-entry",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "subjective_energy": 11},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_manual_entry_invalidates_cache(db_session, monkeypatch, test_user_id):
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
    from health_metrics.routes import manual_entry as me_route

    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/manual-entry",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "weight_lbs": 180.5},
        )
    assert resp.status_code == 201

    # Cache row should be gone
    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date.today(),
        )
    )
    assert r.scalar_one_or_none() is None
