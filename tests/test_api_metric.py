from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_metric_hrv_returns_series_with_stats(db_session, monkeypatch, test_user_id):
    for i in range(14):
        d = date(2026, 5, 1) + timedelta(days=i)
        await db_session.execute(text("""
            INSERT INTO daily_metrics (user_id, metric_date,
                oura_hrv_avg, unified_hrv_z, oura_status, whoop_status)
            VALUES (:u, :d, :hrv, :z, 'ok', 'ok')
        """), {"u": test_user_id, "d": d, "hrv": 45 + (i % 7), "z": -0.5 + (i * 0.05)})
    await db_session.flush()

    from contextlib import asynccontextmanager
    from health_metrics.routes import api as api_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(api_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/metric/hrv?user_id={test_user_id}&days=14&as_of=2026-05-14")

    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "hrv"
    assert body["n_days"] == 14
    assert len(body["series"]) == 14
    assert "mean" in body["stats"]
    assert "std" in body["stats"]
    assert "slope_per_day" in body["stats"]


@pytest.mark.asyncio
async def test_metric_unknown_returns_400(db_session, monkeypatch):
    from contextlib import asynccontextmanager
    from health_metrics.routes import api as api_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(api_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/metric/unknown?user_id=hugo&days=14")
    assert resp.status_code == 400
