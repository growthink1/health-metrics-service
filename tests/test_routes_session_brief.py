"""Tests for GET /api/v1/session-brief."""

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_session_brief_401_without_token(db_session, monkeypatch, test_user_id):
    """No Authorization header → 401."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import session_brief as sb_route

    monkeypatch.setattr(sb_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/session-brief?user_id={test_user_id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_brief_returns_brief_with_dashboard_token(db_session, monkeypatch, test_user_id):
    """Valid dashboard token + cold cache → recompute + write back + return."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import session_brief as sb_route

    monkeypatch.setattr(sb_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/session-brief?user_id={test_user_id}",
            headers={"Authorization": "Bearer dash-tok"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == test_user_id
    assert "regulation_call" in body
    assert "daily_snapshot" in body
    assert "confidence" in body
    assert body["confidence"] in {"high", "medium", "low"}


@pytest.mark.asyncio
async def test_session_brief_accepts_mcp_token(db_session, monkeypatch, test_user_id):
    """Valid MCP token → 200."""
    monkeypatch.setenv("HEALTH_API_TOKEN_MCP", "mcp-tok")
    from health_metrics.routes import session_brief as sb_route

    monkeypatch.setattr(sb_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/session-brief?user_id={test_user_id}",
            headers={"Authorization": "Bearer mcp-tok"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_session_brief_cache_hit_returns_same_generated_at(db_session, monkeypatch, test_user_id):
    """Second request after a cold-cache write returns the cached brief
    (same generated_at timestamp — proves the cache was hit, not recomputed)."""
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import session_brief as sb_route

    monkeypatch.setattr(sb_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    headers = {"Authorization": "Bearer dash-tok"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get(f"/api/v1/session-brief?user_id={test_user_id}", headers=headers)
        r2 = await client.get(f"/api/v1/session-brief?user_id={test_user_id}", headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["generated_at"] == r2.json()["generated_at"]
