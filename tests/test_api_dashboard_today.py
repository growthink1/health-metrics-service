from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_dashboard_today_returns_recommendation_and_today_strip(db_session, monkeypatch, test_user_id):
    # Seed 3 days of daily_metrics + manual_log
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date,
            oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
            unified_hrv_z, unified_rhr_z, whoop_sleep_debt_min,
            whoop_day_strain, oura_status, whoop_status, ingestion_complete)
        VALUES
            (:u, :d1, 45, 60, 400, -1.0, 0.5, 200, 12.0, 'ok', 'ok', TRUE),
            (:u, :d2, 47, 58, 410, -0.8, 0.3, 180, 11.0, 'ok', 'ok', TRUE),
            (:u, :d3, 46, 59, 380, -1.2, 0.7, 220, 13.0, 'ok', 'ok', TRUE)
    """), {"u": test_user_id, "d1": date(2026, 5, 11), "d2": date(2026, 5, 12), "d3": date(2026, 5, 13)})
    await db_session.execute(text(
        "INSERT INTO manual_log (user_id, log_date, subjective_energy, subjective_mood, subjective_hunger) "
        "VALUES (:u, :d, 6, 7, 5)"
    ), {"u": test_user_id, "d": date(2026, 5, 13)})
    await db_session.flush()

    # Patch _session_factory so the route uses our test session.
    from health_metrics.routes import api as api_route
    monkeypatch.setattr(api_route, "_session_factory", lambda: db_session_context(db_session))

    # Patch narration to skip Anthropic
    async def fake_narration(*args, **kwargs):
        return "HRV depressed 1.2σ over 3 days — holding deficit pause."

    monkeypatch.setattr(api_route, "generate_narration", fake_narration)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/dashboard/today?user_id={test_user_id}&as_of=2026-05-13")

    assert resp.status_code == 200
    body = resp.json()
    assert body["metric_date"] == "2026-05-13"
    assert body["today_strip"]["recommendation"] in (
        "deficit", "deficit_conservative", "maintenance", "deload"
    )
    assert body["today_strip"]["today_hrv_ms"] == 46
    assert body["today_strip"]["hrv_z_3d_avg"] == pytest.approx(-1.0, abs=0.01)
    assert "subjective" not in body["today_strip"]["log_status"]  # subjective IS logged
    assert body["narration"] == "HRV depressed 1.2σ over 3 days — holding deficit pause."


def db_session_context(session):
    """Helper that returns a context manager wrapping an existing session.

    The route opens _session_factory() — we monkeypatch the factory to
    return THIS context manager around the test session so we share
    the transaction.
    """
    @asynccontextmanager
    async def _ctx():
        yield session
    return _ctx()
