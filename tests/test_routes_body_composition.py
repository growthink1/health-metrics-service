"""Tests for POST /api/v1/body-composition."""

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import BodyComposition


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_body_comp_insert(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import body_composition as bc_route

    monkeypatch.setattr(bc_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/body-composition",
            headers={"Authorization": "Bearer dash-tok"},
            json={
                "user_id": test_user_id,
                "measured_date": "2026-07-01",
                "source": "dexa",
                "lean_mass_lbs": 170.0,
                "body_fat_pct": 18.5,
            },
        )
    assert resp.status_code == 201
    assert resp.json()["lean_mass_lbs"] == 170.0
    r = await db_session.execute(select(BodyComposition).where(BodyComposition.user_id == test_user_id))
    assert float(r.scalar_one().lean_mass_lbs) == 170.0


@pytest.mark.asyncio
async def test_body_comp_401(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import body_composition as bc_route

    monkeypatch.setattr(bc_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/body-composition", json={"measured_date": "2026-07-01", "source": "dexa"})
    assert resp.status_code == 401
