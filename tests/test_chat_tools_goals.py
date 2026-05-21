"""set_primary_goal, add_subgoal, update_goal, get_goal_status — chat tool handlers."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from health_metrics.chat_tools import (
    READ_TOOLS,
    TOOL_DEFINITIONS,
    WRITE_TOOLS,
    add_subgoal,
    get_goal_status,
    set_primary_goal,
    update_goal,
)
from health_metrics.models import Goal, Milestone, Subgoal


def test_v4_tool_definitions_present():
    names = {t.get("name") for t in TOOL_DEFINITIONS}
    assert {"set_primary_goal", "add_subgoal", "update_goal", "get_goal_status", "web_search"} <= names
    assert "get_goal_status" in READ_TOOLS
    assert {"set_primary_goal", "add_subgoal", "update_goal"} <= WRITE_TOOLS


def test_web_search_tool_declaration_shape():
    web = next(t for t in TOOL_DEFINITIONS if t.get("name") == "web_search")
    assert web.get("type") == "web_search_20250305"
    assert web.get("max_uses") == 3


@pytest.mark.asyncio
async def test_set_primary_goal_archives_prior_and_creates_milestones(db_session, monkeypatch, test_user_id):
    # Pre-existing active primary
    prior = Goal(
        user_id=test_user_id, goal_type="weight", name="old goal", metric="weight_lbs",
        target_value=Decimal("190"), start_date=date(2026, 1, 1), target_date=date(2026, 4, 1),
        is_primary=True, status="active",
    )
    db_session.add(prior)
    await db_session.flush()

    # Mock the recompute call so we don't need a full data fixture
    from health_metrics import chat_tools
    monkeypatch.setattr(chat_tools, "_initial_goal_recompute", AsyncMock(return_value=None))

    result = await set_primary_goal(
        db_session, test_user_id,
        goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=185, target_date="2026-08-01",
    )
    assert result["ok"] is True
    # Old archived
    prior_after = (await db_session.execute(select(Goal).where(Goal.id == prior.id))).scalar_one()
    assert prior_after.status == "archived"
    # New created
    new = (await db_session.execute(
        select(Goal).where(Goal.user_id == test_user_id, Goal.status == "active")
    )).scalar_one()
    assert new.name == "Lose 15 lbs"
    # Milestones generated (3 monthly checkpoints over ~10 weeks => 2 mid + final, or just final)
    ms = (await db_session.execute(select(Milestone).where(Milestone.goal_id == new.id))).scalars().all()
    assert len(ms) >= 1
    assert ms[-1].target_date == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_add_subgoal_attaches_to_active_primary(db_session, test_user_id):
    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g)
    await db_session.flush()

    result = await add_subgoal(db_session, test_user_id, preset="avg_kcal", target_value=2100, window_days=7)
    assert result["ok"] is True
    sg = (await db_session.execute(select(Subgoal).where(Subgoal.goal_id == g.id))).scalar_one()
    assert sg.preset == "avg_kcal"
    assert float(sg.target_value) == 2100.0


@pytest.mark.asyncio
async def test_add_subgoal_rejects_when_no_active_goal(db_session, test_user_id):
    result = await add_subgoal(db_session, test_user_id, preset="avg_kcal", target_value=2100)
    assert result["ok"] is False
    assert "no active" in result["error"].lower()


@pytest.mark.asyncio
async def test_update_goal_status_and_target_date(db_session, test_user_id):
    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g); await db_session.flush()

    r = await update_goal(db_session, test_user_id, status="achieved")
    assert r["ok"] is True
    g2 = (await db_session.execute(select(Goal).where(Goal.id == g.id))).scalar_one()
    assert g2.status == "achieved"


@pytest.mark.asyncio
async def test_update_goal_rejects_past_target_date(db_session, test_user_id):
    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g); await db_session.flush()
    r = await update_goal(db_session, test_user_id, target_date="2020-01-01")
    assert r["ok"] is False
    assert "future" in r["error"].lower()


@pytest.mark.asyncio
async def test_get_goal_status_returns_empty_shape(db_session, test_user_id):
    r = await get_goal_status(db_session, test_user_id)
    assert r["ok"] is True
    assert r["result"]["goal"] is None


@pytest.mark.asyncio
async def test_set_primary_goal_returns_warning_when_recompute_fails(db_session, monkeypatch, test_user_id):
    """If _initial_goal_recompute raises, set_primary_goal still returns ok with a warning."""
    from health_metrics import chat_tools
    monkeypatch.setattr(
        chat_tools, "_initial_goal_recompute",
        AsyncMock(side_effect=RuntimeError("anthropic down")),
    )
    result = await set_primary_goal(
        db_session, test_user_id,
        goal_type="weight", name="g", metric="weight_lbs",
        target_value=185, target_date="2026-08-01",
    )
    assert result["ok"] is True
    assert result["result"]["warning"] == "initial_recompute_failed"


@pytest.mark.asyncio
async def test_update_goal_rejects_no_fields(db_session, test_user_id):
    g = Goal(
        user_id=test_user_id, goal_type="weight", name="g", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g); await db_session.flush()

    r = await update_goal(db_session, test_user_id)
    assert r["ok"] is False
    assert "no fields" in r["error"].lower()
