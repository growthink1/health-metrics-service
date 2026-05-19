"""POST /api/workouts/manual — insert workouts row with source='manual'."""

from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import Workout


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_post_manual_workout_inserts_row(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workouts_manual as wm_route
    monkeypatch.setattr(wm_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/workouts/manual", json={
            "user_id": test_user_id,
            "date": "2026-05-19",
            "sport_name": "running",
            "duration_min": 30,
            "strain": 5.0,
            "kcal": 320,
            "notes": "easy zone 2",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["workout"]["source"] == "manual"
    assert body["workout"]["type"] == "running"
    assert body["workout"]["duration_min"] == 30
    assert isinstance(body["workout"]["id"], int)

    rows = (await db_session.execute(
        select(Workout).where(Workout.user_id == test_user_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].workout_type == "running"
    assert len(rows[0].source_id) >= 32  # uuid hex


@pytest.mark.asyncio
async def test_post_manual_workout_default_strain_kcal_null(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workouts_manual as wm_route
    monkeypatch.setattr(wm_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/workouts/manual", json={
            "user_id": test_user_id,
            "date": "2026-05-19",
            "sport_name": "yoga",
            "duration_min": 45,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["workout"]["strain"] is None
    assert body["workout"]["kcal"] is None


@pytest.mark.asyncio
async def test_post_manual_workout_rejects_bad_date(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workouts_manual as wm_route
    monkeypatch.setattr(wm_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/workouts/manual", json={
            "user_id": test_user_id, "date": "not-a-date",
            "sport_name": "running", "duration_min": 30,
        })
    assert resp.status_code in (400, 422)
