from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_workouts_filters_by_type_and_window(db_session, monkeypatch, test_user_id):
    await db_session.execute(text("""
        INSERT INTO workouts (user_id, workout_date, source, source_id,
            workout_type, started_at, duration_min, strain, avg_hr)
        VALUES
            (:u, '2026-05-12', 'whoop', 'w-1', 'cycling',
             '2026-05-12T17:00:00+00:00'::timestamptz, 45, 14.2, 135),
            (:u, '2026-05-11', 'whoop', 'w-2', 'strength',
             '2026-05-11T17:00:00+00:00'::timestamptz, 60, 11.8, 110),
            (:u, '2026-04-01', 'whoop', 'w-3', 'cycling',
             '2026-04-01T17:00:00+00:00'::timestamptz, 30, 8.0, 120)
    """), {"u": test_user_id})
    await db_session.flush()

    from contextlib import asynccontextmanager
    from health_metrics.routes import api as api_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(api_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 30d window with no filter — should see 2 (5/12 and 5/11), not 4/1
        resp = await client.get(f"/api/workouts?user_id={test_user_id}&days=30&as_of=2026-05-13")
        assert resp.status_code == 200
        assert len(resp.json()["workouts"]) == 2

        # 30d with type=cycling — should see 1
        resp = await client.get(f"/api/workouts?user_id={test_user_id}&days=30&workout_type=cycling&as_of=2026-05-13")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["workouts"]) == 1
        assert body["workouts"][0]["source_id"] == "w-1"
