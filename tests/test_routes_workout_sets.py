"""POST/GET/DELETE /api/workouts/{wid}/sets — per-set logging."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import Workout, WorkoutSet


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


async def _make_workout(db_session, user_id: str, day: date = date(2026, 5, 19)) -> int:
    w = Workout(
        user_id=user_id, workout_date=day, source="manual", source_id=str(uuid4()),
        workout_type="strength",
        started_at=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        duration_min=0,
    )
    db_session.add(w)
    await db_session.flush()
    return w.id


@pytest.mark.asyncio
async def test_post_set_inserts_with_set_number_1(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workout_sets as ws_route
    monkeypatch.setattr(ws_route, "_session_factory", lambda: _ctx(db_session))
    wid = await _make_workout(db_session, test_user_id)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/workouts/{wid}/sets", json={
            "user_id": test_user_id,
            "exercise": "back squat",
            "reps": 5, "weight_lbs": 315.0, "rpe": 8,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["set"]["set_number"] == 1
    assert body["set"]["exercise"] == "back squat"
    assert float(body["set"]["weight_lbs"]) == 315.0


@pytest.mark.asyncio
async def test_post_set_increments_set_number_per_exercise(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workout_sets as ws_route
    monkeypatch.setattr(ws_route, "_session_factory", lambda: _ctx(db_session))
    wid = await _make_workout(db_session, test_user_id)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Two squats + one bench → squat is 1,2 ; bench is 1
        for payload in [
            {"exercise": "back squat", "reps": 5, "weight_lbs": 315},
            {"exercise": "back squat", "reps": 5, "weight_lbs": 315},
            {"exercise": "bench press", "reps": 5, "weight_lbs": 225},
        ]:
            await client.post(f"/api/workouts/{wid}/sets", json={"user_id": test_user_id, **payload})

    rows = (await db_session.execute(
        select(WorkoutSet).where(WorkoutSet.workout_id == wid).order_by(WorkoutSet.id.asc())
    )).scalars().all()
    assert [(r.exercise, r.set_number) for r in rows] == [
        ("back squat", 1), ("back squat", 2), ("bench press", 1),
    ]


@pytest.mark.asyncio
async def test_get_sets_returns_ordered(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workout_sets as ws_route
    monkeypatch.setattr(ws_route, "_session_factory", lambda: _ctx(db_session))
    wid = await _make_workout(db_session, test_user_id)

    db_session.add_all([
        WorkoutSet(user_id=test_user_id, workout_id=wid, set_number=2, exercise="squat", reps=5, weight_lbs=315),
        WorkoutSet(user_id=test_user_id, workout_id=wid, set_number=1, exercise="squat", reps=5, weight_lbs=315),
    ])
    await db_session.flush()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/workouts/{wid}/sets?user_id={test_user_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["set_number"] for s in body["sets"]] == [1, 2]


@pytest.mark.asyncio
async def test_delete_set_removes_row(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workout_sets as ws_route
    monkeypatch.setattr(ws_route, "_session_factory", lambda: _ctx(db_session))
    wid = await _make_workout(db_session, test_user_id)
    s = WorkoutSet(user_id=test_user_id, workout_id=wid, set_number=1, exercise="squat", reps=5)
    db_session.add(s)
    await db_session.flush()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/workouts/sets/{s.id}?user_id={test_user_id}")
    assert resp.status_code == 200

    remaining = (await db_session.execute(
        select(WorkoutSet).where(WorkoutSet.id == s.id)
    )).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_post_set_404_unknown_workout(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import workout_sets as ws_route
    monkeypatch.setattr(ws_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/workouts/999999/sets", json={
            "user_id": test_user_id, "exercise": "squat", "reps": 5,
        })
    assert resp.status_code == 404
