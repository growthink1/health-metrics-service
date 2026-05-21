"""GET /api/goals/status + /api/goals/history."""
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import Goal, GoalRecommendation, Milestone, Subgoal


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_goal_status_empty_when_no_active_goal(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import goals as goals_route
    monkeypatch.setattr(goals_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/goals/status?user_id={test_user_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] is None
    assert body["trajectory"] is None
    assert body["milestones"] == []
    assert body["subgoals"] == []
    assert body["recommendation"] is None


@pytest.mark.asyncio
async def test_goal_status_returns_active_payload(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import goals as goals_route
    monkeypatch.setattr(goals_route, "_session_factory", lambda: _ctx(db_session))

    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        start_value=Decimal("200"), target_value=Decimal("185"),
        start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g)
    await db_session.flush()
    db_session.add_all([
        Milestone(goal_id=g.id, target_value=Decimal("195"), target_date=date(2026, 6, 1)),
        Subgoal(goal_id=g.id, preset="avg_kcal", target_value=Decimal("2100"), window_days=7),
        GoalRecommendation(
            goal_id=g.id, rec_date=date(2026, 5, 20),
            trajectory={"method": "bayesian_normal_normal", "current_value": 192.7,
                        "projected_value_mean": 187.2, "p_on_pace": 0.4,
                        "projected_value_ci_low": 184.0, "projected_value_ci_high": 190.4,
                        "confidence": "med", "data_points_used": 20},
            actions=[{"category": "nutrition", "change": "-100 kcal", "rationale": "off pace"}],
            narration="On pace narrowly.", signals_hash="abc",
        ),
    ])
    await db_session.flush()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/goals/status?user_id={test_user_id}")
    body = resp.json()
    assert body["goal"]["name"] == "Lose 15 lbs"
    assert len(body["milestones"]) == 1
    assert len(body["subgoals"]) == 1
    assert body["subgoals"][0]["preset"] == "avg_kcal"
    assert body["recommendation"]["narration"] == "On pace narrowly."


@pytest.mark.asyncio
async def test_goal_history_returns_recent_rows(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import goals as goals_route
    monkeypatch.setattr(goals_route, "_session_factory", lambda: _ctx(db_session))

    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g); await db_session.flush()
    for i in range(5):
        db_session.add(GoalRecommendation(
            goal_id=g.id, rec_date=date(2026, 5, 15 + i),
            trajectory={"method": "bayesian_normal_normal"},
            actions=[], narration=f"day {i}", signals_hash=f"h{i}",
        ))
    await db_session.flush()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/goals/history?user_id={test_user_id}&days=3")
    body = resp.json()
    assert len(body["rows"]) == 3
