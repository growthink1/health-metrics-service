from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_dashboard_grid_returns_six_tiles(db_session, monkeypatch, test_user_id):
    # Seed 14 days of data
    for i in range(14):
        d = date(2026, 5, 1) + timedelta(days=i)
        await db_session.execute(text("""
            INSERT INTO daily_metrics (user_id, metric_date,
                oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
                whoop_day_strain, whoop_recovery_score,
                oura_status, whoop_status)
            VALUES (:u, :d, :hrv, :rhr, :sl, :st, :rec, 'ok', 'ok')
        """), {"u": test_user_id, "d": d, "hrv": 45 + i, "rhr": 60 - (i % 3),
               "sl": 380 + (i * 5), "st": 10 + (i * 0.3), "rec": 55 + i})
        await db_session.execute(text(
            "INSERT INTO manual_log (user_id, log_date, weight_lbs) "
            "VALUES (:u, :d, :w)"
        ), {"u": test_user_id, "d": d, "w": 220 - (i * 0.1)})
    await db_session.flush()

    from contextlib import asynccontextmanager
    from health_metrics.routes import api as api_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(api_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/dashboard/grid?user_id={test_user_id}&days=14&as_of=2026-05-14")

    assert resp.status_code == 200
    body = resp.json()
    assert body["n_days"] == 14
    metrics = {t["metric"] for t in body["tiles"]}
    assert metrics == {"hrv", "rhr", "sleep_min", "strain", "weight_lbs", "recovery"}
    hrv_tile = next(t for t in body["tiles"] if t["metric"] == "hrv")
    assert len(hrv_tile["series"]) == 14
    assert hrv_tile["current"] == 58  # 45 + 13
