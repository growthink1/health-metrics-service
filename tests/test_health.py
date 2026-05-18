"""Health endpoint tests — used by Railway for restart decisions."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


@pytest.mark.asyncio
async def test_health_returns_200_when_db_up(db_session, monkeypatch):
    from health_metrics.routes import health as health_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(health_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


@pytest.mark.asyncio
async def test_health_returns_503_when_db_down(monkeypatch):
    from health_metrics.routes import health as health_route

    @asynccontextmanager
    async def _ctx():
        # Yield a session that raises on execute()
        class _BrokenSession:
            async def execute(self, *args, **kwargs):
                raise OperationalError("conn", None, Exception("connection refused"))
        yield _BrokenSession()

    monkeypatch.setattr(health_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["db"] == "down"
